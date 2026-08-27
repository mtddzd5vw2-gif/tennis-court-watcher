from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


WORKFLOW_PATH = Path(".github/workflows/update-availability.yml")


def load_workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def workflow_triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    return workflow.get("on", workflow.get(True, {}))


def commit_step_script(workflow: dict[str, Any]) -> str:
    steps = workflow["jobs"]["update"]["steps"]
    return next(
        step["run"]
        for step in steps
        if step.get("name") == "Commit changed availability"
    )


def push_retry_loop(script: str) -> str:
    match = re.search(
        r"for attempt in .*?; do(?P<body>.*?)^\s*done$",
        script,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    return match.group("body")


def matching_step(workflow: dict[str, Any]) -> dict[str, Any]:
    return next(
        step
        for step in workflow["jobs"]["update"]["steps"]
        if step.get("name") == "Match notification rules"
    )


def named_step(workflow: dict[str, Any], name: str) -> dict[str, Any]:
    return next(
        step
        for step in workflow["jobs"]["update"]["steps"]
        if step.get("name") == name
    )


def external_action_references(workflow: dict[str, Any]) -> list[str]:
    return [
        step["uses"]
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "uses" in step and not step["uses"].startswith("./")
    ]


def test_external_actions_are_pinned_to_full_commit_shas() -> None:
    references = external_action_references(load_workflow())

    assert references
    assert all(
        re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", reference)
        for reference in references
    )


def test_data_update_concurrency_is_global_and_non_cancelling() -> None:
    workflow = load_workflow()

    assert workflow["concurrency"] == {
        "group": "tennis-availability-writer",
        "cancel-in-progress": False,
    }


def test_push_retry_is_bounded_to_three_attempts() -> None:
    script = commit_step_script(load_workflow())

    assert "MAX_PUSH_ATTEMPTS=3" in script
    assert 'seq 1 "${MAX_PUSH_ATTEMPTS}"' in script
    assert '"${attempt}" -eq "${MAX_PUSH_ATTEMPTS}"' in script
    assert "exit 1" in script


def test_rejected_push_fetches_and_rebases_before_retry() -> None:
    script = commit_step_script(load_workflow())
    loop = push_retry_loop(script)

    push = loop.index("git push")
    rejection_check = loop.index("grep -Eiq")
    fetch = loop.index("git fetch origin main")
    rebase = loop.index("git rebase origin/main")
    wait = loop.index("sleep 2")

    assert push < rejection_check < fetch < rebase < wait


def test_rebase_conflict_aborts_and_fails_without_force_push() -> None:
    script = commit_step_script(load_workflow())
    conflict_handler = re.search(
        r"if ! git rebase origin/main; then(?P<body>.*?)^\s*fi$",
        script,
        re.MULTILINE | re.DOTALL,
    )

    assert conflict_handler is not None
    handler = conflict_handler.group("body")
    assert handler.index("git rebase --abort") < handler.index("exit 1")
    assert "Rebase onto origin/main conflicted" in handler
    assert not re.search(
        r"git\s+push[^\n]*(?:--force(?:-with-lease)?|-f\b)",
        WORKFLOW_PATH.read_text(encoding="utf-8"),
    )


def test_push_retry_loop_only_retries_git_integration_and_push() -> None:
    loop = push_retry_loop(commit_step_script(load_workflow()))

    assert "scripts/scrape.py" not in loop
    assert "LINE" not in loop
    assert "notification" not in loop.lower()
    assert "upload-artifact" not in loop
    assert "run-output" not in loop


def test_deploy_pages_still_requires_a_successful_non_dry_run_update() -> None:
    workflow = load_workflow()

    assert workflow["jobs"]["deploy-pages"]["if"] == (
        "needs.update.result == 'success' && "
        "needs.update.outputs.deploy_pages == 'true'"
    )


def test_notification_matching_is_variable_gated_after_scraping() -> None:
    workflow = load_workflow()
    steps = workflow["jobs"]["update"]["steps"]
    step = matching_step(workflow)
    names = [item.get("name") for item in steps]

    assert step["if"] == "vars.ENABLE_NOTIFICATION_MATCHING == 'true'"
    assert step["continue-on-error"] is True
    assert names.index("Update availability") < names.index(
        "Match notification rules"
    )
    assert names.index("Match notification rules") < names.index(
        "Upload run data and reservation page snapshots"
    )
    assert "python scripts/match_notification_rules.py" in step["run"]
    assert "--availability run-output/availability.json" in step["run"]


def test_service_role_key_is_scoped_to_the_matching_step() -> None:
    workflow = load_workflow()
    step = matching_step(workflow)

    assert step["env"] == {
        "SUPABASE_URL": "${{ vars.SUPABASE_URL }}",
        "SUPABASE_SERVICE_ROLE_KEY": (
            "${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}"
        ),
    }
    assert "SUPABASE_SERVICE_ROLE_KEY" not in workflow["jobs"]["update"]["env"]
    assert "SUPABASE_SERVICE_ROLE_KEY" not in workflow["jobs"]["deploy-pages"]["env"]
    assert "echo" not in step["run"].lower()


def test_matching_details_are_not_added_to_artifacts_or_pages() -> None:
    workflow = load_workflow()
    update_steps = workflow["jobs"]["update"]["steps"]
    artifact_step = next(
        step
        for step in update_steps
        if step.get("name") == "Upload run data and reservation page snapshots"
    )
    artifact_path = artifact_step["with"]["path"]
    pages_script = next(
        step["run"]
        for step in workflow["jobs"]["deploy-pages"]["steps"]
        if step.get("name") == "Prepare GitHub Pages files"
    )

    assert "match-result" not in artifact_path
    assert "match-result" not in pages_script
    assert "match_candidates" not in artifact_path
    assert "match_candidates" not in pages_script


def test_user_email_enqueue_has_exact_flags_and_dry_run_gate() -> None:
    workflow = load_workflow()
    step = named_step(workflow, "Enqueue user email notifications")

    assert step["if"] == (
        "vars.ENABLE_NOTIFICATION_MATCHING == 'true' && "
        "vars.ENABLE_USER_EMAIL_ENQUEUE == 'true' && "
        "env.LINE_SHADOW_ONLY != 'true' && "
        "env.DRY_RUN != 'true'"
    )
    assert step["continue-on-error"] is True
    assert step["env"] == {
        "SUPABASE_URL": "${{ vars.SUPABASE_URL }}",
        "SUPABASE_SERVICE_ROLE_KEY": "${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}",
    }
    assert "python scripts/enqueue_email_notifications.py" in step["run"]
    assert "--availability run-output/availability.json" in step["run"]


def test_user_email_dispatch_has_exact_flag_and_independent_dry_run_gate() -> None:
    workflow = load_workflow()
    step = named_step(workflow, "Dispatch user email notifications")

    assert step["if"] == (
        "vars.ENABLE_USER_EMAIL_DISPATCH == 'true' && "
        "env.LINE_SHADOW_ONLY != 'true' && "
        "env.DRY_RUN != 'true'"
    )
    assert "ENABLE_NOTIFICATION_MATCHING" not in step["if"]
    assert "ENABLE_USER_EMAIL_ENQUEUE" not in step["if"]
    assert "steps." not in step["if"]
    assert step["continue-on-error"] is True
    assert step["env"] == {
        "SUPABASE_URL": "${{ vars.SUPABASE_URL }}",
        "EMAIL_DELIVERY_WORKER_SECRET": (
            "${{ secrets.EMAIL_DELIVERY_WORKER_SECRET }}"
        ),
    }
    assert step["run"] == "python scripts/dispatch_email_notifications.py"


def test_email_credentials_are_separated_by_step() -> None:
    workflow = load_workflow()
    matching = matching_step(workflow)
    enqueue = named_step(workflow, "Enqueue user email notifications")
    dispatch = named_step(workflow, "Dispatch user email notifications")

    assert "EMAIL_DELIVERY_WORKER_SECRET" not in matching["env"]
    assert "EMAIL_DELIVERY_WORKER_SECRET" not in enqueue["env"]
    assert "SUPABASE_SERVICE_ROLE_KEY" not in dispatch["env"]
    assert "EMAIL_DELIVERY_WORKER_SECRET" not in workflow["jobs"]["update"]["env"]
    assert "EMAIL_DELIVERY_WORKER_SECRET" not in workflow["jobs"]["deploy-pages"]["env"]


def test_email_failures_do_not_block_artifact_commit_or_pages_inputs() -> None:
    workflow = load_workflow()
    steps = workflow["jobs"]["update"]["steps"]
    names = [step.get("name") for step in steps]

    for name in (
        "Match notification rules",
        "Enqueue user email notifications",
        "Dispatch user email notifications",
    ):
        assert named_step(workflow, name)["continue-on-error"] is True
        assert names.index(name) < names.index(
            "Upload run data and reservation page snapshots"
        )
        assert names.index(name) < names.index(
            "Commit changed availability"
        )


def test_user_line_enqueue_defaults_to_shadow_and_requires_explicit_rollout() -> None:
    workflow = load_workflow()
    step = named_step(workflow, "Enqueue user LINE notifications")

    assert step["if"] == (
        "vars.ENABLE_NOTIFICATION_MATCHING == 'true' && "
        "vars.ENABLE_USER_LINE_ENQUEUE == 'true' && "
        "(env.DRY_RUN != 'true' || env.LINE_SHADOW_ONLY == 'true')"
    )
    assert step["continue-on-error"] is True
    assert step["env"] == {
        "SUPABASE_URL": "${{ vars.SUPABASE_URL }}",
        "SUPABASE_SERVICE_ROLE_KEY": "${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}",
        "LINE_NOTIFICATION_SHADOW_MODE": (
            "${{ env.LINE_SHADOW_ONLY == 'true' && 'true' || "
            "vars.LINE_NOTIFICATION_SHADOW_MODE || 'true' }}"
        ),
        "LINE_NOTIFICATION_CANARY_USER_ID": (
            "${{ secrets.LINE_NOTIFICATION_CANARY_USER_ID }}"
        ),
        "LINE_NOTIFICATION_ALLOW_ALL": (
            "${{ vars.LINE_NOTIFICATION_ALLOW_ALL || 'false' }}"
        ),
    }
    assert "scripts/enqueue_line_notifications.py" in step["run"]


def test_user_line_dispatch_is_separately_gated_and_has_no_provider_token() -> None:
    workflow = load_workflow()
    step = named_step(workflow, "Dispatch user LINE notifications")

    assert step["if"] == (
        "vars.ENABLE_USER_LINE_DISPATCH == 'true' && "
        "vars.LINE_NOTIFICATION_SHADOW_MODE == 'false' && "
        "env.LINE_SHADOW_ONLY != 'true' && "
        "env.DRY_RUN != 'true'"
    )
    assert step["continue-on-error"] is True
    assert step["env"] == {
        "SUPABASE_URL": "${{ vars.SUPABASE_URL }}",
        "LINE_DELIVERY_WORKER_SECRET": (
            "${{ secrets.LINE_DELIVERY_WORKER_SECRET }}"
        ),
    }
    assert "LINE_CHANNEL_ACCESS_TOKEN" not in step["env"]
    assert step["run"] == "python scripts/dispatch_line_notifications.py"


def test_legacy_administrator_line_inputs_environment_and_state_are_absent() -> None:
    workflow = load_workflow()
    job = workflow["jobs"]["update"]
    triggers = workflow_triggers(workflow)
    inputs = triggers["workflow_dispatch"]["inputs"]
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert set(inputs) == {"dry_run", "line_shadow_only"}
    assert set(job["env"]) == {"DRY_RUN", "LINE_SHADOW_ONLY"}
    for legacy_reference in (
        "LINE_CHANNEL_ACCESS_TOKEN",
        "LINE_USER_ID",
        "ENABLE_LINE_NOTIFICATIONS",
        "SEND_NOTIFICATION",
        "TEST_NOTIFICATION",
        "INITIALIZE_NOTIFICATION_BASELINE",
        "notification-state.json",
    ):
        assert legacy_reference not in workflow_text
    assert named_step(workflow, "Update availability")["run"] == (
        "python scripts/scrape.py"
    )


def test_schedule_and_scheduled_run_gate_are_maintained() -> None:
    workflow = load_workflow()
    triggers = workflow_triggers(workflow)

    assert triggers["schedule"] == [{"cron": "7,37 0-14,22-23 * * *"}]
    assert workflow["jobs"]["update"]["if"] == (
        "github.event_name == 'workflow_dispatch' || "
        "vars.ENABLE_SCHEDULED_RUNS == 'true'"
    )


def test_run_name_distinguishes_all_execution_modes() -> None:
    workflow = load_workflow()

    assert workflow["run-name"] == (
        "Update tennis availability "
        "[${{ github.event_name == 'schedule' && 'scheduled-live' || "
        "inputs.line_shadow_only && 'manual-line-shadow' || "
        "inputs.dry_run && 'manual-dry-run' || 'manual-live' }}]"
    )


def test_update_checkout_uses_live_branch_head_and_dry_run_event_sha() -> None:
    workflow = load_workflow()
    job = workflow["jobs"]["update"]
    steps = job["steps"]
    names = [step.get("name") for step in steps]
    source_ref = named_step(workflow, "Determine source ref")
    checkout = named_step(workflow, "Check out repository")
    source_revision = named_step(workflow, "Record source revision")

    assert names.index("Determine source ref") < names.index(
        "Check out repository"
    )
    assert source_ref["run"] == (
        'if [[ "${GITHUB_EVENT_NAME}" == "schedule" ]]; then\n'
        '  echo "ref=${GITHUB_REF_NAME}" >> "${GITHUB_OUTPUT}"\n'
        'elif [[ "${DRY_RUN}" == "true" || "${LINE_SHADOW_ONLY}" == "true" ]]; then\n'
        '  echo "ref=${GITHUB_SHA}" >> "${GITHUB_OUTPUT}"\n'
        "else\n"
        '  echo "ref=${GITHUB_REF_NAME}" >> "${GITHUB_OUTPUT}"\n'
        "fi\n"
    )
    assert checkout["with"]["ref"] == "${{ steps.source-ref.outputs.ref }}"
    assert names.index("Check out repository") < names.index(
        "Record source revision"
    )
    assert source_revision["run"] == (
        'echo "sha=$(git rev-parse HEAD)" >> "${GITHUB_OUTPUT}"'
    )
    assert job["outputs"] == {
        "deploy_pages": "${{ steps.execution-mode.outputs.deploy_pages }}",
        "source_sha": "${{ steps.source-revision.outputs.sha }}",
    }


def test_pages_checkout_uses_the_update_source_sha() -> None:
    workflow = load_workflow()
    steps = workflow["jobs"]["deploy-pages"]["steps"]
    checkout = next(
        step for step in steps if step.get("name") == "Check out repository"
    )

    assert checkout["uses"] == (
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    )
    assert checkout["with"] == {
        "ref": "${{ needs.update.outputs.source_sha }}",
    }


def test_dry_run_acquires_artifacts_without_commit_push_or_pages_deploy() -> None:
    workflow = load_workflow()
    dry_run = workflow_triggers(workflow)["workflow_dispatch"]["inputs"]["dry_run"]
    execution_step = named_step(workflow, "Determine execution mode")
    commit_step = named_step(workflow, "Commit changed availability")

    assert dry_run["description"] == (
        "Acquire data and artifacts without commit, push, or Pages deployment"
    )
    assert dry_run["default"] is True
    assert (
        'if [[ "${DRY_RUN}" == "true" || "${LINE_SHADOW_ONLY}" == "true" ]]'
        in execution_step["run"]
    )
    assert 'echo "deploy_pages=false"' in execution_step["run"]
    assert commit_step["if"] == (
        "env.DRY_RUN != 'true' && env.LINE_SHADOW_ONLY != 'true'"
    )
    assert workflow["jobs"]["deploy-pages"]["if"] == (
        "needs.update.result == 'success' && "
        "needs.update.outputs.deploy_pages == 'true'"
    )


def test_line_shadow_only_forces_no_delivery_email_or_repository_writes() -> None:
    workflow = load_workflow()
    inputs = workflow_triggers(workflow)["workflow_dispatch"]["inputs"]
    job = workflow["jobs"]["update"]

    assert inputs["line_shadow_only"] == {
        "description": (
            "Evaluate LINE candidates in forced shadow mode without delivery or "
            "repository writes"
        ),
        "type": "boolean",
        "default": False,
    }
    assert job["env"]["LINE_SHADOW_ONLY"] == (
        "${{ github.event_name == 'workflow_dispatch' && "
        "inputs.line_shadow_only || false }}"
    )
    assert "env.LINE_SHADOW_ONLY != 'true'" in named_step(
        workflow, "Enqueue user email notifications"
    )["if"]
    assert "env.LINE_SHADOW_ONLY != 'true'" in named_step(
        workflow, "Dispatch user email notifications"
    )["if"]
    assert "env.LINE_SHADOW_ONLY != 'true'" in named_step(
        workflow, "Dispatch user LINE notifications"
    )["if"]


def test_non_dry_run_commits_only_availability() -> None:
    script = commit_step_script(load_workflow())

    assert "git diff --quiet -- data/availability.json" in script
    assert re.findall(r"^\s*git add (.+)$", script, re.MULTILINE) == [
        "data/availability.json"
    ]
    assert "notification-state.json" not in script
