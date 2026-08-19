from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATION_PATH = (
    ROOT
    / "supabase/migrations"
    / "20260818100000_add_email_notification_retention_cleanup.sql"
)
RUNBOOK_PATH = ROOT / "docs/PHASE3_RETENTION_CLEANUP.md"


def compact(value: str) -> str:
    without_comments = re.sub(r"--.*?$", "", value, flags=re.MULTILINE)
    return " ".join(without_comments.split())


def migration_sql() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8").lower()


def test_retention_cleanup_migration_is_forward_only_and_bounded() -> None:
    assert MIGRATION_PATH.is_file()

    sql = compact(migration_sql())

    assert (
        "create function public.cleanup_email_notification_history( "
        "batch_size integer default 1000 )"
    ) in sql
    assert "batch_size < 1 or batch_size > 1000" in sql
    assert "security definer set search_path = ''" in sql
    assert "for update of message skip locked limit batch_size" in sql
    assert "for update of delivery_item skip locked limit batch_size" in sql


def test_cleanup_only_deletes_old_stable_terminal_messages() -> None:
    sql = compact(migration_sql())

    assert "message.created_at < v_cutoff" in sql
    assert "message.updated_at < v_cutoff" in sql
    for status in (
        "accepted",
        "delivered",
        "failed_permanent",
        "bounced",
        "complained",
        "suppressed",
        "cancelled",
    ):
        assert f"'{status}'" in sql

    for active_status in ("pending", "processing", "retry_wait"):
        terminal_array = re.search(
            r"message\.status = any \( array\[(.*?)\]"
            r"::public\.notification_message_status\[\] \)",
            sql,
        )
        assert terminal_array
        assert f"'{active_status}'" not in terminal_array.group(1)

    assert "provider_event.created_at >= v_cutoff" in sql


def test_delivery_item_dedupe_authority_is_deleted_last_and_only_when_safe() -> None:
    sql = compact(migration_sql())

    message_delete = sql.index("delete from public.notification_messages")
    delivery_delete = sql.index("delete from public.notification_delivery_items")
    assert message_delete < delivery_delete

    assert "delivery_item.created_at < v_cutoff" in sql
    assert "delivery_item.available_date < v_today" in sql
    assert (
        "where message_item.delivery_item_id = delivery_item.id"
        in sql
    )


def test_cleanup_privilege_and_result_contract_are_non_pii() -> None:
    sql = compact(migration_sql())

    assert (
        "revoke execute on function "
        "public.cleanup_email_notification_history(integer) "
        "from public, anon, authenticated;"
    ) in sql
    assert (
        "grant execute on function "
        "public.cleanup_email_notification_history(integer) "
        "to service_role;"
    ) in sql

    for key in (
        "deleted_message_count",
        "deleted_message_item_count",
        "deleted_provider_event_count",
        "deleted_delivery_item_count",
    ):
        assert f"'{key}'" in sql

    for forbidden in (
        "user_id",
        "email_address",
        "recipient_email",
        "provider_message_id",
        "provider_event_id",
        "slot_id",
    ):
        return_block = sql.split("return pg_catalog.jsonb_build_object(", 1)[1]
        assert f"'{forbidden}'" not in return_block.split(");", 1)[0]


def test_runbook_keeps_cron_outside_migration_and_uses_off_hours() -> None:
    text = RUNBOOK_PATH.read_text(encoding="utf-8")
    normalized = compact(text.lower())

    assert "90 days" in normalized
    assert "03:17 jst" in normalized
    assert "17 18 * * *" in normalized
    assert "cron job itself is not part of the migration" in normalized
    assert "candidate count" in normalized
    assert "delivery itemとも0" in normalized
    assert "manual cleanupも全削除件数0" in normalized
