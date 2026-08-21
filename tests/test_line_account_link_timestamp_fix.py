from __future__ import annotations

import re
from pathlib import Path

from pglast import parse_sql


ROOT = Path(__file__).parents[1]
MIGRATION = (
    ROOT
    / "supabase/migrations"
    / "20260821093000_fix_line_account_link_timestamp_name.sql"
)


def compact(value: str) -> str:
    without_comments = re.sub(r"--.*?$", "", value, flags=re.MULTILINE)
    return " ".join(without_comments.lower().split())


def replacement_body(sql: str, function: str) -> str:
    match = re.search(
        rf"create\s+or\s+replace\s+function\s+public\.{function}\s*\("
        r".*?\)\s*returns\s+text(.*?)\$\$;",
        sql,
        re.DOTALL | re.IGNORECASE,
    )
    assert match, f"missing replacement function: {function}"
    return compact(match.group(1))


def test_timestamp_name_fix_is_forward_only_and_parses() -> None:
    assert MIGRATION.is_file()
    parse_sql(MIGRATION.read_text(encoding="utf-8"))


def test_line_link_rpcs_do_not_use_current_time_as_a_variable() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    start = replacement_body(sql, "create_line_link_session")
    complete = replacement_body(sql, "complete_line_account_link")

    for body in (start, complete):
        assert "request_time timestamptz := pg_catalog.clock_timestamp()" in body
        assert "current_time timestamptz" not in body
        assert "security invoker" in body
        assert "set search_path = ''" in body

    assert "p_expires_at <= request_time" in start
    assert "p_expires_at > request_time + interval '10 minutes'" in start
    assert "session.expires_at >= request_time" in complete
    assert "set consumed_at = request_time" in complete
    assert "target_status, request_time, null, null" in complete


def test_timestamp_fix_preserves_service_role_only_execution() -> None:
    sql = compact(MIGRATION.read_text(encoding="utf-8"))

    for signature in (
        "public.create_line_link_session( uuid, text, text, timestamptz )",
        "public.complete_line_account_link( text, text, text, boolean )",
    ):
        normalized = compact(signature)
        assert f"revoke all on function {normalized}" in sql
        assert f"grant execute on function {normalized} to service_role;" in sql

    assert not re.search(
        r"grant execute on function public\.(?:create|complete)_line.*?"
        r"to (?:anon|authenticated)",
        sql,
    )
