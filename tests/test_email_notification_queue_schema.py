from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATION_PATH = (
    ROOT
    / "supabase/migrations"
    / "20260807140000_create_email_notification_queue.sql"
)
DESIGN_PATH = ROOT / "docs/PHASE3_USER_EMAIL_NOTIFICATION_DESIGN.md"
TABLES = (
    "notification_email_preferences",
    "notification_delivery_items",
    "notification_messages",
    "notification_message_items",
    "notification_provider_events",
)
INTERNAL_TABLES = (
    "notification_delivery_items",
    "notification_messages",
    "notification_message_items",
    "notification_provider_events",
)
MESSAGE_STATUSES = (
    "pending",
    "processing",
    "accepted",
    "delivered",
    "retry_wait",
    "failed_permanent",
    "bounced",
    "complained",
    "suppressed",
    "cancelled",
)


def migration_sql() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8").lower()


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


def function_definition(sql: str, function: str) -> str:
    match = re.search(
        rf"create\s+function\s+public\.{re.escape(function)}\s*\("
        r"(.*?)\$\$;",
        sql,
        re.DOTALL,
    )
    assert match, f"missing function definition: {function}"
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


def test_phase_three_migration_exists_after_phase_two_migrations() -> None:
    previous_migration = (
        ROOT
        / "supabase/migrations"
        / "20260807130000_limit_notification_rules_per_user.sql"
    )

    assert MIGRATION_PATH.is_file()
    assert previous_migration.is_file()
    assert previous_migration.name < MIGRATION_PATH.name


def test_all_five_phase_three_tables_exist() -> None:
    sql = migration_sql()

    for table in TABLES:
        assert re.search(
            rf"create\s+table\s+public\.{table}\s*\(",
            sql,
        )


def test_public_notification_tables_have_no_email_address_column() -> None:
    sql = migration_sql()

    for table in TABLES:
        definition = table_definition(sql, table)
        column_names = re.findall(
            r"(?:^|,)\s*([a-z][a-z0-9_]*)\s+"
            r"(?:uuid|text|boolean|date|time|timestamptz|integer|jsonb|public\.)",
            definition,
        )
        assert "email" not in column_names
        assert "email_address" not in column_names
        assert "recipient_email" not in column_names
        assert not any(column.endswith("_email") for column in column_names)

    assert "auth.users.email" not in sql
    assert "raw_user_meta_data" not in sql


def test_email_preferences_are_keyed_by_user_and_default_off() -> None:
    sql = migration_sql()
    preference = table_definition(sql, "notification_email_preferences")

    assert (
        "user_id uuid primary key references auth.users(id) on delete cascade"
        in preference
    )
    assert "is_enabled boolean not null default false" in preference
    for column in (
        "disabled_reason text",
        "disabled_at timestamptz",
        "created_at timestamptz not null default now()",
        "updated_at timestamptz not null default now()",
    ):
        assert column in preference

    assert (
        "insert into public.notification_email_preferences (user_id) "
        "select auth_user.id from auth.users as auth_user "
        "on conflict (user_id) do nothing;"
    ) in compact(sql)
    assert "after insert on auth.users" in compact(sql)


def test_authenticated_can_only_read_and_toggle_own_active_preference() -> None:
    sql = migration_sql()
    normalized = compact(sql)
    select_policy = policy_definition(
        sql,
        "notification_email_preferences",
        "select",
    )
    update_policy = policy_definition(
        sql,
        "notification_email_preferences",
        "update",
    )

    for policy in (select_policy, update_policy):
        assert "(select auth.uid()) is not null" in policy
        assert "(select auth.uid()) = user_id" in policy
        assert "from public.profiles as profile" in policy
        assert "profile.membership_status = 'active'::public.membership_status" in (
            policy
        )
    assert "using" in update_policy
    assert "with check" in update_policy
    assert (
        "grant select on table public.notification_email_preferences "
        "to authenticated;"
    ) in normalized
    assert (
        "grant update (is_enabled) on table "
        "public.notification_email_preferences to authenticated;"
    ) in normalized
    assert not re.search(
        r"grant\s+update\s+on\s+(?:table\s+)?"
        r"public\.notification_email_preferences\s+to\s+authenticated\s*;",
        normalized,
    )
    assert not re.search(
        r"grant\s+(?:insert|delete)\b.*?"
        r"public\.notification_email_preferences.*?to\s+authenticated",
        normalized,
    )


