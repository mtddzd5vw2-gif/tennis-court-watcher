from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATION_PATH = (
    ROOT
    / "supabase/migrations"
    / "20260812100000_add_account_role.sql"
)
EXISTING_MIGRATIONS = tuple(
    path
    for path in (ROOT / "supabase/migrations").glob("*.sql")
    if path != MIGRATION_PATH
)


def compact(value: str) -> str:
    without_comments = re.sub(r"--.*?$", "", value, flags=re.MULTILINE)
    return " ".join(without_comments.split())


def migration_sql() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8").lower()


def test_account_role_is_a_forward_migration() -> None:
    assert MIGRATION_PATH.is_file()

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


def test_account_role_enum_and_profile_column_are_exact_and_safe() -> None:
    sql = compact(migration_sql())
    enum = re.search(
        r"create type public\.account_role as enum \((.*?)\);",
        sql,
    )

    assert enum
    assert re.findall(r"'([^']+)'", enum.group(1)) == ["member", "admin"]
    assert re.search(
        r"alter table public\.profiles add column account_role "
        r"public\.account_role not null default "
        r"'member'::public\.account_role;",
        sql,
    )
    assert "alter type public.membership_status" not in sql
    assert "membership_status =" not in sql


def test_role_change_rpc_is_uuid_only_and_service_role_only() -> None:
    sql = compact(migration_sql())
    rpc = re.search(
        r"create function public\.set_account_role\( "
        r"p_user_id uuid, p_account_role public\.account_role \)"
        r"(.*?)\$\$;",
        sql,
    )

    assert rpc
    definition = rpc.group(1)
    assert "returns void" in definition
    assert "security definer" in definition
    assert "set search_path = ''" in definition
    assert "update public.profiles set account_role = p_account_role" in definition
    assert "where id = p_user_id" in definition
    assert "auth.uid()" not in definition
    assert "email" not in definition
    assert "user_metadata" not in definition
    assert re.search(
        r"revoke execute on function public\.set_account_role\("
        r"uuid, public\.account_role\) from public, anon, authenticated;",
        sql,
    )
    assert re.search(
        r"grant execute on function public\.set_account_role\("
        r"uuid, public\.account_role\) to service_role;",
        sql,
    )


def test_role_foundation_does_not_expand_profile_table_writes() -> None:
    sql = compact(migration_sql())

    assert not re.search(
        r"grant (?:insert|update|delete|all).*?public\.profiles",
        sql,
    )
    assert not re.search(
        r"create policy .*?on public\.profiles",
        sql,
    )
    assert "latest_terms_version" not in sql
    assert "latest_terms_accepted_at" not in sql
    assert "terms_acceptances" not in sql


def test_no_identity_or_subscription_shortcut_defines_admin() -> None:
    sql = migration_sql()

    for forbidden in (
        "@",
        "github",
        "raw_user_meta_data",
        "user_metadata",
        "subscription",
        "stripe",
    ):
        assert forbidden not in sql
