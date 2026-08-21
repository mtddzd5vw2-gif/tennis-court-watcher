from __future__ import annotations

import re
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "supabase/migrations"
    / "20260821022637_add_configurable_notification_targets.sql"
)


def compact(value: str) -> str:
    without_comments = re.sub(r"--.*?$", "", value, flags=re.MULTILINE)
    return " ".join(without_comments.split())


def migration_sql() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8").lower()


def test_matching_rpc_has_the_private_normalized_result_shape() -> None:
    sql = compact(migration_sql())
    signature = re.search(
        r"create function public\.list_notification_rules_for_matching\(\) "
        r"returns table \((.*?)\) language sql",
        sql,
    )

    assert signature
    result_columns = signature.group(1)
    for expected in (
        "rule_id uuid",
        "user_id uuid",
        "date_from date",
        "date_to date",
        "start_time time without time zone",
        "end_time time without time zone",
        "minimum_duration_minutes smallint",
        "include_holidays boolean",
        "facility_ids text[]",
        "weekdays smallint[]",
    ):
        assert expected in result_columns
    assert "is_enabled" not in result_columns
    assert "email" not in sql


def test_matching_rpc_is_invoker_stable_and_uses_fully_qualified_objects() -> None:
    sql = compact(migration_sql())

    assert (
        "language sql security invoker stable set search_path = '' as $$"
        in sql
    )
    assert "security definer" not in sql
    assert "disable row level security" not in sql
    assert "create policy" not in sql
    for table in (
        "public.notification_rules",
        "public.profiles",
        "public.notification_rule_facilities",
        "public.notification_rule_weekdays",
    ):
        assert table in sql
    assert "pg_catalog.array_agg" in sql
    assert "pg_catalog.count" in sql


def test_matching_rpc_only_returns_complete_enabled_active_member_rules() -> None:
    sql = compact(migration_sql())

    assert "profile.membership_status = 'active'::public.membership_status" in sql
    assert "where rule.is_enabled = true" in sql
    assert (
        "pg_catalog.count(distinct selected_facility.facility_id) >= 1"
        in sql
    )
    assert "pg_catalog.count(distinct selected_weekday.weekday) >= 1" in sql
    assert "or rule.include_holidays = true" in sql
    assert "left join public.notification_rule_weekdays" in sql


def test_matching_rpc_arrays_and_rows_have_deterministic_order() -> None:
    sql = compact(migration_sql())

    assert (
        "pg_catalog.array_agg( distinct selected_facility.facility_id "
        "order by selected_facility.facility_id ) as facility_ids"
    ) in sql
    assert (
        "pg_catalog.array_agg( distinct selected_weekday.weekday "
        "order by selected_weekday.weekday ) filter "
        "(where selected_weekday.weekday is not null)"
    ) in sql
    assert "order by rule.user_id, rule.id;" in sql


def test_migration_does_not_schema_qualify_coalesce_syntax() -> None:
    sql = migration_sql()

    assert "pg_catalog.coalesce" not in sql
    assert sql.count("coalesce(") == 2


def test_matching_rpc_execute_permission_is_service_role_only() -> None:
    sql = compact(migration_sql())
    matching_section = sql[
        sql.index("drop function public.list_notification_rules_for_matching()"):
    ]
    grants = re.findall(r"\bgrant execute\b.*?;", matching_section)

    assert grants == [
        "grant execute on function "
        "public.list_notification_rules_for_matching() to service_role;"
    ]
    assert (
        "revoke execute on function "
        "public.list_notification_rules_for_matching() "
        "from public, anon, authenticated;"
    ) in sql