def test_delivery_item_contains_required_snapshot_and_payload_fields() -> None:
    definition = table_definition(
        migration_sql(),
        "notification_delivery_items",
    )

    for expected in (
        "user_id uuid not null references auth.users(id) on delete cascade",
        "channel public.notification_channel not null",
        "slot_id text not null",
        "facility_id text not null references public.facilities(id)",
        "facility_name text not null",
        "available_date date not null",
        "start_time time without time zone not null",
        "end_time time without time zone not null",
        "matched_rule_ids uuid[] not null",
        "payload jsonb not null",
        "created_at timestamptz not null default now()",
    ):
        assert expected in definition

    assert "check (start_time < end_time)" in definition
    assert "pg_catalog.jsonb_typeof(payload) = 'object'" in definition
    assert "public.notification_email_payload_is_valid(payload)" in definition
    assert "pg_catalog.octet_length(payload::pg_catalog.text) <= 16384" in (
        definition
    )


def test_payload_uses_a_structural_allowlist_instead_of_email_regex_checks() -> None:
    sql = migration_sql()
    validator = function_definition(sql, "notification_email_payload_is_valid")

    assert "pg_catalog.jsonb_typeof(p_payload) = 'object'" in validator
    assert "from pg_catalog.jsonb_object_keys(" in validator
    assert "'court_name'" in validator
    assert "'reservation_url'" in validator
    assert "payload_key.key <> all" in validator
    assert (
        "revoke all on function "
        "public.notification_email_payload_is_valid(jsonb) "
        "from public, anon, authenticated;"
    ) in compact(sql)
    for removed_check in (
        "notification_payload_has_forbidden_keys",
        "payload_has_no_email_address",
        "last_error_message_has_no_email_address",
    ):
        assert removed_check not in sql


def test_delivery_item_database_constraint_is_the_deduplication_authority() -> None:
    raw_sql = migration_sql()
    sql = compact(raw_sql)
    definition = table_definition(raw_sql, "notification_delivery_items")

    assert "unique (user_id, channel, slot_id)" in definition
    assert (
        "on conflict (user_id, channel, slot_id) do nothing"
        in sql
    )
    assert "unique (delivery_item_id)" in table_definition(
        raw_sql,
        "notification_message_items",
    )


def test_duplicate_candidates_union_rules_and_reject_snapshot_conflicts() -> None:
    enqueue = function_definition(
        migration_sql(),
        "enqueue_email_notification_candidates",
    )

    assert "candidate_snapshots as materialized" in enqueue
    assert "select distinct on (" in enqueue
    assert "aggregated_candidate_rules as materialized" in enqueue
    assert (
        "pg_catalog.array_agg( distinct matched_rule.rule_id "
        "order by matched_rule.rule_id )"
    ) in enqueue
    assert (
        "group by candidate.user_id, candidate.channel, candidate.slot_id "
        "having pg_catalog.count(distinct candidate.snapshot) > 1"
    ) in enqueue
    assert (
        "duplicate email notification candidates have conflicting snapshots."
        in enqueue
    )


def test_message_status_enum_contains_the_exact_lifecycle_states() -> None:
    sql = migration_sql()
    match = re.search(
        r"create\s+type\s+public\.notification_message_status"
        r"\s+as\s+enum\s*\((.*?)\);",
        sql,
        re.DOTALL,
    )

    assert match
    assert tuple(re.findall(r"'([a-z_]+)'", match.group(1))) == MESSAGE_STATUSES


