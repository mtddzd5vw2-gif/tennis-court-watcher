from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pglast import parse_sql


ROOT = Path(__file__).parents[1]
MIGRATION = (
    ROOT
    / "supabase/migrations"
    / "20260821073000_add_line_account_link_rpcs.sql"
)
CONFIG = ROOT / "supabase/config.toml"
FUNCTIONS = ROOT / "supabase/functions"
START = FUNCTIONS / "start-line-account-link"
COMPLETE = FUNCTIONS / "complete-line-account-link"
UNLINK = FUNCTIONS / "unlink-line-account"
PGTAP = ROOT / "supabase/tests/database/line_account_link.test.sql"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def compact(value: str) -> str:
    without_comments = re.sub(r"--.*?$", "", value, flags=re.MULTILINE)
    return " ".join(without_comments.lower().split())


def function_body(sql: str, name: str) -> str:
    match = re.search(
        rf"create\s+function\s+public\.{re.escape(name)}\s*\("
        r".*?\)\s*returns\s+text(.*?)\$\$;",
        sql,
        re.DOTALL | re.IGNORECASE,
    )
    assert match, f"missing function: {name}"
    return compact(match.group(1))


def test_line_link_rpc_migration_is_forward_only_and_parses() -> None:
    assert MIGRATION.is_file()
    parse_sql(read(MIGRATION))

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


def test_line_link_rpcs_are_service_role_only_security_invokers() -> None:
    sql = compact(read(MIGRATION))
    signatures = (
        "public.create_line_link_session( uuid, text, text, timestamptz )",
        "public.complete_line_account_link( text, text, text, boolean )",
        "public.unlink_line_account(uuid)",
    )
    for name in (
        "create_line_link_session",
        "complete_line_account_link",
        "unlink_line_account",
    ):
        body = function_body(read(MIGRATION), name)
        assert "security invoker" in body
        assert "set search_path = ''" in body
        assert "security definer" not in body

    for signature in signatures:
        normalized = compact(signature)
        assert f"revoke all on function {normalized}" in sql
        assert f"grant execute on function {normalized}" in sql
    assert not re.search(
        r"grant execute on function public\.(?:create|complete|unlink)_line.*?"
        r"to (?:anon|authenticated)",
        sql,
    )


def test_session_creation_checks_membership_and_replaces_old_state() -> None:
    body = function_body(read(MIGRATION), "create_line_link_session")
    assert "p_state_hash !~ '^[0-9a-f]{64}$'" in body
    assert "p_nonce_hash !~ '^[0-9a-f]{64}$'" in body
    assert "pg_catalog.decode(p_state_hash, 'hex')" in body
    assert "pg_catalog.decode(p_nonce_hash, 'hex')" in body
    assert "p_expires_at > current_time + interval '10 minutes'" in body
    assert "profile.membership_status" in body
    assert "delete from public.line_link_sessions" in body
    assert body.index("delete from public.line_link_sessions") < body.index(
        "insert into public.line_link_sessions"
    )


def test_completion_consumes_state_and_links_one_to_one_atomically() -> None:
    body = function_body(read(MIGRATION), "complete_line_account_link")
    assert "session.state_hash = decoded_state_hash" in body
    assert "session.nonce_hash = decoded_nonce_hash" in body
    assert "session.consumed_at is null" in body
    assert "session.expires_at >= current_time" in body
    assert "for update" in body
    assert body.index("set consumed_at = current_time") < body.index(
        "insert into public.line_account_links"
    )
    assert "on conflict (user_id) do update" in body
    assert "when unique_violation then return 'line_conflict'" in body
    assert "existing_line_user_id <> p_line_user_id" in body
    assert "return 'member_conflict'" in body
    assert "when p_is_friend then 'active'" in body
    assert "else 'blocked'" in body
    for forbidden in (
        "access_token",
        "id_token",
        "refresh_token",
        "authorization_code",
    ):
        assert forbidden not in compact(read(MIGRATION))


