import {
  pagesCoverLivenessWindow,
  parseGitHubRunsPage,
  runsAreNewestFirst,
  WorkflowRun,
} from "./helpers.ts";

const OWNER = "mtddzd5vw2-gif";
const REPOSITORY = "tennis-court-watcher";
const WORKFLOW_FILE = "update-availability.yml";
const API_ROOT = `https://api.github.com/repos/${OWNER}/${REPOSITORY}`;
const PER_PAGE = 100;
const MAX_PAGES = 10;
const GET_ATTEMPTS = 2;
const GET_TIMEOUT_MS = 5_000;
const POST_TIMEOUT_MS = 5_000;
const USER_AGENT = "tennis-court-watcher-scheduler-watchdog/1.0";

export type FetchLike = (
  input: string | URL | Request,
  init?: RequestInit,
) => Promise<Response>;

export interface RunsFetchResult {
  complete: boolean;
  runs: WorkflowRun[];
}

export type DispatchResult =
  | { outcome: "dispatch_accepted"; workflowRunId: number }
  | { outcome: "dispatch_failed"; workflowRunId: null }
  | { outcome: "dispatch_unknown"; workflowRunId: null };

export async function fetchCompleteWorkflowRuns(
  token: string,
  observedAt: Date,
  deadlineAt: number,
  fetchImpl: FetchLike = fetch,
  sleep: (milliseconds: number) => Promise<void> = delay,
): Promise<RunsFetchResult> {
  const collected: WorkflowRun[] = [];
  let expectedTotal: number | null = null;

  for (let page = 1; page <= MAX_PAGES; page += 1) {
    const url = new URL(
      `${API_ROOT}/actions/workflows/${WORKFLOW_FILE}/runs`,
    );
    url.searchParams.set("branch", "main");
    url.searchParams.set("exclude_pull_requests", "true");
    url.searchParams.set("per_page", String(PER_PAGE));
    url.searchParams.set("page", String(page));

    const response = await retryGitHubGet(
      url,
      token,
      deadlineAt,
      fetchImpl,
      sleep,
    );
    if (response === null) {
      return { complete: false, runs: [] };
    }

    let responseValue: unknown;
    try {
      responseValue = await response.json();
    } catch {
      return { complete: false, runs: [] };
    }
    const parsed = parseGitHubRunsPage(responseValue, observedAt);
    if (parsed === null) {
      return { complete: false, runs: [] };
    }
    if (expectedTotal === null) {
      expectedTotal = parsed.totalCount;
    } else if (expectedTotal !== parsed.totalCount) {
      // Concurrent list mutation can shift page boundaries. A later cron tick
      // will retry from a coherent first page.
      return { complete: false, runs: [] };
    }

    collected.push(...parsed.runs);
    if (
      !runsAreNewestFirst(collected) ||
      new Set(collected.map((run) => run.id)).size !== collected.length
    ) {
      return { complete: false, runs: [] };
    }
    if (
      pagesCoverLivenessWindow(
        collected,
        collected.length,
        parsed.totalCount,
        parsed.runs.length,
        observedAt,
        PER_PAGE,
      )
    ) {
      return { complete: true, runs: collected };
    }
  }

  return { complete: false, runs: [] };
}

export async function dispatchUpdateAvailability(
  token: string,
  deadlineAt: number,
  fetchImpl: FetchLike = fetch,
): Promise<DispatchResult> {
  let response: Response;
  try {
    response = await fetchImpl(
      `${API_ROOT}/actions/workflows/${WORKFLOW_FILE}/dispatches`,
      {
        method: "POST",
        headers: githubHeaders(token, true),
        body: JSON.stringify({
          ref: "main",
          inputs: { dry_run: false },
        }),
        signal: AbortSignal.timeout(
          Math.max(1, Math.min(POST_TIMEOUT_MS, deadlineAt - Date.now())),
        ),
      },
    );
  } catch {
    return { outcome: "dispatch_unknown", workflowRunId: null };
  }

  if (response.status >= 500) {
    return { outcome: "dispatch_unknown", workflowRunId: null };
  }
  if (response.status >= 400) {
    return { outcome: "dispatch_failed", workflowRunId: null };
  }
  if (response.status !== 200) {
    return { outcome: "dispatch_unknown", workflowRunId: null };
  }

  let responseValue: unknown;
  try {
    responseValue = await response.json();
  } catch {
    return { outcome: "dispatch_unknown", workflowRunId: null };
  }
  if (
    typeof responseValue !== "object" || responseValue === null ||
    Array.isArray(responseValue)
  ) {
    return { outcome: "dispatch_unknown", workflowRunId: null };
  }
  const workflowRunId = (responseValue as Record<string, unknown>)
    .workflow_run_id;
  if (!Number.isSafeInteger(workflowRunId) || (workflowRunId as number) <= 0) {
    return { outcome: "dispatch_unknown", workflowRunId: null };
  }

  return {
    outcome: "dispatch_accepted",
    workflowRunId: workflowRunId as number,
  };
}

async function retryGitHubGet(
  url: URL,
  token: string,
  deadlineAt: number,
  fetchImpl: FetchLike,
  sleep: (milliseconds: number) => Promise<void>,
): Promise<Response | null> {
  for (let attempt = 1; attempt <= GET_ATTEMPTS; attempt += 1) {
    if (Date.now() >= deadlineAt) {
      return null;
    }
    try {
      const response = await fetchImpl(url, {
        method: "GET",
        headers: githubHeaders(token, false),
        signal: AbortSignal.timeout(
          Math.max(1, Math.min(GET_TIMEOUT_MS, deadlineAt - Date.now())),
        ),
      });
      if (response.ok) {
        return response;
      }
      if (response.status < 500 && response.status !== 429) {
        return null;
      }
    } catch {
      // One bounded retry is allowed for read-only snapshots.
    }
    if (attempt < GET_ATTEMPTS) {
      const backoff = 150 * attempt;
      if (Date.now() + backoff >= deadlineAt) {
        return null;
      }
      await sleep(backoff);
    }
  }
  return null;
}

function githubHeaders(token: string, hasBody: boolean): HeadersInit {
  const headers: Record<string, string> = {
    accept: "application/vnd.github+json",
    authorization: `Bearer ${token}`,
    "user-agent": USER_AGENT,
    "x-github-api-version": "2026-03-10",
  };
  if (hasBody) {
    headers["content-type"] = "application/json";
  }
  return headers;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
