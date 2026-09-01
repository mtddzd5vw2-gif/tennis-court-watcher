from __future__ import annotations

import re
import subprocess
from pathlib import Path

from bs4 import BeautifulSoup
from pglast import parse_sql


ROOT = Path(__file__).parents[1]
MIGRATION = (
    ROOT
    / "supabase/migrations"
    / "20260901070221_add_anonymous_funnel_counts.sql"
)
COUNTER_TYPE_FIX_MIGRATION = (
    ROOT
    / "supabase/migrations"
    / "20260901073821_fix_anonymous_funnel_counter_type.sql"
)
COUNTER_LEAST_FIX_MIGRATION = (
    ROOT
    / "supabase/migrations"
    / "20260901073954_fix_anonymous_funnel_counter_least_expression.sql"
)
FUNCTION = ROOT / "supabase/functions/record-anonymous-funnel-event"
AUTH_SCRIPT = ROOT / "assets/js/auth-foundation.js"
PRIVACY = ROOT / "legal/privacy.html"
CONFIG = ROOT / "supabase/config.toml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def compact(value: str) -> str:
    return " ".join(re.sub(r"--.*?$", "", value, flags=re.MULTILINE).lower().split())


def test_migration_is_forward_only_and_parses() -> None:
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


def test_daily_counts_store_no_visitor_identifiers() -> None:
    sql = compact(read(MIGRATION))
    assert "create table private.anonymous_funnel_daily_counts" in sql
    assert "primary key (event_date, event_name)" in sql
    assert "event_count bigint" in sql
    assert "enable row level security" in sql
    assert "grant select on table private.anonymous_funnel_daily_counts to service_role" in sql
    for forbidden in (
        "user_id",
        "line_user_id",
        "email",
        "ip_address",
        "user_agent",
        "referrer",
        "visitor_id",
        "session_id",
    ):
        assert forbidden not in sql


def test_increment_rpc_is_service_role_only_bounded_and_atomic() -> None:
    sql = compact(read(MIGRATION))
    assert "security definer" in sql
    assert "set search_path = ''" in sql
    assert "set statement_timeout = '2s'" in sql
    assert "on conflict (event_date, event_name) do update" in sql
    assert "event_count + 1" in sql
    assert "current_event_date - 400" in sql
    assert (
        "revoke all on function public.record_anonymous_funnel_event(text) "
        "from public, anon, authenticated, service_role"
    ) in sql
    assert (
        "grant execute on function public.record_anonymous_funnel_event(text) "
        "to service_role"
    ) in sql
    assert not re.search(
        r"grant execute on function public\.record_anonymous_funnel_event"
        r"\(text\) to (?:anon|authenticated)",
        sql,
    )


def test_applied_counter_type_migration_is_preserved_in_history() -> None:
    sql = compact(read(COUNTER_TYPE_FIX_MIGRATION))
    parse_sql(read(COUNTER_TYPE_FIX_MIGRATION))

    assert "create or replace function public.record_anonymous_funnel_event" in sql
    assert "1000000::pg_catalog.int8" in sql
    assert "notify pgrst, 'reload schema'" in sql
    assert (
        "revoke all on function public.record_anonymous_funnel_event(text) "
        "from public, anon, authenticated, service_role"
    ) in sql
    assert (
        "grant execute on function public.record_anonymous_funnel_event(text) "
        "to service_role"
    ) in sql


def test_counter_uses_the_least_expression_without_schema_qualification() -> None:
    sql = compact(read(COUNTER_LEAST_FIX_MIGRATION))
    parse_sql(read(COUNTER_LEAST_FIX_MIGRATION))

    assert "event_count = least(" in sql
    assert "1000000::pg_catalog.int8" in sql
    assert "pg_catalog.least" not in sql
    assert "notify pgrst, 'reload schema'" in sql
    assert (
        "revoke all on function public.record_anonymous_funnel_event(text) "
        "from public, anon, authenticated, service_role"
    ) in sql
    assert (
        "grant execute on function public.record_anonymous_funnel_event(text) "
        "to service_role"
    ) in sql


def test_edge_function_has_strict_public_ingress_boundary() -> None:
    helpers = read(FUNCTION / "helpers.ts")
    index = read(FUNCTION / "index.ts")
    config = read(CONFIG)

    assert '[functions.record-anonymous-funnel-event]\nverify_jwt = false' in config
    assert 'const ALLOWED_ORIGIN = "https://tenniscourtwatcher.com"' in helpers
    assert "origin !== ALLOWED_ORIGIN" in helpers
    assert "MAX_BODY_BYTES = 128" in helpers
    assert 'contentType.split(";", 1)[0].trim().toLowerCase() !== "text/plain"' in helpers
    assert 'requestUrl.search !== ""' in helpers
    assert "Object.keys(value).length !== 1" in helpers
    assert "serviceRoleKey.trim().length === 0" in helpers
    assert "result.error !== null || result.data !== true" in helpers
    assert 'supabase.rpc("record_anonymous_funnel_event"' in index
    assert "console.log" not in helpers
    assert "console.log" not in index
    assert (FUNCTION / "helpers_test.ts").is_file()


def test_browser_metrics_are_daily_best_effort_and_non_blocking() -> None:
    script = read(AUTH_SCRIPT)
    assert 'const FUNNEL_STORAGE_PREFIX = "tcw.anonymousFunnel"' in script
    assert 'timeZone: "Asia/Tokyo"' in script
    assert 'recordAnonymousFunnelEvent(config, "login_page_view")' in script
    assert 'recordAnonymousFunnelEvent(config, "line_start_click")' in script
    assert 'recordAnonymousFunnelEvent(config, "terms_prompt_view")' in script
    assert "async function setupAccount(client, config)" in script
    assert "await setupAccount(client, config)" in script
    assert '"content-type": "text/plain;charset=UTF-8"' in script
    assert 'credentials: "omit"' in script
    assert "keepalive: true" in script
    assert 'referrerPolicy: "no-referrer"' in script
    assert ".catch(() =>" in script
    for forbidden in (
        "navigator.userAgent",
        "document.referrer",
        "session.user.id",
        "session.user.email",
    ):
        assert forbidden not in script.split(
            "function recordAnonymousFunnelEvent", 1
        )[1].split("function enableLoginForm", 1)[0]


def test_privacy_policy_discloses_only_anonymous_daily_aggregation() -> None:
    privacy = BeautifulSoup(read(PRIVACY), "html.parser").get_text(" ", strip=True)
    assert "版番号: 2026-09-01" in privacy
    assert "LINE認証開始ボタンの押下" in privacy
    assert "個人を識別せず日別に集計" in privacy
    assert "端末識別子を保存しません" in privacy
    assert "最大400日間" in privacy
    assert "日付別の記録済み印" in privacy
