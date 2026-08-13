import assert from "node:assert/strict";
import { test } from "node:test";
import { Snapshot, unknownSnapshot } from "./helpers.ts";
import { runWatchdog, WatchdogDependencies } from "./watchdog.ts";

const STALE: Snapshot = {
  outcome: "stale",
  activeRunCount: 0,
  latestLiveRunCreatedAt: "2026-08-13T00:00:00Z",
  latestLiveSuccessAt: "2026-08-13T00:00:00Z",
  latestLiveFailureAt: null,
  consecutiveFailureCount: 0,
};
const FRESH: Snapshot = {
  ...STALE,
  outcome: "fresh",
  latestLiveRunCreatedAt: "2026-08-13T02:50:00Z",
};

function dependenciesFor(snapshots: Snapshot[]) {
  let observeIndex = 0;
  let uuidIndex = 0;
  const calls = {
    claim: 0,
    confirm: 0,
    finish: [] as string[],
    dispatch: 0,
    record: 0,
  };
  const dependencies: WatchdogDependencies = {
    observe: async () => {
      const snapshot = snapshots[observeIndex++];
      if (snapshot === undefined) {
        throw new Error("no snapshot");
      }
      return { snapshot, reclassify: () => snapshot };
    },
    record: async () => {
      calls.record += 1;
      return { lastDispatchedWorkflowRunId: null };
    },
    claim: async () => {
      calls.claim += 1;
      return { acquired: true };
    },
    confirm: async () => {
      calls.confirm += 1;
      return true;
    },
    finish: async (_token, outcome) => {
      calls.finish.push(outcome);
      return true;
    },
    dispatch: async () => {
      calls.dispatch += 1;
      return { outcome: "dispatch_accepted", workflowRunId: 123456 };
    },
    uuid: () =>
      `00000000-0000-4000-8000-${String(++uuidIndex).padStart(12, "0")}`,
  };
  return { dependencies, calls };
}

test("a fresh second snapshot aborts before confirm and POST", async () => {
  const { dependencies, calls } = dependenciesFor([STALE, FRESH]);
  const result = await runWatchdog("dispatch", dependencies);
  assert.equal(result.outcome, "second_fresh");
  assert.equal(calls.claim, 1);
  assert.equal(calls.confirm, 0);
  assert.equal(calls.dispatch, 0);
  assert.deepEqual(calls.finish, ["recheck_fresh"]);
});

test("an unknown second snapshot aborts before POST", async () => {
  const { dependencies, calls } = dependenciesFor([STALE, unknownSnapshot()]);
  const result = await runWatchdog("dispatch", dependencies);
  assert.equal(result.outcome, "second_unknown");
  assert.equal(calls.dispatch, 0);
  assert.deepEqual(calls.finish, ["recheck_unknown"]);
});

test("observe mode records stale state but never claims or posts", async () => {
  const { dependencies, calls } = dependenciesFor([STALE]);
  const result = await runWatchdog("observe", dependencies);
  assert.equal(result.outcome, "observe_stale");
  assert.equal(calls.record, 1);
  assert.equal(calls.claim, 0);
  assert.equal(calls.dispatch, 0);
});

test("dispatch mode confirms after two stale snapshots and posts once", async () => {
  const { dependencies, calls } = dependenciesFor([STALE, STALE]);
  const result = await runWatchdog("dispatch", dependencies);
  assert.equal(result.outcome, "dispatch_accepted");
  assert.equal(calls.confirm, 1);
  assert.equal(calls.dispatch, 1);
  assert.deepEqual(calls.finish, ["dispatch_accepted"]);
});

test("off mode performs no external work", async () => {
  const { dependencies, calls } = dependenciesFor([]);
  const result = await runWatchdog("off", dependencies);
  assert.equal(result.outcome, "off");
  assert.equal(calls.record, 0);
  assert.equal(calls.dispatch, 0);
});
