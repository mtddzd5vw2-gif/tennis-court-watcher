from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pglast import parse_sql


ROOT = Path(__file__).parents[1]
MIGRATION_PATH = (
    ROOT
    / "supabase/migrations"
    / "20260821051500_add_line_account_link_foundation.sql"
)
EXISTING_MIGRATIONS = tuple(
    path
    for path in (ROOT / "supabase/migrations").glob("*.sql")
    if path.name < MIGRATION_PATH.name
)


def compact(value: str) -> str:
    without_comments = re.sub(r"--.*?$", "", value, flags=re.MULTILINE)
    return " ".join(without_comments.split())


def migration_sql() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8").lower()


def table_definition(sql: str, table: str) -> str:
    match = re.search(
        rf"create\s+table\s+public\.{re.escape(table)}\s*\((.*?)\n\);",
        sql,
        re.DOTALL,
    )
    assert match, f"missing table definition: {table}"
    return compact(match.group(1))


def function_definition(sql: str, function: str) -> str:
    match = re.search(
        rf"create\s+function\s+public\.{re.escape(function)}\s*\(\)"
        r"(.*?)\$\$;",
        sql,
        re.DOTALL,
    )
    assert match, f"missing function definition: {function}"
    return compact(match.group(1))


def test_line_foundation_is_forward_only_and_parses_as_postgresql() -> None:
    assert MIGRATION_PATH.is_file()
    parse_sql(MIGRATION_PATH.read_text(encoding="utf-8"))

    changed_existing_migrations = []
    for path in EXISTING_MIGRATIONS:
        result = subprocess.run(
            [
                "git",
                "diff",
                "--quiet",
                "HEAD",
                "--",
                str(path.relative_to(ROOT)),
            ],
            cwd=ROOT,
            check=False,
        )
        if result.returncode != 0:
            changed_existing_migrations.append(path.name)

    assert changed_existing_migrations == []


def test_line_channel_and_link_status_are_forward_enum_additions() -> None:
    sql = compact(migration_sql())

    assert (
        "alter type public.notification_channel add value if not exists 'line';"
        in sql
    )
    enum = re.search(
        r"create type public\.line_account_link_status as enum \((.*?)\);",
        sql,
    )
    assert enum
    assert re.findall(r"'([^']+)'", enum.group(1)) == [
        "active",
        "blocked",
        "unlinked",
        "delivery_failed",
    ]


def test_account_links_enforce_one_member_and_one_line_account() -> None:
    link = table_definition(migration_sql(), "line_account_links")

    assert (
        "user_id uuid primary key references auth.users(id) on delete cascade"
        in link
    )
    assert "line_user_id text not null" in link
    assert "unique (line_user_id)" in link
    assert "pg_catalog.btrim(line_user_id) <> ''" in link
    assert "pg_catalog.char_length(line_user_id) <= 255" in link
    assert "status = 'unlinked'::public.line_account_link_status" in link
    assert "unlinked_at is not null" in link


def test_link_sessions_store_only_fixed_length_one_time_hashes() -> None:
    session = table_definition(migration_sql(), "line_link_sessions")

    assert "user_id uuid not null references auth.users(id) on delete cascade" in (
        session
    )
    assert "state_hash bytea not null" in session
    assert "nonce_hash bytea not null" in session
    assert "unique (state_hash)" in session
    assert "unique (nonce_hash)" in session
    assert "pg_catalog.octet_length(state_hash) = 32" in session
    assert "pg_catalog.octet_length(nonce_hash) = 32" in session
    assert "expires_at <= created_at + interval '10 minutes'" in session
    assert "consumed_at <= expires_at" in session

    for forbidden_column in (
        "state text",
        "nonce text",
        "authorization_code",
        "access_token",
        "id_token",
        "refresh_token",
    ):
        assert forbidden_column not in session


def test_line_tables_are_rls_protected_and_service_role_only() -> None:
    sql = compact(migration_sql())

    for table in ("line_account_links", "line_link_sessions"):
        assert f"alter table public.{table} enable row level security;" in sql

    assert (
        "revoke all privileges on table public.line_account_links, "
        "public.line_link_sessions from public, anon, authenticated, "
        "service_role;"
    ) in sql
    assert (
        "grant select, insert, update, delete on table "
        "public.line_account_links, public.line_link_sessions to service_role;"
    ) in sql
    policy = re.search(
        r"create policy line_account_links_select_own_active "
        r"on public\.line_account_links for select to authenticated "
        r"using \((.*?)\);",
        sql,
    )
    assert policy
    assert "(select auth.uid()) = user_id" in policy.group(1)
    assert "profile.id = (select auth.uid())" in policy.group(1)
    assert "profile.membership_status = 'active'::public.membership_status" in (
        policy.group(1)
    )
    assert "create policy" not in sql.split(
        "alter table public.line_link_sessions enable row level security;",
        maxsplit=1,
    )[0]
    assert (
        "grant select (status, linked_at, last_webhook_at) "
        "on table public.line_account_links to authenticated;"
    ) in sql
    assert not re.search(
        r"grant\s+select\s*\([^)]*line_user_id[^)]*\)",
        sql,
    )
    assert not re.search(
        r"grant\s+.*?on\s+table\s+public\.line_link_sessions\s+"
        r"to\s+(?:anon|authenticated)",
        sql,
    )
    grant_statements = re.findall(r"\bgrant\b.*?;", sql)
    assert not any(
        "on table" in grant
        and "public.line_link_sessions" in grant
        and re.search(r"\bto\s+(?:anon|authenticated)\s*;", grant)
        for grant in grant_statements
    )


def test_authenticated_status_rpc_returns_no_line_identifier() -> None:
    sql = compact(migration_sql())
    status_rpc = function_definition(migration_sql(), "get_my_line_link_status")

    assert "security invoker" in status_rpc
    assert "stable" in status_rpc
    assert "set search_path = ''" in status_rpc
    assert "current_user_id uuid := auth.uid()" in status_rpc
    assert "profile.id = current_user_id" in status_rpc
    assert "profile.membership_status = 'active'::public.membership_status" in (
        status_rpc
    )
    assert "from public.line_account_links as link" in status_rpc
    assert "where link.user_id" not in status_rpc
    assert "line_user_id" not in status_rpc
    assert (
        "revoke all on function public.get_my_line_link_status() "
        "from public, anon, authenticated, service_role;"
    ) in sql
    assert (
        "grant execute on function public.get_my_line_link_status() "
        "to authenticated;"
    ) in sql


def test_updated_at_trigger_is_not_browser_executable() -> None:
    sql = compact(migration_sql())
    trigger = function_definition(migration_sql(), "set_line_account_link_updated_at")

    assert "security invoker" in trigger
    assert "new.updated_at := pg_catalog.now()" in trigger
    assert "before update on public.line_account_links" in sql
    assert (
        "revoke all on function public.set_line_account_link_updated_at() "
        "from public, anon, authenticated, service_role;"
    ) in sql
