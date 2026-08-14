from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATION_PATH = (
    ROOT
    / "supabase/migrations"
    / "20260807150000_add_email_delivery_worker_rpcs.sql"
)
QUEUE_MIGRATION_PATH = (
    ROOT
    / "supabase/migrations"
    / "20260807140000_create_email_notification_queue.sql"
)
FUNCTION_DIR = (
    ROOT / "supabase/functions" / "dispatch-email-notifications"
)
INDEX_PATH = FUNCTION_DIR / "index.ts"
HELPERS_PATH = FUNCTION_DIR / "helpers.ts"
HELPERS_TEST_PATH = FUNCTION_DIR / "helpers_test.ts"
CONFIG_PATH = ROOT / "supabase/config.toml"


def compact(value: str) -> str:
    without_comments = re.sub(r"--.*?$", "", value, flags=re.MULTILINE)
    return " ".join(without_comments.split())


def migration_sql() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8").lower()


def function_definition(sql: str, function_name: str) -> str:
    match = re.search(
        rf"create(?: or replace)? function public\.{function_name}\b"
        rf".*?\$\$;(?:\s|$)",
        sql,
        flags=re.DOTALL,
    )
    assert match, f"missing function {function_name}"
    return compact(match.group(0))


def test_phase_32_is_a_forward_migration_and_phase_31_is_unchanged() -> None:
    assert MIGRATION_PATH.is_file()
    result = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "HEAD",
            "--",
            str(QUEUE_MIGRATION_PATH.relative_to(ROOT)),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == ""


def test_provider_window_columns_store_no_recipient_or_payload() -> None:
    sql = compact(migration_sql())

    assert "add column provider_first_attempt_at timestamptz" in sql
    assert "add column provider_payload_fingerprint text" in sql
    assert "notification_messages_provider_attempt_pair" in sql
    assert "provider_payload_fingerprint ~ '^[0-9a-f]{64}$'" in sql
    assert "recipient_email" not in sql
    assert "provider_payload json" not in sql


def test_claim_enforces_attempt_and_provider_window_bounds() -> None:
    claim = function_definition(migration_sql(), "claim_email_messages")

    assert "v_max_attempts constant pg_catalog.int4 := 5" in claim
    assert "interval '23 hours'" in claim
    assert "for update of message skip locked" in claim
    assert "message.attempt_count >= v_max_attempts" in claim
    assert "message.attempt_count < v_max_attempts" in claim
    assert "'attempt_limit_exceeded'" in claim
    assert "'idempotency_window_expired'" in claim
    assert "locked_until = pg_catalog.now() + interval '5 minutes'" in claim


def test_authorization_rechecks_lease_membership_and_preference() -> None:
    authorization = function_definition(
        migration_sql(),
        "authorize_email_message_send",
    )

    assert "message.status = 'processing'::public.notification_message_status" in (
        authorization
    )
    assert "message.locked_until = p_locked_until" in authorization
    assert "message.locked_until > pg_catalog.now()" in authorization
    assert "for update" in authorization
    assert "public.profiles as profile" in authorization
    assert "profile.membership_status = 'active'::public.membership_status" in (
        authorization
    )
    assert "public.notification_email_preferences as preference" in authorization
    assert "preference.is_enabled = true" in authorization
    assert "preference.disabled_reason is null" in authorization
    assert "status = 'cancelled'::public.notification_message_status" in (
        authorization
    )
    assert "provider_payload_changed" in authorization
    assert "return 'authorized'" in authorization


def test_accepted_result_is_lease_safe_and_normalized() -> None:
    accepted = function_definition(
        migration_sql(),
        "record_email_message_accepted",
    )

    for expected in (
        "status = 'accepted'::public.notification_message_status",
        "provider_message_id = p_provider_message_id",
        "provider_status = 'accepted'",
        "accepted_at = pg_catalog.now()",
        "locked_at = null",
        "locked_until = null",
        "message.locked_until = p_locked_until",
        "message.locked_until > pg_catalog.now()",
    ):
        assert expected in accepted
    assert "returns boolean" in accepted


def test_failure_result_allowlists_codes_and_bounds_retry() -> None:
    failure = function_definition(
        migration_sql(),
        "record_email_message_failure",
    )

    for retryable_code in (
        "auth_lookup_error",
        "resend_network_error",
        "resend_server_error",
        "resend_rate_limited",
        "resend_concurrent_request",
    ):
        assert retryable_code in failure
    assert "v_message.attempt_count < v_max_attempts" in failure
    assert "when 1 then 60" in failure
    assert "when 2 then 120" in failure
    assert "when 3 then 300" in failure
    assert "else 900" in failure
    assert "pg_catalog.random() * 31" in failure
    assert "status = 'retry_wait'::public.notification_message_status" in failure
    assert (
        "status = 'failed_permanent'::public.notification_message_status"
        in failure
    )
    assert "last_error_code = p_error_code" in failure
    assert "last_error_message = null" in failure
    assert "provider response" not in failure


