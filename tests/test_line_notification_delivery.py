from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pglast import parse_sql


ROOT = Path(__file__).parents[1]
MIGRATION = (
    ROOT
    / "supabase/migrations/20260821095256_add_line_notification_delivery.sql"
)
BETA_MIGRATION = (
    ROOT
    / "supabase/migrations/20260828001723_add_line_notification_rollout_allowlist.sql"
)
CONFIG = ROOT / "supabase/config.toml"
WEBHOOK = ROOT / "supabase/functions/line-messaging-webhook"
WORKER = ROOT / "supabase/functions/dispatch-line-notifications"
PGTAP = ROOT / "supabase/tests/database/line_notification_delivery.test.sql"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def compact(value: str) -> str:
    return " ".join(re.sub(r"--.*?$", "", value, flags=re.MULTILINE).split())


def function_definition(name: str, migration: Path = MIGRATION) -> str:
    match = re.search(
        rf"create(?: or replace)? function public\.{re.escape(name)}\b"
        rf".*?\$\$;(?:\s|$)",
        read(migration).lower(),
        flags=re.DOTALL,
    )
    assert match, f"missing function {name}"
    return compact(match.group(0))


def test_line_delivery_is_one_forward_migration_and_parses() -> None:
    assert MIGRATION.is_file()
    assert BETA_MIGRATION.is_file()
    parse_sql(read(MIGRATION))
    parse_sql(read(BETA_MIGRATION))
    changed_existing = []
    for path in (ROOT / "supabase/migrations").glob("*.sql"):
        if path.name >= MIGRATION.name:
            continue
        result = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", str(path.relative_to(ROOT))],
            cwd=ROOT,
            check=False,
        )
        if result.returncode != 0:
            changed_existing.append(path.name)
    assert changed_existing == []


def test_beta_allowlist_is_private_capped_and_service_role_managed() -> None:
    sql = compact(read(BETA_MIGRATION).lower())
    assert "create schema if not exists private" in sql
    assert "create table private.line_notification_beta_allowlist" in sql
    assert "user_id uuid primary key" in sql
    assert "references public.profiles(id) on delete cascade" in sql
    assert "enable row level security" in sql
    assert "force row level security" in sql
    assert "v_max_allowlisted_members constant pg_catalog.int4 := 20" in sql
    assert "security definer set search_path = ''" in function_definition(
        "replace_line_notification_beta_allowlist", BETA_MIGRATION
    )
    assert "grant select on table private.line_notification_beta_allowlist to service_role" in sql
    assert "grant execute on function public.replace_line_notification_beta_allowlist(uuid[]) to service_role" in sql
    assert "grant insert on table private.line_notification_beta_allowlist" not in sql


def test_beta_rollout_is_rechecked_at_all_three_delivery_boundaries() -> None:
    enqueue = function_definition(
        "enqueue_line_notification_candidates", BETA_MIGRATION
    )
    claim = function_definition("claim_line_messages", BETA_MIGRATION)
    authorization = function_definition(
        "authorize_line_message_send", BETA_MIGRATION
    )

    for body in (enqueue, claim, authorization):
        assert "p_use_allowlist boolean" in body
        assert "private.line_notification_beta_allowlist" in body
        assert "p_allow_all" in body
        assert "p_canary_user_id" in body
        assert "pg_advisory_xact_lock_shared(20260828001723)" in body
    assert "v_rollout_mode_count > 1" in enqueue
    assert "v_rollout_mode_count <> 1" in claim
    assert "v_rollout_mode_count <> 1" in authorization
    assert "where not p_shadow_mode" in enqueue
    assert "if not coalesce(" in authorization
    assert "return 'cancelled'" in authorization


def test_deprecated_rollout_rpc_signatures_are_revoked() -> None:
    sql = compact(read(BETA_MIGRATION).lower())
    assert "revoke all on function public.enqueue_line_notification_candidates( jsonb, boolean, uuid, boolean ) from public, anon, authenticated, service_role" in sql
    assert "revoke all on function public.claim_line_messages(integer, uuid, boolean) from public, anon, authenticated, service_role" in sql
    assert "revoke all on function public.authorize_line_message_send( uuid, timestamptz, text, text, uuid, boolean ) from public, anon, authenticated, service_role" in sql


