export const STALE_AFTER_MS = 45 * 60 * 1000;
export const MAX_FUTURE_CLOCK_SKEW_MS = 2 * 60 * 1000;
export const CLAIM_LEASE_MS = 5 * 60 * 1000;
export const DISPATCH_COOLDOWN_MS = 30 * 60 * 1000;
export const WORKFLOW_RUN_STATUSES = new Set([
  "queued",
  "in_progress",
  "requested",
  "waiting",
  "pending",
  "completed",
]);
export const ACTIVE_RUN_STATUSES = new Set([
  "queued",
  "in_progress",
  "requested",
  "waiting",
  "pending",
]);

export interface WorkflowRun {
  id: number;
  event: string;
  status: string;
  conclusion: string | null;
  headBranch: string;
  createdAt: string;
  displayTitle: string;
}

export type SnapshotOutcome = "fresh" | "stale" | "active" | "unknown";

export interface Snapshot {
  outcome: SnapshotOutcome;
  activeRunCount: number;
  latestLiveRunCreatedAt: string | null;
  latestLiveSuccessAt: string | null;
  latestLiveFailureAt: string | null;
  consecutiveFailureCount: number;
}

export interface GitHubRunsPage {
  totalCount: number;
  runs: WorkflowRun[];
}

export function isJstServiceWindow(at: Date): boolean {
  if (!Number.isFinite(at.getTime())) {
    return false;
  }
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Tokyo",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(at);
  const values = Object.fromEntries(
    parts.map((part) => [part.type, part.value]),
  );
  const seconds = Number(values.hour) * 3600 +
    Number(values.minute) * 60 + Number(values.second);
  return seconds >= 7 * 3600 + 20 * 60 || seconds < 30 * 60;
}

export function isQualifyingLiveRun(
  run: WorkflowRun,
  watchdogRunId: number | null,
): boolean {
  if (run.headBranch !== "main") {
    return false;
  }
  if (run.event === "schedule") {
    return true;
  }
  if (watchdogRunId !== null && run.id === watchdogRunId) {
    return true;
  }
  return run.event === "workflow_dispatch" &&
    run.displayTitle.includes("[manual-live]");
}

export function classifyWorkflowRuns(
  runs: WorkflowRun[],
  observedAt: Date,
  watchdogRunId: number | null = null,
): Snapshot {
  const activeCutoff = observedAt.getTime() - STALE_AFTER_MS;
  const activeRuns = runs.filter((run) =>
    run.headBranch === "main" && ACTIVE_RUN_STATUSES.has(run.status) &&
    Date.parse(run.createdAt) > activeCutoff
  );
  const liveRuns = runs
    .filter((run) => isQualifyingLiveRun(run, watchdogRunId))
    .sort((left, right) =>
      Date.parse(right.createdAt) - Date.parse(left.createdAt)
    );
  const latestLiveRunCreatedAt = liveRuns[0]?.createdAt ?? null;
  const completedLiveRuns = liveRuns.filter((run) => run.conclusion !== null);
  const latestSuccess = completedLiveRuns.find((run) =>
    run.conclusion === "success"
  );
  const latestFailure = completedLiveRuns.find((run) =>
    run.conclusion !== "success"
  );

  let consecutiveFailureCount = 0;
  for (const run of completedLiveRuns) {
    if (run.conclusion === "success") {
      break;
    }
    consecutiveFailureCount += 1;
  }

  let outcome: SnapshotOutcome;
  if (activeRuns.length > 0) {
    outcome = "active";
  } else if (latestLiveRunCreatedAt === null) {
    outcome = "unknown";
  } else if (
    Date.parse(latestLiveRunCreatedAt) > observedAt.getTime() - STALE_AFTER_MS
  ) {
    outcome = "fresh";
  } else {
    // Exactly 45:00 old is stale by contract.
    outcome = "stale";
  }

  return {
    outcome,
    activeRunCount: activeRuns.length,
    latestLiveRunCreatedAt,
    latestLiveSuccessAt: latestSuccess?.createdAt ?? null,
    latestLiveFailureAt: latestFailure?.createdAt ?? null,
    consecutiveFailureCount,
  };
}

export function unknownSnapshot(): Snapshot {
  return {
    outcome: "unknown",
    activeRunCount: 0,
    latestLiveRunCreatedAt: null,
    latestLiveSuccessAt: null,
    latestLiveFailureAt: null,
    consecutiveFailureCount: 0,
  };
}

export function parseGitHubRunsPage(
  value: unknown,
  observedAt: Date,
): GitHubRunsPage | null {
  if (
    !Number.isFinite(observedAt.getTime()) ||
    !isRecord(value) || !Number.isSafeInteger(value.total_count) ||
    (value.total_count as number) < 0 || !Array.isArray(value.workflow_runs)
  ) {
    return null;
  }

  const runs: WorkflowRun[] = [];
  for (const candidate of value.workflow_runs) {
    if (!isRecord(candidate)) {
      return null;
    }
    const id = candidate.id;
    const event = candidate.event;
    const status = candidate.status;
    const conclusion = candidate.conclusion;
    const headBranch = candidate.head_branch;
    const createdAt = candidate.created_at;
    const displayTitle = candidate.display_title;
    if (
      !Number.isSafeInteger(id) || (id as number) <= 0 ||
      typeof event !== "string" ||
      typeof status !== "string" || !WORKFLOW_RUN_STATUSES.has(status) ||
      !(conclusion === null || typeof conclusion === "string") ||
      typeof headBranch !== "string" ||
      typeof createdAt !== "string" ||
      !Number.isFinite(Date.parse(createdAt)) ||
      Date.parse(createdAt) > observedAt.getTime() + MAX_FUTURE_CLOCK_SKEW_MS ||
      typeof displayTitle !== "string"
    ) {
      return null;
    }
    runs.push({
      id: id as number,
      event,
      status,
      conclusion: conclusion as string | null,
      headBranch,
      createdAt,
      displayTitle,
    });
  }
  return { totalCount: value.total_count as number, runs };
}

export function pagesCoverLivenessWindow(
  runs: WorkflowRun[],
  fetchedCount: number,
  totalCount: number,
  lastPageSize: number,
  observedAt: Date,
  perPage: number,
): boolean {
  if (fetchedCount >= totalCount || lastPageSize < perPage) {
    return true;
  }
  const cutoff = observedAt.getTime() - STALE_AFTER_MS;
  return runs.some((run) => Date.parse(run.createdAt) <= cutoff);
}

export function runsAreNewestFirst(runs: WorkflowRun[]): boolean {
  for (let index = 1; index < runs.length; index += 1) {
    if (
      Date.parse(runs[index - 1].createdAt) < Date.parse(runs[index].createdAt)
    ) {
      return false;
    }
  }
  return true;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
