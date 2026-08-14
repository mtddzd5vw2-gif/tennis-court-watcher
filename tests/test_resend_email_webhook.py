from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATION_PATH = (
    ROOT
    / "supabase/migrations"
    / "20260814000000_add_resend_delivery_feedback.sql"
)
QUEUE_MIGRATION_PATH = (
    ROOT
    / "supabase/migrations"
    / "20260807140000_create_email_notification_queue.sql"
)
WORKER_MIGRATION_PATH = (
    ROOT
    / "supabase/migrations"
    / "20260807150000_add_email_delivery_worker_rpcs.sql"
)
WEBHOOK_DIR = ROOT / "supabase/functions/resend-email-webhook"
WEBHOOK_HELPERS_PATH = WEBHOOK_DIR / "helpers.ts"
WEBHOOK_INDEX_PATH = WEBHOOK_DIR / "index.ts"
WEBHOOK_TEST_PATH = WEBHOOK_DIR / "helpers_test.ts"
DISPATCH_HELPERS_PATH = (
    ROOT / "supabase/functions/dispatch-email-notifications/helpers.ts"
)
DISPATCH_INDEX_PATH = (
    ROOT / "supabase/functions/dispatch-email-notifications/index.ts"
)
CONFIG_PATH = ROOT / "supabase/config.toml"
PGTAP_PATH = ROOT / "supabase/tests/database/resend_email_webhook.test.sql"


def compact(value: str) -> str:
    without_comments = re.sub(r"--.*?$", "", value, flags=re.MULTILINE)
    return " ".join(without_comments.split())


def migration_sql() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8").lower()


def rpc_definition() -> str:
    match = re.search(
        r"create function public\.record_resend_email_event\b.*?\$\$;",
        migration_sql(),
        flags=re.DOTALL,
    )
    assert match
    return compact(match.group(0))


def test_phase_35a_is_forward_only() -> None:
    assert MIGRATION_PATH.is_file()
    result = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "HEAD",
            "--",
            str(QUEUE_MIGRATION_PATH.relative_to(ROOT)),
            str(WORKER_MIGRATION_PATH.relative_to(ROOT)),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == ""


def test_rpc_is_strict_service_role_only_and_idempotent() -> None:
    sql = compact(migration_sql())
    rpc = rpc_definition()
    signature = (
        "public.record_resend_email_event( text, text, text, timestamptz, "
        "text, text )"
    )

    assert "security definer set search_path = '' as $$" in rpc
    assert "pg_advisory_xact_lock" in rpc
    assert "provider_event.provider_event_id = p_provider_event_id" in rpc
    assert "on conflict (provider, provider_event_id) do nothing" in rpc
    assert "'outcome', 'duplicate'" in rpc
    assert f"revoke execute on function {signature}" in sql
    assert "from public, anon, authenticated;" in sql
    assert f"grant execute on function {signature}" in sql
    assert "to service_role;" in sql


def test_rpc_validates_and_allowlists_only_delivery_events() -> None:
    rpc = rpc_definition()
    expected_events = {
        "email.sent",
        "email.delivery_delayed",
        "email.delivered",
        "email.failed",
        "email.bounced",
        "email.complained",
        "email.suppressed",
    }

    for event_type in expected_events:
        assert f"'{event_type}'" in rpc
    assert "email.opened" not in rpc
    assert "email.clicked" not in rpc
    assert "p_occurred_at > pg_catalog.statement_timestamp()" in rpc
    assert "interval '5 minutes'" in rpc
    assert "not pg_catalog.isfinite(p_occurred_at)" in rpc


def test_rpc_correlation_is_provider_first_and_tag_binding_is_guarded() -> None:
    rpc = rpc_definition()
    direct = rpc.index("message.provider_message_id = p_provider_message_id")
    fallback = rpc.index("p_source_tag = 'user_notification'")

    assert direct < fallback
    assert "message.id = p_message_id_tag::pg_catalog.uuid" in rpc
    assert "v_message.provider_first_attempt_at is null" in rpc
    assert "v_message.provider_payload_fingerprint is null" in rpc
    assert "set provider_message_id = p_provider_message_id" in rpc
    assert "'outcome', 'correlation_conflict'" in rpc
    assert "'outcome', 'ignored_unmatched'" in rpc