def test_webhook_ledger_is_private_minimal_and_idempotent() -> None:
    sql = compact(read(MIGRATION).lower())
    assert "create table public.line_webhook_events" in sql
    assert "webhook_event_id text primary key" in sql
    assert "event_type text not null" in sql
    assert "occurred_at timestamptz not null" in sql
    table = sql.split("create table public.line_webhook_events", 1)[1].split(
        ");", 1
    )[0]
    assert "line_user_id" not in table
    assert "raw_payload" not in table
    assert "enable row level security" in sql
    assert "on conflict (webhook_event_id) do nothing" in function_definition(
        "record_line_webhook_events"
    )


def test_webhook_updates_only_current_link_state_in_event_time_order() -> None:
    body = function_definition("record_line_webhook_events")
    assert "security invoker" in body
    assert "link.line_user_id = v_line_user_id" in body
    assert "link.status <> 'unlinked'::public.line_account_link_status" in body
    assert "link.last_webhook_at <= v_occurred_at" in body
    assert "when 'follow' then 'active'" in body
    assert "else 'blocked'" in body


def test_line_enqueue_is_shadowable_canary_gated_and_rule_aligned() -> None:
    body = function_definition("enqueue_line_notification_candidates")
    assert "security invoker" in body
    assert "not p_shadow_mode and not p_allow_all and p_canary_user_id is null" in body
    assert "where not p_shadow_mode" in body
    assert "candidate.user_id = p_canary_user_id" in body
    assert "profile.membership_status = 'active'" in body
    assert "link.status = 'active'" in body
    assert "notification_rule.is_enabled = true" in body
    assert "on conflict (user_id, channel, slot_id) do nothing" in body
    assert "group by delivery_item.user_id, delivery_item.channel" in body


def test_fixed_canary_uses_shared_queue_without_fake_availability() -> None:
    body = function_definition("enqueue_line_canary_test")
    assert "security invoker" in body
    assert "insert into public.notification_messages" in body
    assert "insert into public.notification_delivery_items" not in body
    assert "line_test_text" in body
    assert "【テスト通知】鹿児島テニス空き情報 line通知の動作確認です。" in body
    assert "on conflict (id) do nothing" in body


def test_email_and_line_workers_have_hard_channel_boundaries() -> None:
    email_claim = function_definition("claim_email_messages")
    line_claim = function_definition("claim_line_messages")
    assert email_claim.count("message.channel = 'email'") >= 3
    assert "message.channel = 'line'" not in email_claim
    assert line_claim.count("message.channel = 'line'") >= 3
    assert "message.channel = 'email'" not in line_claim
    assert "link.line_user_id" in line_claim
    assert "p_allow_all or message.user_id = p_canary_user_id" in line_claim
    assert "message.line_test_text" in line_claim
    assert "for update of message skip locked" in line_claim


def test_line_authorization_rechecks_exact_recipient_and_lease() -> None:
    authorization = function_definition("authorize_line_message_send")
    assert "message.locked_until = p_locked_until" in authorization
    assert "message.locked_until > pg_catalog.now()" in authorization
    assert "link.line_user_id = p_line_user_id" in authorization
    assert "link.status = 'active'" in authorization
    assert "v_message.user_id <> p_canary_user_id" in authorization
    assert "provider_payload_changed" in authorization
    assert "interval '23 hours'" in authorization


def test_line_worker_retry_and_acceptance_are_normalized() -> None:
    accepted = function_definition("record_line_message_accepted")
    failure = function_definition("record_line_message_failure")
    assert "p_provider_status <> all (array['accepted', 'accepted_retry'])" in accepted
    assert "message.channel = 'line'" in accepted
    for code in (
        "line_network_error",
        "line_server_error",
        "line_rate_limited",
        "line_invalid_access_token",
        "line_invalid_recipient_or_payload",
    ):
        assert code in failure
    assert "when 1 then 60" in failure
    assert "when 2 then 120" in failure
    assert "when 3 then 300" in failure
    assert "else 900" in failure
    retryable_codes = failure.split("v_retryable :=", 1)[1].split(");", 1)[0]
    assert "line_rate_limited" not in retryable_codes
    assert "line_quota_exceeded" not in retryable_codes
    assert "worker_internal_error" not in retryable_codes
    assert "line_unexpected_response" not in retryable_codes

    helpers = read(WORKER / "helpers.ts")
    assert 'status === 429' in helpers
    assert 'retryable: false, errorCode: "line_quota_exceeded"' in helpers