def test_message_contains_retry_lock_provider_and_outcome_fields() -> None:
    sql = migration_sql()
    definition = table_definition(sql, "notification_messages")

    for expected in (
        "user_id uuid not null references auth.users(id) on delete cascade",
        "channel public.notification_channel not null",
        "status public.notification_message_status not null default 'pending'",
        "attempt_count integer not null default 0",
        "next_attempt_at timestamptz not null default now()",
        "locked_at timestamptz",
        "locked_until timestamptz",
        "provider_message_id text",
        "provider_status text",
        "last_error_code text",
        "last_error_message text",
        "accepted_at timestamptz",
        "delivered_at timestamptz",
        "failed_at timestamptz",
        "created_at timestamptz not null default now()",
        "updated_at timestamptz not null default now()",
    ):
        assert expected in definition

    assert "check (attempt_count >= 0)" in definition
    assert "locked_at < locked_until" in definition
    assert "unique (id, provider_message_id)" in definition
    assert re.search(
        r"create\s+index\s+notification_messages_created_at_idx"
        r"\s+on\s+public\.notification_messages\s*\(\s*created_at\s*\)",
        sql,
    )


def test_message_item_foreign_keys_preserve_user_and_channel_ownership() -> None:
    definition = table_definition(
        migration_sql(),
        "notification_message_items",
    )

    assert "foreign key (message_id, user_id, channel)" in definition
    assert (
        "references public.notification_messages(id, user_id, channel) "
        "on delete cascade"
    ) in definition
    assert "foreign key (delivery_item_id, user_id, channel)" in definition
    assert (
        "references public.notification_delivery_items(id, user_id, channel) "
        "on delete cascade"
    ) in definition


def test_provider_events_are_normalized_without_raw_webhook_payloads() -> None:
    sql = migration_sql()
    definition = table_definition(sql, "notification_provider_events")

    for expected in (
        "message_id uuid not null",
        "provider text not null default 'resend'",
        "provider_event_id text not null",
        "provider_message_id text not null",
        "event_type text not null",
        "occurred_at timestamptz not null",
        "unique (provider, provider_event_id)",
    ):
        assert expected in definition
    assert "foreign key (message_id, provider_message_id)" in definition
    assert (
        "references public.notification_messages(id, provider_message_id) "
        "on delete cascade"
    ) in definition
    assert "unique (id, provider_message_id)" in table_definition(
        sql,
        "notification_messages",
    )
    assert not re.search(r"\b(?:payload|headers|body)\s+jsonb\b", definition)


def test_rls_is_enabled_on_all_phase_three_tables() -> None:
    sql = migration_sql()

    for table in TABLES:
        assert re.search(
            rf"alter\s+table\s+public\.{table}"
            r"\s+enable\s+row\s+level\s+security",
            sql,
        )


def test_internal_delivery_tables_have_no_browser_policies_or_grants() -> None:
    sql = migration_sql()
    normalized = compact(sql)

    for table in INTERNAL_TABLES:
        assert not re.search(
            rf"create\s+policy\s+\S+\s+on\s+public\.{table}\b",
            sql,
        )
        assert not re.search(
            rf"grant\b.*?public\.{table}.*?"
            r"\bto\s+(?:anon|authenticated|public)\s*;",
            normalized,
        )

    revoke = re.search(
        r"revoke all privileges on table (.*?) "
        r"from public, anon, authenticated;",
        normalized,
    )
    assert revoke
    for table in TABLES:
        assert f"public.{table}" in revoke.group(1)


def test_enqueue_and_claim_execute_permissions_are_service_role_only() -> None:
    sql = compact(migration_sql())
    grants = re.findall(r"\bgrant execute on function\b.*?;", sql)

    assert grants == [
        "grant execute on function "
        "public.enqueue_email_notification_candidates(jsonb) "
        "to service_role;",
        "grant execute on function public.claim_email_messages(integer) "
        "to service_role;",
    ]
    for signature in (
        "public.enqueue_email_notification_candidates(jsonb)",
        "public.claim_email_messages(integer)",
    ):
        assert (
            f"revoke execute on function {signature} "
            "from public, anon, authenticated;"
        ) in sql


def test_every_security_definer_function_has_an_empty_search_path() -> None:
    sql = migration_sql()

    for function in (
        "create_email_notification_preference_for_new_auth_user",
        "enqueue_email_notification_candidates",
        "claim_email_messages",
    ):
        definition = function_definition(sql, function)
        assert "security definer set search_path = '' as $$" in definition


