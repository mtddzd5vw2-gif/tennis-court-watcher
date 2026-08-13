import { Snapshot } from "./helpers.ts";
import { DispatchResult } from "./github.ts";

export type WatchdogMode = "off" | "observe" | "dispatch";

export interface Observation {
  snapshot: Snapshot;
  reclassify: (watchdogRunId: number | null) => Snapshot;
}

export interface SnapshotContext {
  lastDispatchedWorkflowRunId: number | null;
}

export interface ClaimResult {
  acquired: boolean;
}

export interface WatchdogDependencies {
  observe: () => Promise<Observation>;
  record: (
    observationToken: string,
    snapshot: Snapshot,
  ) => Promise<SnapshotContext>;
  claim: (observationToken: string, claimToken: string) => Promise<ClaimResult>;
  confirm: (observationToken: string, claimToken: string) => Promise<boolean>;
  finish: (
    claimToken: string,
    outcome:
      | "recheck_fresh"
      | "recheck_active"
      | "recheck_unknown"
      | DispatchResult["outcome"],
    workflowRunId?: number | null,
  ) => Promise<boolean>;
  dispatch: () => Promise<DispatchResult>;
  uuid: () => string;
}

export interface WatchdogResult {
  outcome: string;
  snapshotCount: number;
  dispatchPostCount: number;
}

export async function runWatchdog(
  mode: WatchdogMode,
  dependencies: WatchdogDependencies,
): Promise<WatchdogResult> {
  if (mode === "off") {
    return result("off", 0, 0);
  }

  const first = await observeAndRecord(dependencies);
  if (first.snapshot.outcome === "unknown") {
    return result("github_snapshot_unknown", 1, 0);
  }
  if (mode === "observe") {
    return result(`observe_${first.snapshot.outcome}`, 1, 0);
  }
  if (first.snapshot.outcome !== "stale") {
    return result(first.snapshot.outcome, 1, 0);
  }

  const claimToken = dependencies.uuid();
  const claim = await dependencies.claim(first.observationToken, claimToken);
  if (!claim.acquired) {
    return result("claim_not_acquired", 1, 0);
  }

  let second: RecordedObservation;
  try {
    second = await observeAndRecord(dependencies);
  } catch {
    await safelyFinish(dependencies, claimToken, "recheck_unknown");
    return result("second_snapshot_unknown", 2, 0);
  }

  if (second.snapshot.outcome !== "stale") {
    const finishOutcome = second.snapshot.outcome === "active"
      ? "recheck_active"
      : second.snapshot.outcome === "fresh"
      ? "recheck_fresh"
      : "recheck_unknown";
    await safelyFinish(dependencies, claimToken, finishOutcome);
    return result(`second_${second.snapshot.outcome}`, 2, 0);
  }

  let confirmed = false;
  try {
    confirmed = await dependencies.confirm(
      second.observationToken,
      claimToken,
    );
  } catch {
    return result("confirm_failed", 2, 0);
  }
  if (!confirmed) {
    return result("confirm_rejected", 2, 0);
  }

  // Exactly one POST is made. Any network ambiguity is recorded while the
  // cooldown established by confirm remains in force.
  const dispatch = await dependencies.dispatch();
  await safelyFinish(
    dependencies,
    claimToken,
    dispatch.outcome,
    dispatch.workflowRunId,
  );
  return result(dispatch.outcome, 2, 1);
}

interface RecordedObservation {
  observationToken: string;
  snapshot: Snapshot;
}

async function observeAndRecord(
  dependencies: WatchdogDependencies,
): Promise<RecordedObservation> {
  const observation = await dependencies.observe();
  const observationToken = dependencies.uuid();
  const context = await dependencies.record(
    observationToken,
    observation.snapshot,
  );
  const corrected = observation.reclassify(
    context.lastDispatchedWorkflowRunId,
  );
  if (!snapshotsEqual(observation.snapshot, corrected)) {
    // The same token updates the normalized observation without incrementing
    // DB counters twice.
    await dependencies.record(observationToken, corrected);
  }
  return { observationToken, snapshot: corrected };
}

async function safelyFinish(
  dependencies: WatchdogDependencies,
  claimToken: string,
  outcome: Parameters<WatchdogDependencies["finish"]>[1],
  workflowRunId: number | null = null,
): Promise<void> {
  try {
    await dependencies.finish(claimToken, outcome, workflowRunId);
  } catch {
    // The short lease eventually releases pre-confirm failures. Confirmed
    // dispatches retain their cooldown even if final bookkeeping fails.
  }
}

function snapshotsEqual(left: Snapshot, right: Snapshot): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function result(
  outcome: string,
  snapshotCount: number,
  dispatchPostCount: number,
): WatchdogResult {
  return { outcome, snapshotCount, dispatchPostCount };
}
