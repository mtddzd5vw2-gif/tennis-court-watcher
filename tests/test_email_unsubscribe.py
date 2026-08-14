from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATION = (
    ROOT
    / "supabase/migrations/20260814100000_add_email_unsubscribe.sql"
)
SENDER_HELPERS = (
    ROOT / "supabase/functions/dispatch-email-notifications/helpers.ts"
)
SENDER_INDEX = ROOT / "supabase/functions/dispatch-email-notifications/index.ts"
UNSUBSCRIBE_HELPERS = (
    ROOT / "supabase/functions/unsubscribe-email-notifications/helpers.ts"
)
UNSUBSCRIBE_INDEX = (
    ROOT / "supabase/functions/unsubscribe-email-notifications/index.ts"
)
CONFIG = ROOT / "supabase/config.toml"
UI_HTML = ROOT / "account/notifications.html"
UI_SCRIPT = ROOT / "assets/js/notification-rules.js"
RUNBOOK = ROOT / "docs/PHASE3_EMAIL_UNSUBSCRIBE.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def compact(value: str) -> str:
    return " ".join(value.lower().split())


def test_forward_migration_creates_private_backfilled_token_table() -> None:
    sql = compact(read(MIGRATION))

    assert "create table public.notification_email_unsubscribe_tokens" in sql
    for column in (
        "user_id uuid primary key references auth.users(id) on delete cascade",
        "token uuid not null unique default gen_random_uuid()",
        "created_at timestamptz not null default now()",
        "rotated_at timestamptz not null default now()",
    ):
        assert column in sql
    assert (
        "alter table public.notification_email_unsubscribe_tokens enable row level security"
        in sql
    )
    assert (
        "revoke all privileges on table "
        "public.notification_email_unsubscribe_tokens "
        "from public, anon, authenticated, service_role"
    ) in sql
    assert (
        "insert into public.notification_email_unsubscribe_tokens (user_id) "
        "select preference.user_id from public.notification_email_preferences"
        in sql
    )
    assert "after insert on public.notification_email_preferences" in sql


def test_unsubscribe_rpcs_are_service_role_only_and_non_enumerating() -> None:
    sql = compact(read(MIGRATION))

    for signature in (
        "get_email_unsubscribe_token_for_message(uuid)",
        "email_unsubscribe_token_is_valid(uuid)",
        "unsubscribe_email_notifications_by_token(uuid)",
    ):
        assert f"grant execute on function public.{signature} to service_role" in sql
        assert f"public.{signature} from public, anon, authenticated, service_role" in sql
    assert sql.count("security definer") >= 5
    assert sql.count("set search_path = ''") >= 5
    assert "message.channel = 'email'::public.notification_channel" in sql
    assert "preference.is_enabled = true" in sql
    assert "preference.disabled_reason is null" in sql
    assert "jsonb_build_object('outcome', 'processed')" in sql
    assert "preference.disabled_reason =" not in sql


def test_reenable_blocks_inflight_work_and_only_rotates_without_it() -> None:
    sql = compact(read(MIGRATION))

    assert "old.is_enabled = false and new.is_enabled = true" in sql
    assert "new.disabled_reason is null" in sql
    assert "if exists ( select 1 from public.notification_messages" in sql
    assert "'processing'::public.notification_message_status" in sql
    assert "'retry_wait'::public.notification_message_status" in sql
    assert "errcode = '55000'" in sql
    assert "token = pg_catalog.gen_random_uuid()" in sql
    assert "rotated_at = pg_catalog.now()" in sql
    assert "update public.notification_messages" not in sql
    assert "'pending'::public.notification_message_status" not in sql


def test_sender_uses_footer_headers_and_one_exact_serialized_payload() -> None:
    helpers = read(SENDER_HELPERS)
    index = read(SENDER_INDEX)

    assert "メール通知を停止する" in helpers
    assert '"List-Unsubscribe": `<${safeUnsubscribeUrl}>`' in helpers
    assert '"List-Unsubscribe-Post": "List-Unsubscribe=One-Click"' in helpers
    assert '"get_email_unsubscribe_token_for_message"' in index
    assert "const serializedPayload = JSON.stringify(providerPayload);" in index
    assert "hmacPayloadFingerprint(\n      serializedPayload," in index
    assert "body: serializedPayload" in index
    assert '"unsubscribe_token_unavailable"' not in index
    assert '"worker_internal_error"' in index
    assert "console.log" not in helpers
    for forbidden_log in ("unsubscribeUrl)", "unsubscribeToken)", "message.user_id)"):
        assert f"console.log({forbidden_log}" not in index


def test_public_edge_function_supports_confirmation_and_rfc8058_safely() -> None:
    helpers = read(UNSUBSCRIBE_HELPERS)
    index = read(UNSUBSCRIBE_INDEX)
    config = read(CONFIG)

    assert "[functions.unsubscribe-email-notifications]\nverify_jwt = false" in config
    assert 'request.method !== "GET" && request.method !== "POST"' in helpers
    assert 'form.get("List-Unsubscribe") === "One-Click"' in helpers
    assert "request.body.getReader()" in helpers
    assert 'new TextDecoder("utf-8", { fatal: true })' in helpers
    assert "await reader.cancel()" in helpers
    assert "request.text()" not in helpers
    assert "confirmationResponse" in helpers
    assert "minimalSuccessResponse" in helpers
    assert "humanSuccessResponse" in helpers
    assert "methodNotAllowedResponse" in helpers
    assert '"cache-control": "no-store"' in helpers
    assert '"content-security-policy"' in helpers
    assert "console.log" not in index
    assert "request.url" not in index


def test_account_ui_blocks_provider_suppression_without_mutating_reason() -> None:
    html = read(UI_HTML)
    script = read(UI_SCRIPT)

    assert "data-email-notification-toggle" in html
    assert "data-email-notification-guidance" in html
    for reason in (
        "resend_bounced",
        "resend_complained",
        "resend_suppressed",
    ):
        assert reason in script
    assert "配信エラーのためメール通知を停止しています" in script
    assert '.from("notification_email_preferences")' in script
    assert '.update({ is_enabled: nextEnabled })' in script
    assert "update({ disabled_reason" not in script
    assert "emailPreferenceToggle.disabled" in script


def test_runbook_contains_required_guard_and_scope_boundaries() -> None:
    runbook = read(RUNBOOK)

    guard = """select status, count(*)
from public.notification_messages
where status in ('processing','retry_wait')
group by status;"""
    assert guard in runbook
    assert "where provider_first_attempt_at is not null" not in runbook
    assert "0行でなければsenderをdeployしない" in runbook
    assert "`ENABLE_USER_EMAIL_NOTIFICATIONS=false`にして新規claimを停止する" in runbook
    assert "`processing`と`retry_wait`がともに0行" in runbook
    assert "通常workerでdrainしてからmaintenance boundary" in runbook
    assert "rollbackが必要なら、事前の`retry_wait=0`確認" in runbook
    assert "Phase 3.5c" in runbook
    for boundary in (
        "Resend Suppression Listの自動解除",
        "bounce/complaint後の自動再有効化",
        "Phase 4 LINE",
        "production deploy",
        "secret変更",
        "commit",
        "push",
    ):
        assert boundary in runbook
