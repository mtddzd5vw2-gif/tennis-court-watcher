from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml


MIGRATION_PATH = Path(
    "supabase/migrations/20260813000000_add_update_availability_watchdog.sql"
)
FUNCTION_DIR = Path("supabase/functions/update-availability-watchdog")
CONFIG_PATH = Path("supabase/config.toml")
WORKFLOW_PATH = Path(".github/workflows/update-availability.yml")
RUNBOOK_PATH = Path("docs/PHASE3_SCHEDULER_WATCHDOG.md")


def migration_text() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def workflow_triggers(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True, {}))


def sql_function(name: str) -> str:
    match = re.search(
        rf"create function public\.{re.escape(name)}\b(?P<body>.*?)\n\$\$;",
        migration_text(),
        re.DOTALL,
    )
    assert match is not None
    return match.group("body")


def test_watchdog_state_contains_required_operational_fields() -> None:
    sql = migration_text()
    for field in (
        "watchdog_name",
        "last_snapshot_at",
        "observation_token",
        "snapshot_outcome",
        "active_run_count",
        "latest_live_run_created_at",
        "latest_live_success_at",
        "latest_live_failure_at",
        "consecutive_failure_count",
        "claim_token",
        "claimed_at",
        "claim_expires_at",
        "dispatch_cooldown_until",
        "dispatch_confirmed_at",
        "last_dispatch_attempt_at",
        "last_dispatch_accepted_at",
        "last_dispatched_workflow_run_id",
        "last_outcome",
        "check_count",
        "github_api_error_count",
        "dispatch_attempt_count",
        "dispatch_accepted_count",
        "updated_at",
    ):
        assert re.search(rf"^\s*{field}\b", sql, re.MULTILINE)
    assert "watchdog_name text primary key" in sql
    assert "watchdog_name = 'update-availability'" in sql


def test_claim_is_one_atomic_conditional_update_without_cooldown() -> None:
    body = sql_function("claim_update_availability_fallback")

    assert "return query\n  update public.update_availability_watchdog_state" in body
    assert "state.observation_token = p_observation_token" in body
    assert "state.snapshot_outcome = 'stale'" in body
    assert "state.active_run_count = 0" in body
    assert "state.last_snapshot_at is not null" in body
    assert "state.last_snapshot_at >= v_now - interval '2 minutes'" in body
    assert "state.last_snapshot_at <= v_now + interval '2 minutes'" in body
    assert "state.latest_live_run_created_at is not null" in body
    assert "state.latest_live_run_created_at is null" not in body
    assert "interval '45 minutes'" in body
    assert "interval '5 minutes'" in body
    assert "dispatch_cooldown_until =" not in body
    assert "for update" not in body.lower()


def test_confirm_starts_cooldown_only_after_second_observation() -> None:
    body = sql_function("confirm_update_availability_fallback")

    assert "state.observation_token = p_observation_token" in body
    assert "state.claim_token = p_claim_token" in body
    assert "state.claim_expires_at > v_now" in body
    assert "state.last_snapshot_at is not null" in body
    assert "state.last_snapshot_at >= v_now - interval '2 minutes'" in body
    assert "state.last_snapshot_at <= v_now + interval '2 minutes'" in body
    assert "state.latest_live_run_created_at is not null" in body
    assert "state.latest_live_run_created_at is null" not in body
    assert "dispatch_cooldown_until = v_now + interval '30 minutes'" in body
    assert "dispatch_attempt_count = state.dispatch_attempt_count + 1" in body
    assert "update_availability_watchdog_in_service_window" in body


def test_rpc_security_and_state_access_are_service_only() -> None:
    sql = migration_text()
    for function_name in (
        "record_update_availability_watchdog_snapshot",
        "claim_update_availability_fallback",
        "confirm_update_availability_fallback",
        "finish_update_availability_fallback",
    ):
        body = sql_function(function_name)
        assert "security definer" in body
        assert "set search_path = ''" in body
        assert re.search(
            rf"grant execute on function\s+public\.{function_name}",
            sql,
        )
    assert "enable row level security" in sql
    assert re.search(
        r"revoke all privileges on table\s+"
        r"public\.update_availability_watchdog_state\s+"
        r"from public, anon, authenticated, service_role",
        sql,
    )
    assert "create policy" not in sql.lower()


def test_edge_function_auth_mode_deadline_and_dispatch_contract() -> None:
    index = (FUNCTION_DIR / "index.ts").read_text(encoding="utf-8")
    github = (FUNCTION_DIR / "github.ts").read_text(encoding="utf-8")
    watchdog = (FUNCTION_DIR / "watchdog.ts").read_text(encoding="utf-8")

    assert 'request.method !== "POST"' in index
    assert 'request.headers.has("origin")' in index
    assert 'Deno.env.get("SCHEDULER_WATCHDOG_SECRET")' in index
    assert "expectedSecret.length < 32" in index
    assert 'Deno.env.get("WATCHDOG_MODE")' in index
    assert 'const normalized = value ?? "off"' in index
    assert "const FUNCTION_DEADLINE_MS = 17_800" in index
    assert 'Deno.env.get("GITHUB_ACTIONS_DISPATCH_TOKEN")' in index
    assert 'url.searchParams.set("branch", "main")' in github
    assert 'url.searchParams.set("exclude_pull_requests", "true")' in github
    assert 'url.searchParams.set("per_page", String(PER_PAGE))' in github
    assert '"x-github-api-version": "2026-03-10"' in github
    assert "response.status !== 200" in github
    assert "Number.isSafeInteger(workflowRunId)" in github
    assert 'ref: "main"' in github
    assert "inputs: { dry_run: false }" in github
    assert "GET_ATTEMPTS = 2" in github
    assert "Exactly one POST is made" in watchdog
    assert "dependencies.dispatch()" in watchdog


def test_supabase_config_disables_gateway_jwt_for_custom_secret() -> None:
    config = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    assert config["functions"]["update-availability-watchdog"] == {
        "verify_jwt": False
    }


def test_cron_is_runbook_only_with_exact_schedule_and_timeout() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    migration = migration_text()

    assert "2,12,22,32,42,52 0-15,22-23 * * *" in runbook
    assert "timeout_milliseconds := 20000" in runbook
    assert "cron.schedule" in runbook
    assert "vault.decrypted_secrets" in runbook
    assert "cron.schedule" not in migration
    assert "net.http_post" not in migration


def test_native_workflow_schedule_concurrency_and_source_logic_are_unchanged() -> None:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    triggers = workflow_triggers(workflow)
    update = workflow["jobs"]["update"]
    steps = {step.get("name"): step for step in update["steps"]}

    assert triggers["schedule"] == [{"cron": "7,37 0-14,22-23 * * *"}]
    assert workflow["concurrency"] == {
        "group": "tennis-availability-writer",
        "cancel-in-progress": False,
    }
    assert steps["Check out repository"]["with"]["ref"] == (
        "${{ steps.source-ref.outputs.ref }}"
    )
    assert steps["Update availability"]["run"] == "python scripts/scrape.py"
    assert steps["Commit changed availability"]["if"] == "env.DRY_RUN != 'true'"
