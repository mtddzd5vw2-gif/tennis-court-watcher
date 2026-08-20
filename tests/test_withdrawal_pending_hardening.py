from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATION = (
    ROOT
    / "supabase/migrations"
    / "20260820100000_harden_withdrawal_pending_self_access.sql"
)


def read_migration() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def compact(value: str) -> str:
    return " ".join(value.split())


def test_withdrawal_pending_self_read_policies_fail_closed() -> None:
    sql = read_migration()

    assert "drop policy if exists profiles_select_own" in sql
    assert "drop policy if exists terms_acceptances_select_own" in sql

    profiles_policy = sql.split(
        "create policy profiles_select_own", 1
    )[1].split(
        "drop policy if exists terms_acceptances_select_own", 1
    )[0]

    terms_policy = sql.split(
        "create policy terms_acceptances_select_own", 1
    )[1].split(
        "create or replace function public.accept_current_terms()", 1
    )[0]

    assert "(select auth.uid()) = id" in profiles_policy
    assert "(select auth.uid()) = user_id" in terms_policy
    assert "from public.profiles as profile" in terms_policy

    for status in ("pending_terms", "active", "suspended"):
        expected = f"'{status}'::public.membership_status"
        assert expected in profiles_policy
        assert expected in terms_policy

    assert (
        "'withdrawal_pending'::public.membership_status"
        not in profiles_policy
    )
    assert (
        "'withdrawal_pending'::public.membership_status"
        not in terms_policy
    )


def test_terms_acceptance_rejects_withdrawal_pending_before_mutation() -> None:
    sql = compact(read_migration())

    lock = (
        "select profile.membership_status "
        "into current_membership_status "
        "from public.profiles as profile "
        "where profile.id = current_user_id "
        "for update;"
    )
    guard = (
        "if current_membership_status = "
        "'withdrawal_pending'::public.membership_status then"
    )
    insert = "insert into public.terms_acceptances"

    assert lock in sql
    assert guard in sql
    assert "raise exception 'account withdrawal is pending'" in sql
    assert "using errcode = '42501'" in sql
    assert sql.index(lock) < sql.index(guard) < sql.index(insert)


def test_terms_acceptance_preserves_existing_membership_transitions() -> None:
    sql = compact(read_migration())

    assert (
        "membership_status = case "
        "when profile.membership_status = 'pending_terms' "
        "then 'active'::public.membership_status "
        "else profile.membership_status end"
    ) in sql

    assert (
        "revoke execute on function public.accept_current_terms() "
        "from public, anon, authenticated;"
    ) in sql
    assert (
        "grant execute on function public.accept_current_terms() "
        "to authenticated;"
    ) in sql
