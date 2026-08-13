import { createClient } from "npm:@supabase/supabase-js@2.95.0";
import {
  classifyWorkflowRuns,
  isJstServiceWindow,
  Snapshot,
  unknownSnapshot,
} from "./helpers.ts";
import {
  dispatchUpdateAvailability,
  fetchCompleteWorkflowRuns,
} from "./github.ts";
import { runWatchdog, WatchdogDependencies, WatchdogMode } from "./watchdog.ts";

const FUNCTION_DEADLINE_MS = 17_800;

function createWatchdogClient(supabaseUrl: string, serviceRoleKey: string) {
  return createClient(supabaseUrl, serviceRoleKey, {
    auth: {
      autoRefreshToken: false,
      persistSession: false,
      detectSessionInUrl: false,
    },
  });
}

type WatchdogClient = ReturnType<typeof createWatchdogClient>;

Deno.serve(async (request: Request): Promise<Response> => {
  const startedAt = Date.now();
  const deadlineAt = startedAt + FUNCTION_DEADLINE_MS;
  let responseStatus = 200;
  let responseBody: Record<string, unknown> = {
    outcome: "request_rejected",
    snapshot_count: 0,
    dispatch_post_count: 0,
  };

  const finish = (): Response => {
    console.log(JSON.stringify({
      ...responseBody,
      duration_ms: Date.now() - startedAt,
    }));
    return new Response(JSON.stringify(responseBody), {
      status: responseStatus,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
      },
    });
  };

  if (request.method !== "POST") {
    responseStatus = 405;
    return finish();
  }
  if (request.headers.has("origin")) {
    responseStatus = 403;
    return finish();
  }

  const expectedSecret = Deno.env.get("SCHEDULER_WATCHDOG_SECRET") ?? "";
  const suppliedSecret = readBearerToken(request.headers.get("authorization"));
  if (expectedSecret.length < 32) {
    responseStatus = 503;
    responseBody.outcome = "configuration_error";
    return finish();
  }
  if (
    suppliedSecret === null ||
    !(await secretsEqual(suppliedSecret, expectedSecret))
  ) {
    responseStatus = 401;
    return finish();
  }

  const mode = readMode(Deno.env.get("WATCHDOG_MODE"));
  if (mode === null) {
    responseStatus = 503;
    responseBody.outcome = "configuration_error";
    return finish();
  }
  if (mode === "off") {
    responseBody.outcome = "off";
    return finish();
  }

  const requestTime = new Date();
  if (!isJstServiceWindow(requestTime)) {
    responseBody.outcome = "outside_service_window";
    return finish();
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
  const githubToken = Deno.env.get("GITHUB_ACTIONS_DISPATCH_TOKEN") ?? "";
  if (
    supabaseUrl.length === 0 ||
    serviceRoleKey.length === 0 ||
    githubToken.length < 32
  ) {
    responseStatus = 503;
    responseBody.outcome = "configuration_error";
    return finish();
  }

  const supabase = createWatchdogClient(supabaseUrl, serviceRoleKey);
  const dependencies = buildDependencies(
    supabase,
    githubToken,
    deadlineAt,
  );

  try {
    const watchdogResult = await runWatchdog(mode, dependencies);
    responseBody = {
      outcome: watchdogResult.outcome,
      snapshot_count: watchdogResult.snapshotCount,
      dispatch_post_count: watchdogResult.dispatchPostCount,
      mode,
    };
    if (
      watchdogResult.outcome.includes("unknown") ||
      watchdogResult.outcome === "confirm_failed"
    ) {
      responseStatus = 502;
    }
  } catch {
    responseStatus = 502;
    responseBody = {
      outcome: "internal_error",
      snapshot_count: 0,
      dispatch_post_count: 0,
      mode,
    };
  }
  return finish();
});

