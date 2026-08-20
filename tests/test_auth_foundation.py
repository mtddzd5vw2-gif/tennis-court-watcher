from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from bs4 import BeautifulSoup
from playwright.sync_api import Browser, Playwright, sync_playwright


ROOT = Path(__file__).parents[1]
STATIC_PAGES = (
    Path("auth/login.html"),
    Path("auth/callback.html"),
    Path("account/index.html"),
    Path("account/notifications.html"),
    Path("legal/terms.html"),
    Path("legal/privacy.html"),
)
STATIC_ASSETS = (
    Path("assets/css/auth.css"),
    Path("assets/js/auth-foundation.js"),
    Path("assets/js/notification-rules.js"),
    Path("assets/config/auth-config.example.js"),
    Path("scripts/generate_auth_config.py"),
)
SUPABASE_JS_URL = (
    "https://cdn.jsdelivr.net/npm/"
    "@supabase/supabase-js@2.106.2/dist/umd/supabase.js"
)
MEMBER_MIGRATIONS = tuple(
    (ROOT / "supabase/migrations").glob("*_create_member_profiles.sql")
)
ACCEPT_TERMS_FIX_MIGRATION = (
    ROOT
    / "supabase/migrations"
    / "20260806000000_fix_accept_current_terms_conflict.sql"
)
MOCK_AUTH_CONFIG = """window.TCW_AUTH_CONFIG = Object.freeze({
  supabaseUrl: "https://project.example.supabase.co",
  supabasePublishableKey: "sb_publishable_test_public_only",
  authCallbackUrl: "http://pages.test/project/auth/callback.html",
});
"""
MOCK_SUPABASE_SDK = """
window.supabase = {
  createClient(url, key, options) {
    window.__clientArguments = { url, key, options };
    window.__authCalls = window.__authCalls || [];
    window.__dataCalls = window.__dataCalls || [];
    const mock = window.__mockAuth || {};
    const currentVersion = mock.currentVersion || "2026-08-04-draft";
    const acceptedAt = mock.acceptedAt || "2026-08-04T01:30:00Z";

    function resultFor(table) {
      if (table === "profiles") {
        return {
          data: {
            membership_status: mock.profileStatus || "active",
            latest_terms_version:
              mock.latestTermsVersion === null
                ? null
                : (mock.latestTermsVersion || currentVersion),
            latest_terms_accepted_at:
              mock.latestTermsVersion === null ? null : acceptedAt,
            created_at: "2026-08-04T00:00:00Z",
          },
          error: mock.profileError ? { name: "PostgrestError" } : null,
        };
      }
      if (table === "terms_acceptances") {
        const defaultAcceptances = [{
          document_type: "terms",
          version: currentVersion,
          accepted_at: acceptedAt,
          source: "web",
        }];
        return {
          data: mock.acceptances === undefined
            ? defaultAcceptances
            : mock.acceptances,
          error: mock.acceptancesError ? { name: "PostgrestError" } : null,
        };
      }
      return {
        data: { version: currentVersion, effective_at: "2026-08-04T00:00:00+09:00" },
        error: mock.currentDocumentError ? { name: "PostgrestError" } : null,
      };
    }

    return {
      auth: {
        signInWithOtp(payload) {
          window.__authCalls.push({ method: "signInWithOtp", payload });
          window.sessionStorage.setItem("mock-sign-in-called", "true");
          return new Promise((resolve) => {
            window.setTimeout(
              () => resolve({ error: mock.signInError ? { name: "AuthError" } : null }),
              mock.delay || 0,
            );
          });
        },
        async exchangeCodeForSession(code) {
          window.__authCalls.push({ method: "exchangeCodeForSession" });
          window.sessionStorage.setItem("mock-exchanged-code", code);
          return { error: mock.exchangeError ? { name: "AuthError" } : null };
        },
        async getSession() {
          window.__authCalls.push({ method: "getSession" });
          const sessionLookupCount = Number(
            window.sessionStorage.getItem("mock-session-lookup-count") || "0"
          );
          window.sessionStorage.setItem(
            "mock-session-lookup-count",
            String(sessionLookupCount + 1),
          );
          const form = document.querySelector("[data-auth-form]");
          if (form) {
            window.__formHiddenWhenGetSessionCalled = form.hidden;
          }
          if (mock.sessionDelay) {
            await new Promise((resolve) => {
              window.setTimeout(resolve, mock.sessionDelay);
            });
          }
          return {
            data: {
              session:
                mock.sessionEmail &&
                !window.sessionStorage.getItem("mock-signed-out")
                ? {
                    user: {
                      email: mock.sessionEmail,
                      email_confirmed_at: "2026-08-04T00:00:00Z",
                    },
                  }
                : null,
            },
            error: mock.sessionError ? { name: "AuthError" } : null,
          };
        },
        async signOut(options) {
          window.__authCalls.push({ method: "signOut", options });
          delete mock.sessionEmail;
          window.sessionStorage.setItem("mock-signed-out", "true");
          window.sessionStorage.setItem(
            "mock-sign-out-options",
            JSON.stringify(options),
          );
          return { error: mock.signOutError ? { name: "AuthError" } : null };
        },
      },
      functions: {
        async invoke(name, options) {
          const call = {
            method: "functions.invoke",
            name,
            options,
          };
          window.__authCalls.push(call);
          window.sessionStorage.setItem(
            "mock-function-invoke",
            JSON.stringify(call),
          );
          if (mock.deleteAccountError) {
            return {
              data: null,
              error: { name: "FunctionsHttpError" },
            };
          }
          if (name === "delete-account") {
            delete mock.sessionEmail;
            window.sessionStorage.setItem(
              "mock-account-deleted",
              "true",
            );
          }
          return { data: null, error: null };
        },
      },
      from(table) {
        const call = { table, filters: [] };
        window.__dataCalls.push(call);
        const query = {
          select(columns) {
            call.columns = columns;
            return query;
          },
          eq(column, value) {
            call.filters.push({ column, value });
            return query;
          },
          async single() {
            return resultFor(table);
          },
          async order(column, options) {
            call.order = { column, options };
            return resultFor(table);
          },
        };
        return query;
      },
      async rpc(name) {
        window.__authCalls.push({ method: "rpc", name });
        window.sessionStorage.setItem("mock-rpc-called", name);
        if (mock.rpcError) {
          return { data: null, error: { name: "PostgrestError" } };
        }
        mock.profileStatus = "active";
        mock.latestTermsVersion = currentVersion;
        mock.acceptances = [{
          document_type: "terms",
          version: currentVersion,
          accepted_at: acceptedAt,
          source: "web",
        }];
        return {
          data: [{ version: currentVersion, accepted_at: acceptedAt }],
          error: null,
        };
      },
    };
  },
};
"""


