import assert from "node:assert/strict";
import { test } from "node:test";
import {
  classifyWorkflowRuns,
  isJstServiceWindow,
  MAX_FUTURE_CLOCK_SKEW_MS,
  parseGitHubRunsPage,
  STALE_AFTER_MS,
  WorkflowRun,
} from "./helpers.ts";

const NOW = new Date("2026-08-13T03:00:00.000Z");

function run(overrides: Partial<WorkflowRun> = {}): WorkflowRun {
  return {
    id: 1,
    event: "schedule",
    status: "completed",
    conclusion: "success",
    headBranch: "main",
    createdAt: new Date(NOW.getTime() - 10 * 60 * 1000).toISOString(),
    displayTitle: "Update tennis availability [scheduled-live]",
    ...overrides,
  };
}

function apiPage(overrides: Record<string, unknown> = {}) {
  return {
    total_count: 1,
    workflow_runs: [{
      id: 1,
      event: "schedule",
      status: "completed",
      conclusion: "success",
      head_branch: "main",
      created_at: NOW.toISOString(),
      display_title: "Update tennis availability [scheduled-live]",
      ...overrides,
    }],
  };
}

test("scheduled success and failure both establish liveness by created_at", () => {
  for (const conclusion of ["success", "failure", "cancelled", "timed_out"]) {
    const snapshot = classifyWorkflowRuns([run({ conclusion })], NOW);
    assert.equal(snapshot.outcome, "fresh");
    assert.equal(snapshot.latestLiveRunCreatedAt, run().createdAt);
  }
});

test("manual-live on main establishes liveness", () => {
  const snapshot = classifyWorkflowRuns([
    run({
      event: "workflow_dispatch",
      displayTitle: "Update tennis availability [manual-live]",
    }),
  ], NOW);
  assert.equal(snapshot.outcome, "fresh");
});

test("a persisted watchdog run id establishes liveness", () => {
  const candidate = run({
    id: 991,
    event: "workflow_dispatch",
    displayTitle: "legacy title without marker",
  });
  assert.equal(classifyWorkflowRuns([candidate], NOW).outcome, "unknown");
  assert.equal(classifyWorkflowRuns([candidate], NOW, 991).outcome, "fresh");
});

test("empty workflow runs are unknown", () => {
  const snapshot = classifyWorkflowRuns([], NOW);
  assert.equal(snapshot.outcome, "unknown");
  assert.equal(snapshot.latestLiveRunCreatedAt, null);
});

test("a completed manual dry-run without a live run is unknown", () => {
  const snapshot = classifyWorkflowRuns([
    run({
      id: 2,
      event: "workflow_dispatch",
      displayTitle: "Update tennis availability [manual-dry-run]",
    }),
  ], NOW);
  assert.equal(snapshot.outcome, "unknown");
  assert.equal(snapshot.latestLiveRunCreatedAt, null);
});

test("a feature branch run without a main live run is unknown", () => {
  const snapshot = classifyWorkflowRuns([
    run({ id: 3, headBranch: "feature/test" }),
  ], NOW);
  assert.equal(snapshot.outcome, "unknown");
  assert.equal(snapshot.latestLiveRunCreatedAt, null);
});

test("manual dry-run plus feature branch run remains unknown", () => {
  const snapshot = classifyWorkflowRuns([
    run({
      id: 2,
      event: "workflow_dispatch",
      displayTitle: "Update tennis availability [manual-dry-run]",
    }),
    run({ id: 3, headBranch: "feature/test" }),
  ], NOW);
  assert.equal(snapshot.outcome, "unknown");
  assert.equal(snapshot.latestLiveRunCreatedAt, null);
});

test("an active main dry-run blocks fallback collision", () => {
  const snapshot = classifyWorkflowRuns([
    run({
      event: "workflow_dispatch",
      status: "queued",
      conclusion: null,
      displayTitle: "Update tennis availability [manual-dry-run]",
    }),
  ], NOW);
  assert.equal(snapshot.outcome, "active");
  assert.equal(snapshot.activeRunCount, 1);
});

test("an active run at least 45 minutes old does not block fallback", () => {
  const snapshot = classifyWorkflowRuns([
    run({
      status: "queued",
      conclusion: null,
      createdAt: new Date(NOW.getTime() - STALE_AFTER_MS).toISOString(),
    }),
  ], NOW);
  assert.equal(snapshot.outcome, "stale");
  assert.equal(snapshot.activeRunCount, 0);
});

test("exactly 45 minutes old is stale", () => {
  const snapshot = classifyWorkflowRuns([
    run({
      createdAt: new Date(NOW.getTime() - STALE_AFTER_MS).toISOString(),
    }),
  ], NOW);
  assert.equal(snapshot.outcome, "stale");
});

test("JST service-window boundaries are exact", () => {
  assert.equal(isJstServiceWindow(new Date("2026-08-12T22:19:59Z")), false);
  assert.equal(isJstServiceWindow(new Date("2026-08-12T22:20:00Z")), true);
  assert.equal(isJstServiceWindow(new Date("2026-08-12T15:22:00Z")), true);
  assert.equal(isJstServiceWindow(new Date("2026-08-12T15:29:59Z")), true);
  assert.equal(isJstServiceWindow(new Date("2026-08-12T15:30:00Z")), false);
});

test("malformed GitHub response is rejected", () => {
  assert.equal(parseGitHubRunsPage({ workflow_runs: [] }, NOW), null);
  assert.equal(
    parseGitHubRunsPage({ total_count: 1, workflow_runs: [{}] }, NOW),
    null,
  );
  assert.equal(
    parseGitHubRunsPage(apiPage({ created_at: "not-a-date" }), NOW),
    null,
  );
});

test("unknown workflow status fails closed", () => {
  assert.equal(parseGitHubRunsPage(apiPage({ status: "mystery" }), NOW), null);
});

test("created_at beyond the allowed future clock skew fails closed", () => {
  const createdAt = new Date(
    NOW.getTime() + MAX_FUTURE_CLOCK_SKEW_MS + 1,
  ).toISOString();
  assert.equal(
    parseGitHubRunsPage(apiPage({ created_at: createdAt }), NOW),
    null,
  );
});

test("created_at exactly at the future clock skew boundary is allowed", () => {
  const createdAt = new Date(
    NOW.getTime() + MAX_FUTURE_CLOCK_SKEW_MS,
  ).toISOString();
  assert.notEqual(
    parseGitHubRunsPage(apiPage({ created_at: createdAt }), NOW),
    null,
  );
});