def test_privileged_functions_use_fully_qualified_database_objects() -> None:
    sql = migration_sql()
    enqueue = function_definition(
        sql,
        "enqueue_email_notification_candidates",
    )
    claim = function_definition(sql, "claim_email_messages")

    for table in (
        "public.facilities",
        "public.profiles",
        "public.notification_rules",
        "public.notification_email_preferences",
        "public.notification_delivery_items",
        "public.notification_messages",
        "public.notification_message_items",
    ):
        assert table in f"{enqueue} {claim}"
    for function in (
        "pg_catalog.jsonb_typeof",
        "pg_catalog.jsonb_array_length",
        "pg_catalog.jsonb_array_elements",
        "pg_catalog.jsonb_object_keys",
        "pg_catalog.btrim",
        "pg_catalog.char_length",
        "pg_catalog.cardinality",
        "pg_catalog.array_agg",
        "pg_catalog.count",
        "pg_catalog.now",
        "pg_catalog.jsonb_agg",
        "pg_catalog.jsonb_build_object",
    ):
        assert function in f"{enqueue} {claim}"


def test_enqueue_validates_json_shape_types_and_safe_limits() -> None:
    enqueue = function_definition(
        migration_sql(),
        "enqueue_email_notification_candidates",
    )

    assert "pg_catalog.jsonb_typeof(p_candidates) <> 'array'" in enqueue
    assert "v_max_candidates constant pg_catalog.int4 := 500" in enqueue
    assert "v_candidate_count > v_max_candidates" in enqueue
    assert "from pg_catalog.jsonb_object_keys(v_candidate)" in enqueue
    for required_key in (
        "user_id",
        "channel",
        "slot_id",
        "facility_id",
        "facility_name",
        "available_date",
        "start_time",
        "end_time",
        "matched_rule_ids",
        "payload",
    ):
        assert f"'{required_key}'" in enqueue
    assert "v_max_matched_rules constant pg_catalog.int4 := 5" in enqueue
    assert "v_max_payload_bytes constant pg_catalog.int4 := 16384" in enqueue
    assert "::pg_catalog.uuid" in enqueue
    assert "::pg_catalog.date" in enqueue
    assert "::pg_catalog.time" in enqueue


def test_enqueue_treats_an_empty_array_as_an_aggregate_zero_noop() -> None:
    enqueue = function_definition(
        migration_sql(),
        "enqueue_email_notification_candidates",
    )
    design = DESIGN_PATH.read_text(encoding="utf-8")

    assert "v_candidate_count := pg_catalog.jsonb_array_length(p_candidates)" in (
        enqueue
    )
    assert "if v_candidate_count < 1" not in enqueue
    assert "空配列は安全なno-op" in design


def test_enqueue_rechecks_member_preference_and_rule_state() -> None:
    enqueue = function_definition(
        migration_sql(),
        "enqueue_email_notification_candidates",
    )

    assert "inner join public.profiles as profile" in enqueue
    assert (
        "profile.membership_status = 'active'::public.membership_status"
        in enqueue
    )
    assert (
        "inner join public.notification_email_preferences as preference"
        in enqueue
    )
    assert "preference.is_enabled = true" in enqueue
    assert "preference.disabled_reason is null" in enqueue
    assert "from public.notification_rules as notification_rule" in enqueue
    assert "notification_rule.is_enabled = true" in enqueue


def test_enqueue_only_creates_messages_for_new_delivery_items() -> None:
    enqueue = function_definition(
        migration_sql(),
        "enqueue_email_notification_candidates",
    )

    assert "inserted_delivery_items as (" in enqueue
    assert "from inserted_delivery_items as delivery_item" in enqueue
    assert "group by delivery_item.user_id, delivery_item.channel" in enqueue
    assert "inserted_links as (" in enqueue
    assert "inner join inserted_messages as message" in enqueue


def test_enqueue_result_contains_aggregate_counts_and_no_individual_ids() -> None:
    sql = compact(migration_sql())
    signature = re.search(
        r"create function public\.enqueue_email_notification_candidates\( "
        r"p_candidates jsonb \) returns table \((.*?)\) language plpgsql",
        sql,
    )

    assert signature
    result_columns = signature.group(1).strip()
    assert result_columns == (
        "candidate_count integer, "
        "inserted_delivery_item_count integer, "
        "inserted_message_count integer, "
        "linked_item_count integer"
    )
    assert not re.search(
        r"\b(?:user_id|rule_id|delivery_item_id|message_id|email)\b",
        result_columns,
    )