def test_worker_rpcs_are_service_role_only_security_definers() -> None:
    sql = compact(migration_sql())
    signatures = (
        "public.authorize_email_message_send( uuid, timestamptz, text )",
        "public.record_email_message_accepted( uuid, timestamptz, text )",
        "public.record_email_message_failure( uuid, timestamptz, text )",
    )

    for function_name in (
        "claim_email_messages",
        "authorize_email_message_send",
        "record_email_message_accepted",
        "record_email_message_failure",
    ):
        definition = function_definition(migration_sql(), function_name)
        assert "security definer set search_path = '' as $$" in definition
    for signature in signatures:
        assert f"revoke execute on function {signature}" in sql
        assert f"grant execute on function {signature} to service_role;" in sql


def test_edge_function_is_fail_closed_and_service_to_service_only() -> None:
    source = INDEX_PATH.read_text(encoding="utf-8")
    config = CONFIG_PATH.read_text(encoding="utf-8")

    assert 'request.method !== "POST"' in source
    assert 'request.headers.has("origin")' in source
    assert 'Deno.env.get("EMAIL_DELIVERY_WORKER_SECRET")' in source
    assert "secretsEqual(suppliedSecret, workerSecret)" in source
    assert (
        'Deno.env.get("ENABLE_USER_EMAIL_NOTIFICATIONS") !== "true"'
        in source
    )
    flag_check = source.index("ENABLE_USER_EMAIL_NOTIFICATIONS")
    claim_call = source.index('"claim_email_messages"')
    assert flag_check < claim_call
    assert "const MAX_BATCH_SIZE = 10" in source
    assert "for (const candidate of claimedMessages)" in source
    assert "[functions.dispatch-email-notifications]" in config
    section = config.split("[functions.dispatch-email-notifications]", 1)[1]
    assert "verify_jwt = false" in section.split("[", 1)[0]


def test_worker_resolves_auth_recipient_only_immediately_before_send() -> None:
    source = INDEX_PATH.read_text(encoding="utf-8")

    assert "getUserById(message.user_id)" in source
    auth_lookup = source.index("getUserById(message.user_id)")
    authorization = source.index('"authorize_email_message_send"')
    resend_fetch = source.index("fetch(RESEND_EMAIL_ENDPOINT")
    assert auth_lookup < authorization < resend_fetch
    assert "recipient_email" not in migration_sql()
    assert "JSON.stringify(metrics)" in source
    assert "new Response(serializedPayload" not in source


def test_resend_request_has_required_secrets_headers_and_exact_body_reuse() -> None:
    source = INDEX_PATH.read_text(encoding="utf-8")

    assert 'Deno.env.get("RESEND_API_KEY")' in source
    assert 'Deno.env.get("RESEND_FROM_EMAIL")' in source
    assert 'Deno.env.get("EMAIL_DELIVERY_PAYLOAD_HMAC_KEY")' in source
    assert 'const RESEND_EMAIL_ENDPOINT = "https://api.resend.com/emails"' in source
    for expected in (
        'authorization: `Bearer ${resendApiKey}`',
        '"content-type": "application/json"',
        '"user-agent": USER_AGENT',
        '"idempotency-key": deterministicIdempotencyKey(',
        "body: serializedPayload",
    ):
        assert expected in source
    assert source.count("serializedPayload") >= 3
    assert "console.log(JSON.stringify(metrics))" in source
    assert source.count("console.") == 1


def test_pure_helpers_and_unit_tests_cover_required_cases() -> None:
    helpers = HELPERS_PATH.read_text(encoding="utf-8")
    helper_tests = HELPERS_TEST_PATH.read_text(encoding="utf-8")

    for helper in (
        "escapeHtml",
        "validHttpUrl",
        "classifyResendError",
        "deterministicIdempotencyKey",
    ):
        assert f"function {helper}" in helpers
        assert helper in helper_tests
    for provider_code in (
        "rate_limit_exceeded",
        "concurrent_idempotent_requests",
        "invalid_idempotent_request",
        "invalid_api_key",
        "invalid_from_address",
        "validation_error",
    ):
        assert provider_code in helpers
        assert provider_code in helper_tests
    assert "escapeHtml(facilityName)" in helpers
    assert "escapeHtml(courtName)" in helpers
    assert "escapeHtml(reservationUrl)" in helpers


def test_public_availability_data_is_untouched() -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    changed_paths = {
        line[3:].strip('"').replace("\\", "/")
        for line in result.stdout.splitlines()
    }
    assert "data/availability.json" not in changed_paths