def test_line_rpcs_are_service_role_only() -> None:
    sql = compact(read(MIGRATION).lower())
    for name in (
        "record_line_webhook_events",
        "enqueue_line_notification_candidates",
        "enqueue_line_canary_test",
        "claim_line_messages",
        "authorize_line_message_send",
        "record_line_message_accepted",
        "record_line_message_failure",
        "cancel_line_notification_backlog",
        "cleanup_line_webhook_events",
    ):
        assert f"grant execute on function public.{name}" in sql
    assert not re.search(
        r"grant execute on function public\.(?:record|enqueue|claim|authorize)_line"
        r".*?to (?:anon|authenticated)",
        sql,
    )


def test_webhook_verifies_raw_signature_before_json_and_never_logs_identity() -> None:
    helpers = read(WEBHOOK / "helpers.ts")
    index = read(WEBHOOK / "index.ts")
    config = read(CONFIG)
    assert helpers.index("verifyLineWebhookSignature(") < helpers.index("JSON.parse(")
    assert 'request.headers.get("x-line-signature")' in helpers
    assert 'crypto.subtle.importKey(\n    "raw"' in helpers
    assert 'name: "HMAC", hash: "SHA-256"' in helpers
    assert 'supabase.rpc("record_line_webhook_events"' in index
    assert 'Deno.env.get(name)' in index
    assert '"x-line-signature": signature' in index
    assert "forwardedBody.set(rawBody)" in index
    assert "body: forwardedBody.buffer" in index
    assert 'redirect: "follow"' in index
    assert 'LINE_WEBHOOK_BRIDGE_ENABLED' in helpers
    assert 'LINE_LEGACY_WEBHOOK_URL' in helpers
    assert 'url.hostname !== "script.google.com"' in helpers
    assert helpers.index(
        "result = await dependencies.recordEvents("
    ) < helpers.index(
        "forwardLegacyWebhook!("
    )
    assert "console." not in helpers
    assert "console." not in index
    assert "[functions.line-messaging-webhook]\nverify_jwt = false" in config


def test_worker_checks_quota_before_claim_and_uses_retry_key() -> None:
    source = read(WORKER / "index.ts")
    helpers = read(WORKER / "helpers.ts")
    config = read(CONFIG)
    quota = source.index("fetchQuotaConsumption(channelAccessToken)")
    claim = source.index('"claim_line_messages"')
    send = source.index("fetch(LINE_PUSH_ENDPOINT")
    assert quota < claim < send
    assert "Math.min(batchSize, remainingQuota)" in source
    assert "limit <= 180" in source
    assert 'Deno.env.get("ENABLE_USER_LINE_NOTIFICATIONS") !== "true"' in source
    assert 'Deno.env.get("LINE_NOTIFICATION_CANARY_USER_ID")' in source
    assert 'Deno.env.get("LINE_NOTIFICATION_USE_ALLOWLIST")' in source
    assert 'Deno.env.get("LINE_NOTIFICATION_ALLOW_ALL")' in source
    assert "p_canary_user_id: rolloutControls.canaryUserId" in source
    assert "p_use_allowlist: rolloutControls.useAllowlist" in source
    assert "p_allow_all: rolloutControls.allowAll" in source
    assert "message.test_text ?? renderLineMessage(message.items)" in source
    assert '"x-line-retry-key": deterministicLineRetryKey(' in source
    assert 'response.headers.get("x-line-accepted-request-id")' in source
    assert "MAX_LINE_TEXT_CHARACTERS = 4800" in helpers
    assert "[functions.dispatch-line-notifications]\nverify_jwt = false" in config
    assert "console." not in source


def test_line_edge_function_unit_tests_are_present() -> None:
    assert (WEBHOOK / "helpers_test.ts").is_file()
    assert (WORKER / "helpers_test.ts").is_file()
    assert PGTAP.is_file()
    assert "select extensions.plan(61);" in read(PGTAP)
    parse_sql(read(PGTAP))