def test_claim_uses_bounded_skip_locked_leases_and_retry_states() -> None:
    claim = function_definition(migration_sql(), "claim_email_messages")

    assert "v_max_batch_size constant pg_catalog.int4 := 100" in claim
    assert "batch_size < 1" in claim
    assert "batch_size > v_max_batch_size" in claim
    assert "for update of message skip locked" in claim
    assert "limit batch_size" in claim
    assert "'pending'::public.notification_message_status" in claim
    assert "'retry_wait'::public.notification_message_status" in claim
    assert "'processing'::public.notification_message_status" in claim
    assert "message.next_attempt_at <= pg_catalog.now()" in claim
    assert "message.locked_until <= pg_catalog.now()" in claim
    assert "status = 'processing'::public.notification_message_status" in claim
    assert "locked_at = pg_catalog.now()" in claim
    assert "locked_until = pg_catalog.now() + interval '5 minutes'" in claim


def test_claim_cancels_ineligible_messages_before_claiming_eligible_messages() -> None:
    claim = function_definition(migration_sql(), "claim_email_messages")

    assert "ineligible_messages as materialized" in claim
    assert "cancelled_messages as (" in claim
    assert "not exists (" in claim
    assert "status = 'cancelled'::public.notification_message_status" in claim
    assert "locked_at = null" in claim
    assert "locked_until = null" in claim
    assert "claimable_messages as materialized" in claim
    assert claim.count("public.profiles as profile") >= 2
    assert claim.count(
        "profile.membership_status = "
        "'active'::public.membership_status"
    ) >= 2
    assert claim.count("preference.is_enabled = true") >= 2
    assert claim.count("preference.disabled_reason is null") >= 2
    assert (
        "message.status in ( "
        "'pending'::public.notification_message_status, "
        "'retry_wait'::public.notification_message_status )"
    ) in claim
    assert (
        "message.status = 'processing'::public.notification_message_status "
        "and message.locked_until <= pg_catalog.now()"
    ) in claim


def test_claim_result_includes_user_id_but_excludes_email_and_internal_item_ids() -> None:
    sql = compact(migration_sql())
    signature = re.search(
        r"create function public\.claim_email_messages\( batch_size integer \) "
        r"returns table \((.*?)\) language plpgsql",
        sql,
    )
    claim = function_definition(sql, "claim_email_messages")

    assert signature
    result_columns = signature.group(1)
    assert "message_id uuid" in result_columns
    assert "user_id uuid" in result_columns
    assert "items jsonb" in result_columns
    assert "email" not in result_columns
    assert "rule_id" not in result_columns
    assert "delivery_item_id" not in result_columns
    returned_item_shape = claim.split("pg_catalog.jsonb_build_object(", 1)[1]
    returned_item_shape = returned_item_shape.split(")", 1)[0]
    for forbidden in (
        "'email'",
        "'user_id'",
        "'matched_rule_ids'",
        "'slot_id'",
        "'delivery_item_id'",
    ):
        assert forbidden not in returned_item_shape


def test_design_covers_required_phase_three_decisions_and_boundaries() -> None:
    design = DESIGN_PATH.read_text(encoding="utf-8")

    for expected in (
        "Phase 3",
        "Phase 4",
        "既存管理者向けLINE通知",
        "初期OFF",
        "unique (user_id, channel, slot_id)",
        "同じ `slot_id`",
        "DBの一意制約",
        "FOR UPDATE SKIP LOCKED",
        "Resend",
        "webhook",
        "配信停止",
        "個人情報",
        "機能フラグ",
        "段階的導入",
        "90日",
        "service_role",
        "GitHub Actions",
        "対象外",
        "Supabase Auth Admin API",
        "user_id",
        "空配列は安全なno-op",
        "許可field",
        "cancelled",
    ):
        assert expected in design


def test_public_availability_data_is_not_modified() -> None:
    diff_result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    status_result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    changed_paths = set(diff_result.stdout.splitlines())
    for status_line in status_result.stdout.splitlines():
        path = status_line[3:]
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        changed_paths.add(path.strip('"'))

    forbidden = {"data/availability.json"}

    assert changed_paths.isdisjoint(forbidden)