def test_unlink_is_active_member_only_and_idempotent() -> None:
    body = function_body(read(MIGRATION), "unlink_line_account")
    assert "profile.membership_status" in body
    assert "status = 'unlinked'::public.line_account_link_status" in body
    assert "unlinked_at = pg_catalog.clock_timestamp()" in body
    assert "delete from public.line_link_sessions" in body
    assert "return 'not_linked'" in body
    assert "return 'unlinked'" in body


def test_edge_function_gateway_boundaries_are_explicit() -> None:
    config = read(CONFIG)
    assert "[functions.start-line-account-link]\nverify_jwt = true" in config
    assert "[functions.complete-line-account-link]\nverify_jwt = false" in config
    assert "[functions.unlink-line-account]\nverify_jwt = true" in config


def test_start_and_unlink_identity_come_only_from_verified_jwt() -> None:
    start_index = read(START / "index.ts")
    unlink_index = read(UNLINK / "index.ts")
    start_helpers = read(START / "helpers.ts")
    unlink_helpers = read(UNLINK / "helpers.ts")

    for index in (start_index, unlink_index):
        assert "admin.auth.getUser(input.accessToken)" in index
        assert "userResult.data.user.id" in index
        assert 'Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")' in index or (
            "getEnv: (name) => Deno.env.get(name)" in index
        )
    assert "user_id" not in start_helpers
    assert "user_id" not in unlink_helpers
    assert 'record.confirmation === CONFIRMATION_VALUE' in unlink_helpers


def test_start_uses_256_bit_state_nonce_and_no_email_scope() -> None:
    helpers = read(START / "helpers.ts")
    assert "randomBytes(32)" in helpers
    assert 'crypto.subtle.digest(\n    "SHA-256"' in helpers
    assert 'url.searchParams.set("state", input.state)' in helpers
    assert 'url.searchParams.set("nonce", input.nonce)' in helpers
    assert 'url.searchParams.set("scope", "openid profile")' in helpers
    assert 'url.searchParams.set("bot_prompt", "aggressive")' in helpers
    assert 'url.searchParams.set("scope", "openid profile email")' not in helpers
    assert 'authorization_url: authorizationUrl' in helpers


def test_callback_verifies_line_identity_and_never_logs_oauth_material() -> None:
    helpers = read(COMPLETE / "helpers.ts")
    line_api = read(COMPLETE / "line-api.ts")

    assert "https://api.line.me/oauth2/v2.1/token" in line_api
    assert "https://api.line.me/oauth2/v2.1/verify" in line_api
    assert "https://api.line.me/friendship/v1/status" in line_api
    assert "https://api.line.me/oauth2/v2.1/revoke" in line_api
    assert 'value.iss === "https://access.line.me"' in line_api
    assert "value.aud === channelId" in line_api
    assert "value.exp > nowSeconds" in line_api
    assert "/^[0-9a-f]{64}$/.test(value.nonce)" in line_api
    assert "sha256Bytea(verified.nonce)" in line_api
    assert "stateHash: await sha256Bytea(input.state)" in line_api
    assert line_api.count('redirect: "error"') == 4
    assert 'log(JSON.stringify({ outcome }))' in helpers
    assert "log(request" not in helpers
    assert "log(url" not in helpers
    assert "log(input" not in line_api
    assert '"referrer-policy": "no-referrer"' in helpers
    assert "LINE_LOGIN_CHANNEL_SECRET" not in helpers


def test_all_line_edge_function_unit_tests_are_present() -> None:
    expected = (
        START / "helpers_test.ts",
        COMPLETE / "helpers_test.ts",
        COMPLETE / "line-api_test.ts",
        UNLINK / "helpers_test.ts",
    )
    assert all(path.is_file() for path in expected)
    assert PGTAP.is_file()
    assert "select extensions.plan(32);" in read(PGTAP)
    parse_sql(read(PGTAP))
