import assert from "node:assert/strict";
import { test } from "node:test";
import {
  dispatchUpdateAvailability,
  fetchCompleteWorkflowRuns,
  FetchLike,
} from "./github.ts";

const TOKEN = "g".repeat(40);

function apiRun(id: number, createdAt: string) {
  return {
    id,
    event: "schedule",
    status: "completed",
    conclusion: "success",
    head_branch: "main",
    created_at: createdAt,
    display_title: "Update tennis availability [scheduled-live]",
  };
}

test("pagination that cannot cover the 45-minute window fails closed", async () => {
  let calls = 0;
  let firstUrl = "";
  const recentRuns = Array.from({ length: 100 }, (_, index) =>
    apiRun(
      1000 - index,
      new Date(Date.now() - index * 1000).toISOString(),
    ));
  const fetchImpl: FetchLike = async (input) => {
    calls += 1;
    if (calls === 1) {
      firstUrl = String(input);
      return Response.json({ total_count: 101, workflow_runs: recentRuns });
    }
    return new Response("temporary", { status: 503 });
  };
  const result = await fetchCompleteWorkflowRuns(
    TOKEN,
    new Date(),
    Date.now() + 10_000,
    fetchImpl,
    async () => {},
  );
  assert.equal(result.complete, false);
  assert.deepEqual(result.runs, []);
  assert.equal(calls, 3, "the second page gets one bounded GET retry");
  const parsedFirstUrl = new URL(firstUrl);
  assert.equal(parsedFirstUrl.searchParams.get("branch"), "main");
  assert.equal(
    parsedFirstUrl.searchParams.get("exclude_pull_requests"),
    "true",
  );
  assert.equal(parsedFirstUrl.searchParams.get("per_page"), "100");
});

test("a malformed second snapshot response fails closed", async () => {
  const result = await fetchCompleteWorkflowRuns(
    TOKEN,
    new Date(),
    Date.now() + 10_000,
    async () => Response.json({ total_count: 1, workflow_runs: [{}] }),
  );
  assert.equal(result.complete, false);
});

test("200 with workflow_run_id accepts exactly one live dispatch", async () => {
  let calls = 0;
  let body = "";
  let apiVersion = "";
  const result = await dispatchUpdateAvailability(
    TOKEN,
    Date.now() + 10_000,
    async (_input, init) => {
      calls += 1;
      body = String(init?.body);
      apiVersion = new Headers(init?.headers).get("x-github-api-version") ?? "";
      return Response.json({
        workflow_run_id: 123456,
        run_url: "https://api.github.com/repos/example/actions/runs/123456",
        html_url: "https://github.com/example/actions/runs/123456",
      });
    },
  );
  assert.equal(calls, 1);
  assert.deepEqual(JSON.parse(body), {
    ref: "main",
    inputs: { dry_run: false },
  });
  assert.equal(apiVersion, "2026-03-10");
  assert.deepEqual(result, {
    outcome: "dispatch_accepted",
    workflowRunId: 123456,
  });
});

test("200 with missing, invalid, or malformed workflow_run_id is unknown", async () => {
  const responses = [
    () => Response.json({}),
    () => Response.json({ workflow_run_id: 0 }),
    () => Response.json({ workflow_run_id: -1 }),
    () => Response.json({ workflow_run_id: Number.MAX_SAFE_INTEGER + 1 }),
    () => Response.json({ workflow_run_id: "123456" }),
    () => Response.json(null),
    () => new Response("not-json", { status: 200 }),
  ];
  for (const response of responses) {
    let calls = 0;
    const result = await dispatchUpdateAvailability(
      TOKEN,
      Date.now() + 10_000,
      async () => {
        calls += 1;
        return response();
      },
    );
    assert.equal(calls, 1);
    assert.deepEqual(result, {
      outcome: "dispatch_unknown",
      workflowRunId: null,
    });
  }
});

test("unexpected 204 success is unknown and is not retried", async () => {
  let calls = 0;
  const result = await dispatchUpdateAvailability(
    TOKEN,
    Date.now() + 10_000,
    async () => {
      calls += 1;
      return new Response(null, { status: 204 });
    },
  );
  assert.equal(calls, 1);
  assert.deepEqual(result, {
    outcome: "dispatch_unknown",
    workflowRunId: null,
  });
});

test("workflow dispatch never retries timeout or 5xx", async () => {
  const fetchImplementations: FetchLike[] = [
    async (_input, _init) => {
      throw new Error("timeout");
    },
    async (_input, _init) => new Response("server error", { status: 503 }),
  ];
  for (const fetchImpl of fetchImplementations) {
    let calls = 0;
    const result = await dispatchUpdateAvailability(
      TOKEN,
      Date.now() + 10_000,
      async (input, init) => {
        calls += 1;
        return await fetchImpl(input, init);
      },
    );
    assert.equal(calls, 1);
    assert.equal(result.outcome, "dispatch_unknown");
  }
});
