from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATION_DIR = ROOT / "supabase/migrations"
DESIGN_PATH = ROOT / "docs/PHASE2_NOTIFICATION_RULES_DESIGN.md"
MASTER_TABLES = ("regions", "facility_types", "facilities")
USER_TABLES = (
    "notification_rules",
    "notification_rule_facilities",
    "notification_rule_weekdays",
)
ALL_TABLES = (*MASTER_TABLES, *USER_TABLES)
EXPECTED_FACILITIES = {
    "kamoike-prefectural": "鴨池県営テニスコート",
    "sumizei": "SuMIzeiテニスコート",
    "toukai-tennis": "東開庭球場",
}
NOTIFICATION_MIGRATION_HASH = (
    "8f28ec3d2ba245a9360002ed971ac954957e6078e427ec1d3662be3604b41581"
)
SAVE_RPC_MIGRATION_HASH = (
    "343a3ac9ba08b0303387c61b831d3a2844753373ea123618acb4911d96139494"
)
IMMUTABLE_MIGRATION_HASHES = {
    "20260804000000_create_member_profiles.sql": (
        "f55877aed328b98cd05ce17c92998b9fbb6f7dfc66ca72abbedf533010121056"
    ),
    "20260806000000_fix_accept_current_terms_conflict.sql": (
        "8460e7a4ba02ac72deb98cfdd58bab183bf2edd8a7f41b4ae307458414cc2fe5"
    ),
}


def notification_migration_path() -> Path:
    migrations = tuple(MIGRATION_DIR.glob("*_create_notification_rules.sql"))
    assert len(migrations) == 1
    return migrations[0]


def migration_sql() -> str:
    return notification_migration_path().read_text(encoding="utf-8").lower()


def compact(value: str) -> str:
    without_comments = re.sub(r"--.*?$", "", value, flags=re.MULTILINE)
    return " ".join(without_comments.split())


def table_definition(sql: str, table: str) -> str:
    match = re.search(
        rf"create\s+table\s+public\.{re.escape(table)}\s*\((.*?)\n\);",
        sql,
        re.DOTALL,
    )
    assert match, f"missing table definition: {table}"
    return compact(match.group(1))


def policy_definition(sql: str, table: str, action: str) -> str:
    match = re.search(
        rf"create\s+policy\s+\S+\s+on\s+public\.{re.escape(table)}"
        rf"\s+for\s+{action}\s+to\s+authenticated\s+(.*?);",
        sql,
        re.DOTALL,
    )
    assert match, f"missing {action} policy on {table}"
    return compact(match.group(1))


def test_notification_rule_migration_defines_six_tables_before_save_rpc() -> None:
    migration = notification_migration_path()
    save_rpc_migration = (
        MIGRATION_DIR / "20260807100000_add_notification_rule_save_rpc.sql"
    )

    assert migration.name == "20260807000000_create_notification_rules.sql"
    assert save_rpc_migration.is_file()
    assert migration.name[:14] < save_rpc_migration.name[:14]
    assert all(
        migration.name[:14] > existing_name[:14]
        for existing_name in IMMUTABLE_MIGRATION_HASHES
    )
    sql = migration_sql()
    for table in ALL_TABLES:
        assert re.search(rf"create\s+table\s+public\.{table}\s*\(", sql)


def test_master_seed_matches_availability_json() -> None:
    availability = json.loads(
        (ROOT / "data/availability.json").read_text(encoding="utf-8")
    )
    availability_facilities = {
        facility["id"]: facility["name"]
        for facility in availability["facilities"]
    }
    assert availability_facilities == EXPECTED_FACILITIES

    sql = notification_migration_path().read_text(encoding="utf-8")
    facilities_insert = re.search(
        r"insert\s+into\s+public\.facilities\s*\(.*?\)\s*values\s*(.*?);",
        sql,
        re.DOTALL,
    )
    assert facilities_insert
    seeded_rows = re.findall(
        r"\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\s*,"
        r"\s*'([^']+)'\s*,\s*\d+\s*\)",
        facilities_insert.group(1),
    )
    seeded_facilities = {facility_id: name for facility_id, _, _, name in seeded_rows}
    assert seeded_facilities == EXPECTED_FACILITIES
    assert all(
        region_id == "jp-kagoshima-kagoshima-city"
        and facility_type_id == "tennis-court"
        for _, region_id, facility_type_id, _ in seeded_rows
    )