def test_rpc_stores_only_normalized_events_and_uses_provider_time_order() -> None:
    rpc = rpc_definition()

    for column in (
        "provider_event_id",
        "provider_message_id",
        "event_type",
        "provider_status",
        "occurred_at",
    ):
        assert column in rpc
    assert "recipient_email" not in rpc
    assert "subject" not in rpc
    assert "raw_payload" not in rpc
    assert "provider_event.occurred_at desc" in rpc
    assert "when 'email.complained' then 70" in rpc
    assert "when 'email.sent' then 10" in rpc


def test_rpc_maps_states_clears_leases_and_controls_preferences() -> None:
    rpc = rpc_definition()

    for expected in (
        "'accepted'::public.notification_message_status",
        "'delivered'::public.notification_message_status",
        "'failed_permanent'::public.notification_message_status",
        "'bounced'::public.notification_message_status",
        "'complained'::public.notification_message_status",
        "'suppressed'::public.notification_message_status",
        "'resend_delivery_failed'",
        "'resend_bounced'",
        "'resend_complained'",
        "'resend_suppressed'",
        "locked_at = null",
        "locked_until = null",
    ):
        assert expected in rpc
    assert "update public.notification_email_preferences" in rpc
    preference_update = rpc.split(
        "update public.notification_email_preferences", 1
    )[1]
    assert "is_enabled = false" in preference_update
    assert "set is_enabled = true" not in preference_update


def test_dispatch_payload_has_exact_tags_and_fingerprints_exact_json() -> None:
    helpers = DISPATCH_HELPERS_PATH.read_text(encoding="utf-8")
    index = DISPATCH_INDEX_PATH.read_text(encoding="utf-8")

    assert '{ name: "tcw_source", value: "user_notification" }' in helpers
    assert '{ name: "tcw_message_id", value: messageId.toLowerCase() }' in helpers
    build_call = index.split("buildResendPayload(", 1)[1].split(");", 1)[0]
    assert "message.message_id" in build_call
    serialized = index.index("const serializedPayload = JSON.stringify")
    fingerprint = index.index("hmacPayloadFingerprint(")
    fetch_body = index.index("body: serializedPayload")
    assert serialized < fingerprint < fetch_body


def test_webhook_uses_pinned_svix_raw_body_and_minimal_payload_fields() -> None:
    helpers = WEBHOOK_HELPERS_PATH.read_text(encoding="utf-8")
    index = WEBHOOK_INDEX_PATH.read_text(encoding="utf-8")

    assert 'from "npm:svix@1.99.1"' in helpers
    assert "readRawBody(request, MAX_WEBHOOK_BODY_BYTES)" in helpers
    assert "verifySignature(\n        rawBody," in helpers
    assert "MAX_WEBHOOK_BODY_BYTES = 64 * 1024" in helpers
    for expected in (
        "envelope.type",
        "envelope.created_at",
        "data.email_id",
        'readTag(tags, "tcw_source")',
        'readTag(tags, "tcw_message_id")',
    ):
        assert expected in helpers
    assert 'supabase.rpc("record_resend_email_event", args)' in index


def test_webhook_enforces_transport_errors_and_aggregate_only_output() -> None:
    helpers = WEBHOOK_HELPERS_PATH.read_text(encoding="utf-8")
    tests = WEBHOOK_TEST_PATH.read_text(encoding="utf-8")

    for expected in (
        'request.method !== "POST"',
        'request.headers.has("origin")',
        'request.headers.get("svix-id")',
        'request.headers.get("svix-timestamp")',
        'request.headers.get("svix-signature")',
        'dependencies.getEnv("RESEND_WEBHOOK_SIGNING_SECRET")',
        'finish(401, "unauthorized")',
        'finish(503, "configuration_error")',
        'finish(502, "retryable_error"',
        'finish(200, "ignored_unsupported"',
    ):
        assert expected in helpers
    assert "rawBody" not in helpers.split("const aggregate:", 1)[1].split(
        "return new Response", 1
    )[0]
    assert "responses and logs contain aggregates but no PII" in tests
    assert "console.error" not in helpers


def test_webhook_config_and_required_test_suites_are_present() -> None:
    config = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    pg_tap = PGTAP_PATH.read_text(encoding="utf-8")
    deno_tests = WEBHOOK_TEST_PATH.read_text(encoding="utf-8")

    assert config["functions"]["resend-email-webhook"]["verify_jwt"] is False
    assert "select extensions.plan(50);" in pg_tap
    for scenario in (
        "duplicate svix-id",
        "does not regress",
        "tag-race correlation",
        "correlation conflict",
        "external unmatched",
        "service role can execute",
    ):
        assert scenario in pg_tap
    assert deno_tests.count('test("') >= 14