function buildDependencies(
  supabase: WatchdogClient,
  githubToken: string,
  deadlineAt: number,
): WatchdogDependencies {
  return {
    observe: async () => {
      const observedAt = new Date();
      const fetched = await fetchCompleteWorkflowRuns(
        githubToken,
        observedAt,
        deadlineAt,
      );
      if (!fetched.complete) {
        const snapshot = unknownSnapshot();
        return { snapshot, reclassify: () => snapshot };
      }
      return {
        snapshot: classifyWorkflowRuns(fetched.runs, observedAt),
        reclassify: (watchdogRunId: number | null) =>
          classifyWorkflowRuns(fetched.runs, observedAt, watchdogRunId),
      };
    },
    record: async (observationToken: string, snapshot: Snapshot) => {
      const { data, error } = await supabase
        .rpc(
          "record_update_availability_watchdog_snapshot",
          snapshotRpcArguments(observationToken, snapshot),
        )
        .abortSignal(deadlineSignal(deadlineAt));
      if (error !== null || !Array.isArray(data) || data.length !== 1) {
        throw new Error("snapshot_record_failed");
      }
      const row = data[0] as Record<string, unknown>;
      if (row.recorded !== true) {
        throw new Error("snapshot_record_failed");
      }
      const runId = optionalPositiveInteger(
        row.last_dispatched_workflow_run_id,
      );
      if (
        row.last_dispatched_workflow_run_id !== null &&
        runId === null
      ) {
        throw new Error("snapshot_context_invalid");
      }
      return { lastDispatchedWorkflowRunId: runId };
    },
    claim: async (observationToken: string, claimToken: string) => {
      const { data, error } = await supabase
        .rpc(
          "claim_update_availability_fallback",
          {
            p_observation_token: observationToken,
            p_claim_token: claimToken,
          },
        )
        .abortSignal(deadlineSignal(deadlineAt));
      if (error !== null || !Array.isArray(data)) {
        throw new Error("claim_failed");
      }
      if (data.length === 0) {
        return { acquired: false };
      }
      if (
        data.length !== 1 ||
        (data[0] as Record<string, unknown>).claim_token !== claimToken
      ) {
        throw new Error("claim_result_invalid");
      }
      return { acquired: true };
    },
    confirm: async (observationToken: string, claimToken: string) => {
      const { data, error } = await supabase
        .rpc(
          "confirm_update_availability_fallback",
          {
            p_observation_token: observationToken,
            p_claim_token: claimToken,
          },
        )
        .abortSignal(deadlineSignal(deadlineAt));
      if (error !== null || typeof data !== "boolean") {
        throw new Error("confirm_failed");
      }
      return data;
    },
    finish: async (claimToken, outcome, workflowRunId = null) => {
      const { data, error } = await supabase
        .rpc(
          "finish_update_availability_fallback",
          {
            p_claim_token: claimToken,
            p_outcome: outcome,
            p_workflow_run_id: workflowRunId,
          },
        )
        .abortSignal(deadlineSignal(deadlineAt));
      if (error !== null || typeof data !== "boolean") {
        throw new Error("finish_failed");
      }
      return data;
    },
    dispatch: () => dispatchUpdateAvailability(githubToken, deadlineAt),
    uuid: () => crypto.randomUUID(),
  };
}

function snapshotRpcArguments(
  observationToken: string,
  snapshot: Snapshot,
): Record<string, unknown> {
  return {
    p_observation_token: observationToken,
    p_snapshot_outcome: snapshot.outcome,
    p_active_run_count: snapshot.activeRunCount,
    p_latest_live_run_created_at: snapshot.latestLiveRunCreatedAt,
    p_latest_live_success_at: snapshot.latestLiveSuccessAt,
    p_latest_live_failure_at: snapshot.latestLiveFailureAt,
    p_consecutive_failure_count: snapshot.consecutiveFailureCount,
  };
}

function readMode(value: string | undefined): WatchdogMode | null {
  const normalized = value ?? "off";
  return normalized === "off" || normalized === "observe" ||
      normalized === "dispatch"
    ? normalized
    : null;
}

function readBearerToken(value: string | null): string | null {
  const match = value?.match(/^Bearer ([^\s]+)$/);
  return match?.[1] ?? null;
}

async function secretsEqual(left: string, right: string): Promise<boolean> {
  const encoder = new TextEncoder();
  const [leftHash, rightHash] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(left)),
    crypto.subtle.digest("SHA-256", encoder.encode(right)),
  ]);
  const leftBytes = new Uint8Array(leftHash);
  const rightBytes = new Uint8Array(rightHash);
  let difference = leftBytes.length ^ rightBytes.length;
  for (let index = 0; index < leftBytes.length; index += 1) {
    difference |= leftBytes[index] ^ (rightBytes[index] ?? 0);
  }
  return difference === 0;
}

function optionalPositiveInteger(value: unknown): number | null {
  return Number.isSafeInteger(value) && (value as number) > 0
    ? value as number
    : null;
}

function deadlineSignal(deadlineAt: number): AbortSignal {
  return AbortSignal.timeout(Math.max(1, deadlineAt - Date.now()));
}