def read(relative_path: Path | str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def playwright_runtime() -> Playwright:
    runtime = sync_playwright().start()
    yield runtime
    runtime.stop()


@pytest.fixture(scope="module")
def browser(playwright_runtime: Playwright) -> Browser:
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    instance = playwright_runtime.chromium.launch(
        headless=True,
        executable_path=executable,
    )
    yield instance
    instance.close()


@pytest.fixture
def auth_page_loader(browser: Browser):
    contexts = []

    def load(path: str, mock: dict | None = None):
        context = browser.new_context()
        contexts.append(context)
        page = context.new_page()
        messages: list[str] = []
        page.on("console", lambda message: messages.append(message.text))
        page.add_init_script(
            script=f"""
              window.__mockAuth = {json.dumps(mock or {})};
              if (
                window.__mockAuth.pendingTerms &&
                !window.sessionStorage.getItem("mock-pending-initialized")
              ) {{
                window.sessionStorage.setItem("tcw.pendingTermsAcceptance", "1");
                window.sessionStorage.setItem("mock-pending-initialized", "1");
              }}
            """
        )

        def route_request(route) -> None:
            parsed = urlsplit(route.request.url)
            if route.request.url == SUPABASE_JS_URL:
                route.fulfill(
                    status=200,
                    content_type="application/javascript",
                    body=MOCK_SUPABASE_SDK,
                )
                return

            relative_path = parsed.path.removeprefix("/project/")
            if relative_path in {
                "auth/login.html",
                "auth/callback.html",
                "account/index.html",
            }:
                route.fulfill(
                    status=200,
                    content_type="text/html",
                    body=read(relative_path),
                )
            elif relative_path == "assets/config/auth-config.js":
                route.fulfill(
                    status=200,
                    content_type="application/javascript",
                    body=MOCK_AUTH_CONFIG,
                )
            elif relative_path == "assets/js/auth-foundation.js":
                route.fulfill(
                    status=200,
                    content_type="application/javascript",
                    body=read(relative_path),
                )
            elif relative_path == "assets/css/auth.css":
                route.fulfill(status=200, content_type="text/css", body="")
            else:
                route.fulfill(status=404, body="not found")

        page.route("**/*", route_request)
        page.goto(f"http://pages.test/project/{path}")
        return page, messages

    yield load
    for context in contexts:
        context.close()


def local_target(source: Path, reference: str) -> Path | None:
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc or reference.startswith(("#", "mailto:", "tel:")):
        return None
    relative_path = unquote(parsed.path)
    if not relative_path:
        return None
    return (source.parent / relative_path).resolve()


def test_required_static_pages_and_assets_exist() -> None:
    for relative_path in (*STATIC_PAGES, *STATIC_ASSETS):
        path = ROOT / relative_path
        assert path.is_file(), f"Missing static foundation file: {relative_path}"
        assert path.stat().st_size > 0


def test_local_auth_config_is_ignored_but_sample_is_committable() -> None:
    ignored = (
        ".env",
        ".env.local",
        "assets/config/auth-config.js",
        "assets/config/development.local.js",
        "private-service-role.json",
        "authentication.key",
    )
    for relative_path in ignored:
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", relative_path],
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == 0, f"{relative_path} should be ignored"

    sample = subprocess.run(
        ["git", "check-ignore", "--quiet", "assets/config/auth-config.example.js"],
        cwd=ROOT,
        check=False,
    )
    assert sample.returncode == 1


def test_static_page_links_and_assets_resolve_inside_repository() -> None:
    for relative_page in STATIC_PAGES:
        page = ROOT / relative_page
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
        references = [
            element.get(attribute)
            for element, attribute in (
                *((element, "href") for element in soup.find_all(href=True)),
                *((element, "src") for element in soup.find_all(src=True)),
            )
        ]

        assert references, f"No links found in {relative_page}"
        for reference in references:
            target = local_target(page, reference)
            if target is None:
                continue
            assert target.is_relative_to(ROOT.resolve())
            if target == (ROOT / "assets/config/auth-config.js").resolve():
                assert subprocess.run(
                    ["git", "check-ignore", "--quiet", str(target.relative_to(ROOT))],
                    cwd=ROOT,
                    check=False,
                ).returncode == 0
                continue
            assert target.exists(), f"{relative_page}: broken link {reference}"


def test_project_markdown_local_links_resolve() -> None:
    markdown_link = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")

    for markdown_file in (ROOT / "README.md", *(ROOT / "docs").glob("*.md")):
        content = markdown_file.read_text(encoding="utf-8")
        for raw_target in markdown_link.findall(content):
            target_text = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            target = local_target(markdown_file, target_text)
            if target is None:
                continue
            assert target.is_relative_to(ROOT.resolve())
            assert target.exists(), f"{markdown_file.name}: broken link {target_text}"


def test_auth_pages_load_only_pinned_supabase_sdk_and_do_not_submit_forms() -> None:
    for relative_page in STATIC_PAGES:
        soup = BeautifulSoup(read(relative_page), "html.parser")
        for script in soup.find_all("script", src=True):
            if urlsplit(script["src"]).scheme:
                assert script["src"] == SUPABASE_JS_URL
        for form in soup.find_all("form"):
            assert not form.get("action")

    login = BeautifulSoup(read("auth/login.html"), "html.parser")
    assert login.find("input", {"type": "email"})
    consent = login.find("input", {"name": "terms-consent"})
    assert consent.has_attr("disabled")
    assert not consent.has_attr("required")
    mode_buttons = login.find_all("button", {"data-auth-mode": True})
    assert [button["data-auth-mode"] for button in mode_buttons] == [
        "login",
        "signup",
    ]
    assert [button["aria-pressed"] for button in mode_buttons] == ["true", "false"]
    assert login.find("button", {"type": "submit"}).has_attr("disabled")
    assert login.find("form", {"data-auth-form": True}).has_attr("hidden")
    session_status = login.find(attrs={"data-login-session-status": True})
    assert session_status["aria-live"] == "polite"
    assert login.find("noscript")
    login_text = login.get_text(" ", strip=True)
    assert "Phase" not in login_text
    assert "課金" not in login_text


def test_auth_script_uses_pkce_and_does_not_log_credentials() -> None:
    callback = BeautifulSoup(read("auth/callback.html"), "html.parser")
    script = read("assets/js/auth-foundation.js")

    assert callback.body["data-page"] == "auth-callback"
    assert callback.find("meta", {"name": "referrer"})["content"] == "no-referrer"
    assert "history.replaceState" in script
    assert 'flowType: "pkce"' in script
    assert "persistSession: true" in script
    assert "autoRefreshToken: true" in script
    assert "signInWithOtp" in script
    assert "exchangeCodeForSession" in script
    assert "getSession" in script
    assert 'signOut({ scope: "local" })' in script
    assert "localStorage" not in script
    assert "access_token" not in script.lower()
    assert "refresh_token" not in script.lower()
    assert "console." not in script
    assert "fetch(" not in script


def test_member_pages_hide_internal_status_and_development_language() -> None:
    login = BeautifulSoup(read("auth/login.html"), "html.parser")
    account = BeautifulSoup(read("account/index.html"), "html.parser")
    script = read("assets/js/auth-foundation.js")

    login_text = login.get_text(" ", strip=True)
    assert "Phase" not in login_text
    assert "課金" not in login_text
    assert login.find(attrs={"data-auth-mode": "login"})
    assert login.find(attrs={"data-auth-mode": "signup"})
    assert "shouldCreateUser: requestMode === \"signup\"" in script

    account_text = account.get_text(" ", strip=True)
    assert "アカウント状態" not in account_text
    assert "会員登録日時" not in account_text
    assert "最新の規約同意日時" not in account_text
    assert not account.find(id="terms-history-title")
    assert account.find(attrs={"data-account-email": True})
    for selector in (
        "data-account-email-verified",
        "data-membership-status",
        "data-account-created-at",
        "data-latest-terms-version",
        "data-latest-terms-accepted-at",
        "data-terms-history",
    ):
        assert not account.find(attrs={selector: True})


def test_legal_pages_are_explicitly_drafts_requiring_review() -> None:
    for relative_page in (Path("legal/terms.html"), Path("legal/privacy.html")):
        text = BeautifulSoup(read(relative_page), "html.parser").get_text(" ", strip=True)
        assert "暫定案" in text
        assert "一般公開前" in text
        assert "内容確認が必要" in text


def test_member_profile_migration_defines_required_tables_and_data_minimization() -> None:
    assert len(MEMBER_MIGRATIONS) == 1
    migration = MEMBER_MIGRATIONS[0].read_text(encoding="utf-8").lower()
    profiles = re.search(
        r"create table public\.profiles\s*\((.*?)\n\);",
        migration,
        re.DOTALL,
    )

    assert profiles
    assert "references auth.users(id) on delete cascade" in profiles.group(1)
    assert "membership_status" in profiles.group(1)
    assert "latest_terms_version" in profiles.group(1)
    assert "latest_terms_accepted_at" in profiles.group(1)
    assert not re.search(r"\bemail\b", profiles.group(1))
    for table in (
        "legal_document_versions",
        "profiles",
        "terms_acceptances",
    ):
        assert f"create table public.{table}" in migration
        assert f"alter table public.{table} enable row level security" in migration


def test_member_profile_migration_enforces_append_only_history_and_rls_grants() -> None:
    migration = MEMBER_MIGRATIONS[0].read_text(encoding="utf-8").lower()

    assert "accepted_at timestamptz not null default now()" in migration
    assert "unique (user_id, document_type, version)" in migration
    assert "terms_acceptances_select_own" in migration
    assert "(select auth.uid()) = user_id" in migration
    assert "(select auth.uid()) = id" in migration
    assert not re.search(
        r"create policy .*?on public\.terms_acceptances\s+for "
        r"(insert|update|delete|all)",
        migration,
        re.DOTALL,
    )
    assert re.search(
        r"revoke all privileges on table.*?from public, anon, authenticated",
        migration,
        re.DOTALL,
    )
    assert re.search(
        r"grant select on table.*?to authenticated",
        migration,
        re.DOTALL,
    )
    assert not re.search(
        r"grant\s+(insert|update|delete|all).*?to authenticated",
        migration,
        re.DOTALL,
    )


def test_accept_current_terms_rpc_uses_database_identity_version_and_time() -> None:
    migration = MEMBER_MIGRATIONS[0].read_text(encoding="utf-8").lower()
    rpc = re.search(
        r"create function public\.accept_current_terms\(\)(.*?)\n\$\$;",
        migration,
        re.DOTALL,
    )

    assert rpc
    body = rpc.group(1)
    assert "auth.uid()" in body
    assert "current_user_id uuid" in body
    assert "public.legal_document_versions" in body
    assert "document.is_current" in body
    assert "insert into public.terms_acceptances" in body
    assert "update public.profiles" in body
    assert "on conflict (user_id, document_type, version) do nothing" in body
    assert "accepted_at" not in re.search(
        r"insert into public\.terms_acceptances\s*\((.*?)\)",
        body,
        re.DOTALL,
    ).group(1)
    assert re.search(
        r"revoke all on function public\.accept_current_terms\(\)"
        r".*?from public, anon, authenticated",
        migration,
        re.DOTALL,
    )
    assert re.search(
        r"grant execute on function public\.accept_current_terms\(\)"
        r"\s*to authenticated",
        migration,
    )


def accept_current_terms_fix_rpc() -> str:
    migration = ACCEPT_TERMS_FIX_MIGRATION.read_text(encoding="utf-8").lower()
    rpc = re.search(
        r"create or replace function public\.accept_current_terms\(\)"
        r"(.*?)\n\$\$;",
        migration,
        re.DOTALL,
    )
    assert rpc
    return rpc.group(1)


def test_accept_current_terms_conflict_fix_migration_exists() -> None:
    assert ACCEPT_TERMS_FIX_MIGRATION.is_file()
    assert ACCEPT_TERMS_FIX_MIGRATION.stat().st_size > 0


def test_accept_current_terms_fix_names_conflict_constraint() -> None:
    body = accept_current_terms_fix_rpc()

    assert (
        "on conflict on constraint "
        "terms_acceptances_user_document_version_key"
    ) in body
    assert re.search(
        r"on conflict on constraint "
        r"terms_acceptances_user_document_version_key\s+do nothing",
        body,
    )


def test_accept_current_terms_fix_avoids_ambiguous_conflict_columns() -> None:
    body = accept_current_terms_fix_rpc()

    assert "returns table" in body
    assert re.search(r"\bversion text\b", body)
    assert not re.search(
        r"on conflict\s*\(\s*user_id\s*,\s*document_type\s*,\s*version\s*\)",
        body,
    )


def test_accept_current_terms_fix_preserves_security_and_rpc_contract() -> None:
    migration = ACCEPT_TERMS_FIX_MIGRATION.read_text(encoding="utf-8").lower()
    body = accept_current_terms_fix_rpc()

    for expected in (
        "security definer",
        "set search_path = ''",
        "auth.uid()",
        "public.legal_document_versions",
        "document.is_current",
        "insert into public.terms_acceptances",
        "update public.profiles",
        "member profile is unavailable",
        "select current_terms_version, recorded_accepted_at",
    ):
        assert expected in body
    insert_columns = re.search(
        r"insert into public\.terms_acceptances\s*\((.*?)\)",
        body,
        re.DOTALL,
    )
    assert insert_columns
    assert "accepted_at" not in insert_columns.group(1)
    assert re.search(
        r"revoke execute on function public\.accept_current_terms\(\)"
        r"\s*from public, anon, authenticated",
        migration,
    )
    assert re.search(
        r"grant execute on function public\.accept_current_terms\(\)"
        r"\s*to authenticated",
        migration,
    )


def accept_current_terms_profile_update() -> str:
    migration = ACCEPT_TERMS_FIX_MIGRATION.read_text(encoding="utf-8").lower()
    update = re.search(
        r"update public\.profiles as profile\s+set\s+(.*?)"
        r"\s+where profile\.id = current_user_id;",
        migration,
        re.DOTALL,
    )
    assert update
    return " ".join(update.group(1).split())


def test_accept_current_terms_activates_pending_terms_profile_only() -> None:
    update = accept_current_terms_profile_update()

    assert (
        "membership_status = case "
        "when profile.membership_status = 'pending_terms' "
        "then 'active'::public.membership_status "
        "else profile.membership_status end"
    ) in update
    assert not re.search(r"membership_status\s*=\s*'active'\s*,", update)


def test_accept_current_terms_does_not_activate_suspended_profile() -> None:
    update = accept_current_terms_profile_update()

    assert "else profile.membership_status end" in update
    assert not re.search(
        r"when profile\.membership_status = 'suspended'\s+"
        r"then 'active'",
        update,
    )


def test_accept_current_terms_does_not_activate_withdrawal_pending_profile() -> None:
    update = accept_current_terms_profile_update()

    assert "else profile.membership_status end" in update
    assert not re.search(
        r"when profile\.membership_status = 'withdrawal_pending'\s+"
        r"then 'active'",
        update,
    )


def test_accept_current_terms_keeps_active_profile_active() -> None:
    update = accept_current_terms_profile_update()

    assert "else profile.membership_status end" in update
    assert not re.search(
        r"when profile\.membership_status = 'active'\s+then",
        update,
    )


def test_auth_profile_trigger_and_backfill_do_not_infer_terms_acceptance() -> None:
    migration = MEMBER_MIGRATIONS[0].read_text(encoding="utf-8").lower()
    backfill = re.search(
        r"-- existing auth users.*?"
        r"(insert into public\.profiles.*?on conflict \(id\) do nothing;)",
        migration,
        re.DOTALL,
    )

    assert "create function public.create_profile_for_new_auth_user()" in migration
    assert "create trigger create_profile_after_auth_user_insert" in migration
    assert "after insert on auth.users" in migration
    assert "values (new.id)" in migration
    assert backfill
    assert "terms_acceptances" not in backfill.group(1)
    assert "'active'" not in backfill.group(1)


def test_public_config_sample_has_only_expected_empty_values() -> None:
    config = read("assets/config/auth-config.example.js")
    entries = dict(
        re.findall(r"^\s{2}([A-Za-z][A-Za-z0-9]*):\s*\"([^\"]*)\",$", config, re.MULTILINE)
    )

    assert entries == {
        "supabaseUrl": "",
        "supabasePublishableKey": "",
        "authCallbackUrl": "",
    }
    assert "publishable key" in config
    assert "service role key" in config
    assert "Never put" in config


def run_config_generator(
    tmp_path: Path,
    overrides: dict[str, str | None] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    environment = os.environ.copy()
    environment.update(
        {
            "SUPABASE_URL": "https://project.example.supabase.co",
            "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_test_public_only",
            "AUTH_CALLBACK_URL": (
                "http://localhost:8765/auth/callback.html"
            ),
        }
    )
    for name, value in (overrides or {}).items():
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = value

    output = tmp_path / "auth-config.js"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/generate_auth_config.py"),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    return result, output


def test_auth_config_generator_escapes_javascript_strings(tmp_path: Path) -> None:
    publishable_key = 'sb_publishable_test"</script>&\u2028tail'
    result, output = run_config_generator(
        tmp_path,
        {"SUPABASE_PUBLISHABLE_KEY": publishable_key},
    )

    assert result.returncode == 0, result.stderr
    config = output.read_text(encoding="utf-8")
    assert publishable_key not in config
    assert "</script>" not in config
    assert "\\u003c/script\\u003e\\u0026\\u2028" in config
    assert set(
        re.findall(r"^\s{2}([A-Za-z][A-Za-z0-9]*):", config, re.MULTILINE)
    ) == {"supabaseUrl", "supabasePublishableKey", "authCallbackUrl"}


@pytest.mark.parametrize("missing_name", (
    "SUPABASE_URL",
    "SUPABASE_PUBLISHABLE_KEY",
    "AUTH_CALLBACK_URL",
))
def test_auth_config_generator_fails_when_required_value_is_missing(
    tmp_path: Path,
    missing_name: str,
) -> None:
    result, output = run_config_generator(tmp_path, {missing_name: "  "})

    assert result.returncode == 1
    assert missing_name in result.stderr
    assert not output.exists()


@pytest.mark.parametrize(
    "forbidden_key",
    (
        "sb_secret_test_not_a_real_key",
        "test-service_role-key",
    ),
)
def test_auth_config_generator_rejects_secret_key_markers(
    tmp_path: Path,
    forbidden_key: str,
) -> None:
    result, output = run_config_generator(
        tmp_path,
        {"SUPABASE_PUBLISHABLE_KEY": forbidden_key},
    )

    assert result.returncode == 1
    assert "secret/service-role" in result.stderr
    assert not output.exists()


def test_auth_config_generator_rejects_service_role_jwt(tmp_path: Path) -> None:
    def encode(value: dict[str, object]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    fake_jwt = f"{encode({'alg': 'none'})}.{encode({'role': 'service_role'})}.test"
    result, output = run_config_generator(
        tmp_path,
        {"SUPABASE_PUBLISHABLE_KEY": fake_jwt},
    )

    assert result.returncode == 1
    assert "service-role JWT" in result.stderr
    assert not output.exists()


def test_auth_config_generator_rejects_non_publishable_value(tmp_path: Path) -> None:
    result, output = run_config_generator(
        tmp_path,
        {"SUPABASE_PUBLISHABLE_KEY": "database-password-not-a-public-key"},
    )

    assert result.returncode == 1
    assert "publishable key or legacy anon JWT" in result.stderr
    assert not output.exists()


def test_static_foundation_contains_no_credential_like_values() -> None:
    files = [ROOT / path for path in (*STATIC_PAGES, *STATIC_ASSETS)]
    forbidden_value_patterns = {
        "JWT": re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),
        "Supabase secret key": re.compile(r"\bsb_secret_[A-Za-z0-9_-]{10,}"),
        "Supabase service role JWT assignment": re.compile(
            r"service[_ -]?role(?:_key)?\s*[:=]\s*[\"'][^\"']{8,}",
            re.IGNORECASE,
        ),
        "real Supabase project URL": re.compile(
            r"https://[a-z0-9]{8,}\.supabase\.co", re.IGNORECASE
        ),
        "private key material": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    }

    for path in files:
        content = path.read_text(encoding="utf-8")
        for label, pattern in forbidden_value_patterns.items():
            assert not pattern.search(content), f"{label} found in {path.relative_to(ROOT)}"


def test_pages_workflow_publishes_phase_zero_and_auth_foundation() -> None:
    workflow = read(".github/workflows/update-availability.yml")

    for artifact_path in ("index.html", "auth", "account", "legal", "assets"):
        assert re.search(rf"^\s{{12}}{re.escape(artifact_path)}$", workflow, re.MULTILINE)

    assert "cp run-artifact/index.html _site/index.html" in workflow
    assert (
        "cp run-artifact/run-output/availability.json "
        "_site/data/availability.json"
    ) in workflow
    for directory in ("auth", "account", "legal", "assets"):
        assert f"cp -R run-artifact/{directory} _site/{directory}" in workflow

    for variable in (
        "vars.SUPABASE_URL",
        "vars.SUPABASE_PUBLISHABLE_KEY",
        "vars.AUTH_CALLBACK_URL",
    ):
        assert variable in workflow
    assert "python scripts/generate_auth_config.py" in workflow
    assert "--output _site/assets/config/auth-config.js" in workflow
    assert "python scripts/scrape.py" in workflow
    assert "LINE_CHANNEL_ACCESS_TOKEN" not in workflow
    assert "LINE_USER_ID" not in workflow
    assert "notification-state.json" not in workflow


def test_existing_phase_zero_page_and_public_json_contract_remain() -> None:
    index = read("index.html")
    workflow = read(".github/workflows/update-availability.yml")

    assert "data/availability.json" in index
    assert "id=\"page-utils\"" in index
    assert "data/availability.json" in workflow
    assert (ROOT / "data/availability.json").is_file()
    assert not (ROOT / "data/notification-state.json").exists()


def test_login_magic_link_validates_input_prevents_duplicates_and_is_neutral(
    auth_page_loader,
) -> None:
    page, messages = auth_page_loader(
        "auth/login.html",
        {"delay": 30},
    )
    email = page.locator('input[name="email"]')
    submit = page.locator('button[type="submit"]')

    assert submit.is_disabled()
    email.fill("not-an-email")
    assert submit.is_disabled()

    email.fill("member@example.test")
    assert submit.is_enabled()
    page.locator("form").evaluate(
        """form => {
          form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
          form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
        }"""
    )
    page.locator('[data-form-status][data-state="success"]').wait_for()

    calls = page.evaluate("window.__authCalls")
    assert calls == [
        {"method": "getSession"},
        {
            "method": "signInWithOtp",
            "payload": {
                "email": "member@example.test",
                "options": {
                    "emailRedirectTo": (
                        "http://pages.test/project/auth/callback.html"
                    ),
                    "shouldCreateUser": False,
                },
            },
        }
    ]
    status = page.locator("[data-form-status]").inner_text()
    assert "メールを確認してください" in status
    assert "member@example.test" not in status
    assert messages == []
    stored = page.evaluate(
        """Object.fromEntries(
      Object.entries(sessionStorage).filter(([key]) => key.startsWith("tcw."))
        )"""
    )
    assert stored == {}
    assert "member@example.test" not in json.dumps(stored)

    arguments = page.evaluate("window.__clientArguments")
    assert arguments["url"] == "https://project.example.supabase.co"
    assert arguments["key"] == "sb_publishable_test_public_only"
    assert arguments["options"]["auth"] == {
        "flowType": "pkce",
        "persistSession": True,
        "autoRefreshToken": True,
        "detectSessionInUrl": False,
    }


def test_signup_requires_terms_consent_and_marks_pending_acceptance(
    auth_page_loader,
) -> None:
    page, messages = auth_page_loader("auth/login.html")
    page.locator('[data-auth-mode="signup"]').click()

    email = page.locator('input[name="email"]')
    consent = page.locator('input[name="terms-consent"]')
    submit = page.locator('button[type="submit"]')

    assert page.locator("[data-signup-consent]").is_visible()
    assert consent.is_enabled()
    assert submit.inner_text() == "会員登録用リンクを送る"
    email.fill("new-member@example.test")
    assert submit.is_disabled()
    consent.check()
    assert submit.is_enabled()
    submit.click()
    page.locator('[data-form-status][data-state="success"]').wait_for()

    assert page.evaluate("window.__authCalls") == [
        {"method": "getSession"},
        {
            "method": "signInWithOtp",
            "payload": {
                "email": "new-member@example.test",
                "options": {
                    "emailRedirectTo": (
                        "http://pages.test/project/auth/callback.html"
                    ),
                    "shouldCreateUser": True,
                },
            },
        },
    ]
    assert page.evaluate(
        'window.sessionStorage.getItem("tcw.pendingTermsAcceptance")'
    ) == "1"
    assert messages == []


def test_login_checks_session_before_revealing_form_and_shows_it_when_absent(
    auth_page_loader,
) -> None:
    page, messages = auth_page_loader(
        "auth/login.html",
        {"sessionDelay": 500},
    )
    form = page.locator("[data-auth-form]")

    assert form.is_hidden()
    assert page.evaluate("window.__authCalls") == [{"method": "getSession"}]
    assert page.evaluate("window.__formHiddenWhenGetSessionCalled") is True
    assert "ログイン状態を確認しています" in page.locator(
        "[data-login-session-status]"
    ).inner_text()

    form.wait_for(state="visible")
    assert page.locator("[data-login-session-status]").is_hidden()
    assert messages == []


def test_login_with_existing_session_replaces_route_without_sending_magic_link(
    auth_page_loader,
) -> None:
    page, messages = auth_page_loader(
        "auth/login.html",
        {"sessionEmail": "member@example.test"},
    )

    page.wait_for_function(
        """() => document.querySelector("[data-login-session-status]")
          .textContent.includes("ログイン済みです")"""
    )
    assert page.locator("[data-auth-form]").is_hidden()
    page.wait_for_url("http://pages.test/project/account/index.html")
    page.locator("[data-account-content]:not([hidden])").wait_for()

    assert page.evaluate(
        'window.sessionStorage.getItem("mock-session-lookup-count")'
    ) == "2"
    assert page.evaluate(
        'window.sessionStorage.getItem("mock-sign-in-called")'
    ) is None
    assert "window.location.replace(ACCOUNT_PATH)" in read(
        "assets/js/auth-foundation.js"
    )
    assert messages == []


def test_login_session_check_failure_keeps_magic_link_form_usable(
    auth_page_loader,
) -> None:
    page, messages = auth_page_loader(
        "auth/login.html",
        {"sessionError": True},
    )
    form = page.locator("[data-auth-form]")
    form.wait_for(state="visible")

    status = page.locator('[data-login-session-status][data-state="error"]')
    assert "ログイン状態を確認できませんでした" in status.inner_text()
    page.locator('input[name="email"]').fill("member@example.test")
    page.locator('button[type="submit"]').click()
    page.locator('[data-form-status][data-state="success"]').wait_for()

    methods = [call["method"] for call in page.evaluate("window.__authCalls")]
    assert methods == ["getSession", "signInWithOtp"]
    assert messages == []


def test_pkce_callback_exchanges_code_scrubs_url_and_opens_account(
    auth_page_loader,
) -> None:
    page, messages = auth_page_loader(
        "auth/callback.html?code=one-time-code&unexpected=value",
        {"sessionEmail": "member@example.test"},
    )

    page.wait_for_url("http://pages.test/project/account/index.html")
    page.locator("[data-account-content]:not([hidden])").wait_for()

    assert page.url == "http://pages.test/project/account/index.html"
    assert page.evaluate(
        'window.sessionStorage.getItem("mock-exchanged-code")'
    ) == "one-time-code"
    assert page.locator("[data-account-email]").inner_text() == "member@example.test"
    assert messages == []


def test_pkce_callback_failure_scrubs_url_and_shows_login_route(
    auth_page_loader,
) -> None:
    page, messages = auth_page_loader(
        "auth/callback.html?code=expired-code#token=must-not-remain",
        {"exchangeError": True},
    )

    page.locator('[data-callback-status][data-state="error"]').wait_for()
    assert page.url == "http://pages.test/project/auth/callback.html"
    assert page.locator("[data-callback-retry]").is_visible()
    assert "expired-code" not in page.locator("body").inner_text()
    assert messages == []


def test_pkce_callback_accepts_terms_only_when_same_browser_marker_exists(
    auth_page_loader,
) -> None:
    page, messages = auth_page_loader(
        "auth/callback.html?code=one-time-code",
        {
            "sessionEmail": "member@example.test",
            "pendingTerms": True,
        },
    )

    page.wait_for_url("http://pages.test/project/account/index.html")
    page.locator("[data-account-content]:not([hidden])").wait_for()
    assert page.evaluate(
        'window.sessionStorage.getItem("mock-rpc-called")'
    ) == "accept_current_terms"
    assert page.evaluate(
        'window.sessionStorage.getItem("tcw.pendingTermsAcceptance")'
    ) is None
    assert messages == []


def test_pkce_callback_keeps_session_when_terms_rpc_fails(
    auth_page_loader,
) -> None:
    page, messages = auth_page_loader(
        "auth/callback.html?code=one-time-code",
        {
            "sessionEmail": "member@example.test",
            "pendingTerms": True,
            "rpcError": True,
            "profileStatus": "pending_terms",
            "latestTermsVersion": None,
            "acceptances": [],
        },
    )

    page.wait_for_url("http://pages.test/project/account/index.html")
    page.locator("[data-terms-consent-panel]:not([hidden])").wait_for()
    assert page.locator("[data-account-email]").inner_text() == "member@example.test"
    assert page.evaluate(
        'window.sessionStorage.getItem("tcw.pendingTermsAcceptance")'
    ) == "1"
    assert messages == []


def test_account_checks_session_displays_email_and_signs_out(
    auth_page_loader,
) -> None:
    page, messages = auth_page_loader(
        "account/index.html",
        {"sessionEmail": "member@example.test"},
    )
    page.locator("[data-account-content]:not([hidden])").wait_for()

    assert page.locator("[data-account-email]").inner_text() == "member@example.test"
    assert page.locator("[data-sign-out]").is_enabled()
    assert page.locator("[data-delete-account-start]").is_enabled()
    assert page.locator("[data-delete-account-panel]").is_hidden()

    page.locator("[data-sign-out]").click()
    page.wait_for_url("http://pages.test/project/auth/login.html")
    assert page.evaluate(
        'window.sessionStorage.getItem("mock-signed-out")'
    ) == "true"
    assert page.evaluate(
        'JSON.parse(window.sessionStorage.getItem("mock-sign-out-options"))'
    ) == {"scope": "local"}
    assert messages == []


def test_account_deletion_requires_two_stage_confirmation_and_calls_edge_function(
    auth_page_loader,
) -> None:
    page, messages = auth_page_loader(
        "account/index.html",
        {"sessionEmail": "member@example.test"},
    )
    page.locator("[data-account-content]:not([hidden])").wait_for()

    start = page.locator("[data-delete-account-start]")
    panel = page.locator("[data-delete-account-panel]")
    consent = page.locator("[data-delete-account-consent]")
    confirm = page.locator("[data-delete-account-confirm]")

    assert panel.is_hidden()
    start.click()
    assert panel.is_visible()
    assert confirm.is_disabled()

    consent.check()
    assert confirm.is_enabled()
    confirm.click()

    page.wait_for_url("http://pages.test/project/auth/login.html")
    page.locator("[data-auth-form]:not([hidden])").wait_for()

    function_call = page.evaluate(
        'JSON.parse(window.sessionStorage.getItem("mock-function-invoke"))'
    )
    assert function_call == {
        "method": "functions.invoke",
        "name": "delete-account",
        "options": {
            "body": {
                "confirmation": "delete-my-account",
            },
        },
    }
    serialized = json.dumps(function_call)
    assert "user_id" not in serialized
    assert "member@example.test" not in serialized
    assert page.evaluate(
        'window.sessionStorage.getItem("mock-account-deleted")'
    ) == "true"
    assert messages == []


def test_account_deletion_failure_stays_signed_in_and_allows_retry(
    auth_page_loader,
) -> None:
    page, messages = auth_page_loader(
        "account/index.html",
        {
            "sessionEmail": "member@example.test",
            "deleteAccountError": True,
        },
    )
    page.locator("[data-account-content]:not([hidden])").wait_for()

    page.locator("[data-delete-account-start]").click()
    page.locator("[data-delete-account-consent]").check()
    page.locator("[data-delete-account-confirm]").click()

    error = page.locator(
        '[data-delete-account-status][data-state="error"]'
    )
    error.wait_for()

    assert page.url == "http://pages.test/project/account/index.html"
    assert "退会処理を完了できませんでした" in error.inner_text()
    assert page.locator("[data-delete-account-confirm]").is_enabled()
    assert page.locator("[data-delete-account-cancel]").is_enabled()
    assert page.locator("[data-sign-out]").is_enabled()
    assert page.evaluate(
        'window.sessionStorage.getItem("mock-account-deleted")'
    ) is None
    assert messages == []

def test_pending_terms_account_requires_explicit_consent_and_refreshes(
    auth_page_loader,
) -> None:
    page, messages = auth_page_loader(
        "account/index.html",
        {
            "sessionEmail": "member@example.test",
            "profileStatus": "pending_terms",
            "latestTermsVersion": None,
            "acceptances": [],
        },
    )
    panel = page.locator("[data-terms-consent-panel]")
    panel.wait_for(state="visible")
    button = page.locator("[data-accept-current-terms]")

    assert button.is_disabled()
    page.locator("[data-account-terms-consent]").check()
    assert button.is_enabled()
    button.click()
    page.locator('[data-action-status][data-state="success"]').wait_for()

    assert panel.is_hidden()
    assert "利用規約への同意を登録しました" in page.locator(
        '[data-action-status][data-state="success"]'
    ).inner_text()
    assert messages == []


def test_active_account_uses_rls_queries_without_another_user_id(
    auth_page_loader,
) -> None:
    page, messages = auth_page_loader(
        "account/index.html",
        {"sessionEmail": "member@example.test"},
    )
    page.locator("[data-account-loading]").wait_for(state="hidden")

    assert page.locator("[data-account-email]").inner_text() == (
        "member@example.test"
    )
    for selector in (
        "[data-account-email-verified]",
        "[data-membership-status]",
        "[data-account-created-at]",
        "[data-latest-terms-version]",
        "[data-latest-terms-accepted-at]",
        "[data-terms-history]",
    ):
        assert page.locator(selector).count() == 0
    assert page.locator("[data-terms-consent-panel]").is_hidden()
    data_calls = page.evaluate("window.__dataCalls")
    member_calls = [
        call for call in data_calls
        if call["table"] in {"profiles", "terms_acceptances"}
    ]
    assert member_calls
    assert all(
        filter_item["column"] not in {"id", "user_id"}
        for call in member_calls
        for filter_item in call["filters"]
    )
    profile_call = next(call for call in data_calls if call["table"] == "profiles")
    terms_call = next(
        call for call in data_calls if call["table"] == "terms_acceptances"
    )
    assert profile_call["columns"] == "membership_status"
    assert terms_call["columns"] == "document_type,version"
    assert messages == []


def test_account_profile_failure_is_generalized_and_keeps_logout_available(
    auth_page_loader,
) -> None:
    page, messages = auth_page_loader(
        "account/index.html",
        {
            "sessionEmail": "member@example.test",
            "profileError": True,
        },
    )
    page.locator('[data-account-loading][data-state="error"]').wait_for()

    assert "会員情報を取得できませんでした" in page.locator(
        "[data-account-loading]"
    ).inner_text()
    assert page.locator("[data-sign-out]").is_enabled()
    assert "member@example.test" not in page.locator(
        "[data-account-loading]"
    ).inner_text()
    assert messages == []


def test_account_redirects_to_login_without_session(auth_page_loader) -> None:
    page, messages = auth_page_loader("account/index.html")

    page.wait_for_url("http://pages.test/project/auth/login.html")
    assert messages == []