def test_region_and_facility_type_seeds_are_defined() -> None:
    sql = compact(migration_sql())

    assert "'jp-kagoshima-kagoshima-city'" in sql
    assert "'jp'" in sql
    assert "'46'" in sql
    assert "'46201'" in sql
    assert "'鹿児島市'" in sql
    assert "'asia/tokyo'" in sql
    assert "'tennis-court'" in sql
    assert "'テニスコート'" in sql


def test_notification_rules_has_owner_foreign_key_constraints_and_index() -> None:
    sql = migration_sql()
    rules = table_definition(sql, "notification_rules")

    assert "user_id uuid not null references auth.users(id) on delete cascade" in rules
    assert "unique (id, user_id)" in rules
    assert re.search(
        r"create\s+index\s+notification_rules_user_id_idx"
        r"\s+on\s+public\.notification_rules\s*\(\s*user_id\s*\)",
        sql,
    )

    for table in ("notification_rule_facilities", "notification_rule_weekdays"):
        definition = table_definition(sql, table)
        assert "foreign key (rule_id, user_id)" in definition
        assert (
            "references public.notification_rules(id, user_id) on delete cascade"
            in definition
        )


def test_rule_value_checks_cover_names_dates_times_duration_and_weekdays() -> None:
    sql = migration_sql()
    rules = table_definition(sql, "notification_rules")
    weekdays = table_definition(sql, "notification_rule_weekdays")

    assert "check (btrim(name) <> '')" in rules
    assert "check (char_length(name) <= 80)" in rules
    assert "check (start_time < end_time)" in rules
    assert (
        "check ( date_from is null or date_to is null or date_from <= date_to )"
        in rules
    )
    assert "minimum_duration_minutes between 30 and 720" in rules
    assert "minimum_duration_minutes % 30 = 0" in rules
    assert "check (weekday between 1 and 7)" in weekdays
    assert "is_enabled boolean not null default false" in rules


def test_required_child_indexes_are_present() -> None:
    sql = migration_sql()

    expected_indexes = {
        "notification_rule_facilities_user_id_idx": (
            "notification_rule_facilities",
            "user_id",
        ),
        "notification_rule_facilities_facility_id_idx": (
            "notification_rule_facilities",
            "facility_id",
        ),
        "notification_rule_weekdays_user_id_idx": (
            "notification_rule_weekdays",
            "user_id",
        ),
    }
    for index, (table, column) in expected_indexes.items():
        assert re.search(
            rf"create\s+index\s+{index}\s+on\s+public\.{table}"
            rf"\s*\(\s*{column}\s*\)",
            sql,
        )


def test_rls_is_enabled_on_all_six_tables() -> None:
    sql = migration_sql()

    for table in ALL_TABLES:
        assert re.search(
            rf"alter\s+table\s+public\.{table}\s+enable\s+row\s+level\s+security",
            sql,
        )


def test_user_table_policies_require_owner_and_active_membership() -> None:
    sql = migration_sql()

    for table in USER_TABLES:
        for action in ("select", "insert", "update", "delete"):
            policy = policy_definition(sql, table, action)
            assert "(select auth.uid()) is not null" in policy
            assert "(select auth.uid()) = user_id" in policy
            assert "from public.profiles as profile" in policy
            assert "profile.id = (select auth.uid())" in policy
            assert "profile.membership_status = 'active'" in policy


def test_insert_and_update_policies_use_required_checks() -> None:
    sql = migration_sql()

    for table in USER_TABLES:
        insert_policy = policy_definition(sql, table, "insert")
        update_policy = policy_definition(sql, table, "update")
        assert "with check" in insert_policy
        assert "using" in update_policy
        assert "with check" in update_policy


