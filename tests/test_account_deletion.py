from pathlib import Path


ROOT = Path(__file__).parents[1]
FUNCTION_DIR = ROOT / "supabase/functions/delete-account"
HELPERS = FUNCTION_DIR / "helpers.ts"
INDEX = FUNCTION_DIR / "index.ts"
CONFIG = ROOT / "supabase/config.toml"
ACCOUNT_HTML = ROOT / "account/index.html"
AUTH_SCRIPT = ROOT / "assets/js/auth-foundation.js"
PROFILE_GRANT_MIGRATION = (
    ROOT
    / "supabase/migrations/20260819100000_grant_account_deletion_profile_lock.sql"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_delete_account_function_requires_gateway_user_jwt() -> None:
    config = read(CONFIG)

    assert (
        "[functions.delete-account]\n"
        "verify_jwt = true"
    ) in config


def test_delete_account_request_has_fixed_confirmation_and_bounded_body() -> None:
    helpers = read(HELPERS)

    assert 'const CONFIRMATION_VALUE = "delete-my-account"' in helpers
    assert "export const MAX_JSON_BODY_BYTES = 256" in helpers
    assert "request.body.getReader()" in helpers
    assert "await reader.cancel()" in helpers
    assert 'new TextDecoder("utf-8", { fatal: true })' in helpers
    assert "request.json()" not in helpers
    assert '"user_id"' not in helpers
    assert '"email"' not in helpers
    assert '"https://tenniscourtwatcher.com"' in helpers


def test_delete_account_identity_comes_from_verified_auth_user() -> None:
    index = read(INDEX)

    get_user = index.index("admin.auth.getUser(accessToken)")
    lock_profile = index.index('membership_status: "withdrawal_pending"')
    delete_user = index.index("admin.auth.admin.deleteUser(userId)")

    assert get_user < lock_profile < delete_user
    assert "userResult.data.user.id" in index
    assert '.eq("id", userId)' in index
    helpers = read(HELPERS)
    assert 'dependencies.getEnv("SUPABASE_SERVICE_ROLE_KEY")' in helpers
    assert "request" not in index


def test_account_ui_requires_explicit_second_confirmation() -> None:
    html = read(ACCOUNT_HTML)
    script = read(AUTH_SCRIPT)

    for marker in (
        "data-delete-account-start",
        "data-delete-account-panel",
        "data-delete-account-consent",
        "data-delete-account-confirm",
        "data-delete-account-cancel",
        "data-delete-account-status",
    ):
        assert marker in html

    assert "退会手続きを開く" in html
    assert "退会を実行する" in html
    assert "元に戻せません" in html

    assert 'client.functions.invoke("delete-account"' in script
    assert 'confirmation: "delete-my-account"' in script
    assert "user_id" not in script
    assert "deleteConfirm.disabled" in script


def test_delete_account_service_role_has_only_required_profile_update() -> None:
    migration = read(PROFILE_GRANT_MIGRATION)

    assert "grant update (membership_status)" in migration
    assert "on table public.profiles" in migration
    assert "to service_role;" in migration
    assert "grant update on table public.profiles" not in migration
    assert "to authenticated" not in migration
    assert "to anon" not in migration