def test_master_policies_and_grants_are_authenticated_read_only() -> None:
    sql = migration_sql()
    normalized = compact(sql)

    for table in MASTER_TABLES:
        actions = re.findall(
            rf"create\s+policy\s+\S+\s+on\s+public\.{table}"
            r"\s+for\s+(\w+)\s+to\s+authenticated",
            sql,
        )
        assert actions == ["select"]

    assert (
        "grant select on table public.regions, public.facility_types, "
        "public.facilities to authenticated;"
    ) in normalized
    assert (
        "grant select, insert, update, delete on table "
        "public.notification_rules, public.notification_rule_facilities, "
        "public.notification_rule_weekdays to authenticated;"
    ) in normalized

    grant_statements = re.findall(r"\bgrant\b.*?;", normalized)
    assert not any(re.search(r"\bto\s+anon\s*;", grant) for grant in grant_statements)
    assert not any(re.search(r"\bto\s+public\s*;", grant) for grant in grant_statements)
    assert "service_role" not in sql


def test_all_browser_privileges_are_revoked_before_minimal_grants() -> None:
    sql = compact(migration_sql())
    revoke = re.search(
        r"revoke all privileges on table (.*?) "
        r"from public, anon, authenticated;",
        sql,
    )

    assert revoke
    for table in ALL_TABLES:
        assert f"public.{table}" in revoke.group(1)


def test_updated_at_trigger_is_isolated_and_not_browser_executable() -> None:
    sql = migration_sql()
    function = re.search(
        r"create\s+function\s+public\.set_notification_rule_updated_at\(\)"
        r"(.*?)\$\$;",
        sql,
        re.DOTALL,
    )

    assert function
    assert "set search_path = ''" in function.group(1)
    assert "new.updated_at := pg_catalog.now()" in function.group(1)
    assert "security definer" not in function.group(1)
    assert re.search(
        r"create\s+trigger\s+set_notification_rules_updated_at"
        r"\s+before\s+update\s+on\s+public\.notification_rules",
        sql,
    )
    assert re.search(
        r"revoke\s+all\s+on\s+function"
        r"\s+public\.set_notification_rule_updated_at\(\)"
        r"\s+from\s+public,\s*anon,\s*authenticated",
        sql,
    )


def test_phase_two_design_documents_boundaries_and_incomplete_rules() -> None:
    design = DESIGN_PATH.read_text(encoding="utf-8")

    for expected in (
        "Phase 2",
        "Phase 3",
        "availability.json",
        "ISO 8601",
        "regions.timezone",
        "開始日の下限なし",
        "終了日の上限なし",
        "is_enabled",
        "active",
        "authenticated",
        "anon",
        "複合外部キー",
        "条件数上限",
        "施設1件以上",
        "曜日1件以上",
        "save_notification_rule",
        "security invoker",
        "完了",
        "最大5件",
        "停止中",
        "advisory",
        "照合エンジン",
        "自動適用されない",
    ):
        assert expected in design


def test_existing_migration_content_remains_unchanged() -> None:
    for filename, expected_hash in IMMUTABLE_MIGRATION_HASHES.items():
        normalized_bytes = (MIGRATION_DIR / filename).read_bytes().replace(
            b"\r\n", b"\n"
        )
        digest = hashlib.sha256(normalized_bytes).hexdigest()
        assert digest == expected_hash

    notification_bytes = notification_migration_path().read_bytes().replace(
        b"\r\n", b"\n"
    )
    assert hashlib.sha256(notification_bytes).hexdigest() == (
        NOTIFICATION_MIGRATION_HASH
    )
    save_rpc_bytes = (
        MIGRATION_DIR / "20260807100000_add_notification_rule_save_rpc.sql"
    ).read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(save_rpc_bytes).hexdigest() == SAVE_RPC_MIGRATION_HASH


def test_phase_zero_public_data_and_ui_are_not_modified() -> None:
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    changed_paths = set(result.stdout.splitlines())
    forbidden_exact = {
        "data/availability.json",
        "index.html",
    }

    assert changed_paths.isdisjoint(forbidden_exact)
