import assert from "node:assert/strict";
import { test } from "node:test";
import {
  createUnsubscribeWorkerHandler,
  MAX_FORM_BODY_BYTES,
} from "./index.ts";
import type { Env, UpstreamFetch } from "./index.ts";

const TOKEN = "123e4567-e89b-42d3-a456-426614174000";
const USER_ID = "223e4567-e89b-42d3-a456-426614174000";
const EMAIL = "member@example.test";
const WORKER_SECRET = "worker-secret-value-that-is-at-least-32-bytes";
const PUBLIC_URL = `https://unsubscribe.tenniscourtwatcher.com/u/${TOKEN}`;
const UPSTREAM_URL =
  "https://oocqyeariwuppkeaeioh.supabase.co/functions/v1/unsubscribe-email-notifications";
const ENV: Env = {
  SUPABASE_UNSUBSCRIBE_URL: UPSTREAM_URL,
  UNSUBSCRIBE_WORKER_SECRET: WORKER_SECRET,
};

function formRequest(
  url: string,
  body: URLSearchParams,
  headers: HeadersInit = {},
): Request {
  return new Request(url, {
    method: "POST",
    headers: {
      "content-type": "application/x-www-form-urlencoded",
      ...headers,
    },
    body,
  });
}

test("GET returns generic Japanese HTML without an upstream side effect", async () => {
  let upstreamCalls = 0;
  const handler = createUnsubscribeWorkerHandler(async () => {
    upstreamCalls += 1;
    return new Response(null, { status: 200 });
  });

  const validResponse = await handler(new Request(PUBLIC_URL), ENV);
  const invalidResponse = await handler(
    new Request("https://unsubscribe.tenniscourtwatcher.com/u/not-a-token"),
    ENV,
  );
  const validBody = await validResponse.text();
  const invalidBody = await invalidResponse.text();

  assert.equal(validResponse.status, 200);
  assert.equal(
    validResponse.headers.get("content-type"),
    "text/html; charset=utf-8",
  );
  assert.equal(validResponse.headers.get("cache-control"), "no-store");
  assert.equal(validResponse.headers.get("referrer-policy"), "no-referrer");
  assert.match(
    validResponse.headers.get("content-security-policy") ?? "",
    /form-action 'self'/,
  );
  assert.match(validBody, /<html lang="ja">/);
  assert.match(validBody, /メール通知の停止/);
  assert.match(validBody, /<form method="post">/);
  assert.equal(validBody, invalidBody);
  assert.equal(upstreamCalls, 0);
});

test("human POST sends only the custom secret header and body token", async () => {
  let observedUrl = "";
  let observedInit: RequestInit | undefined;
  const handler = createUnsubscribeWorkerHandler(async (input, init) => {
    observedUrl = input.toString();
    observedInit = init;
    return new Response(null, { status: 200 });
  });

  const response = await handler(
    formRequest(
      `${PUBLIC_URL}?utm_source=${USER_ID}&email=${encodeURIComponent(EMAIL)}`,
      new URLSearchParams({ interaction: "human" }),
    ),
    ENV,
  );

  assert.equal(response.status, 200);
  assert.match(await response.text(), /メール通知を停止しました/);
  assert.equal(observedUrl, UPSTREAM_URL);
  assert.equal(new URL(observedUrl).search, "");
  assert.equal(observedInit?.method, "POST");
  assert.equal(observedInit?.redirect, "manual");
  assert.equal(
    new Headers(observedInit?.headers).get("content-type"),
    "application/x-www-form-urlencoded",
  );
  assert.equal(
    new Headers(observedInit?.headers).get("authorization"),
    null,
  );
  assert.equal(
    new Headers(observedInit?.headers).get("x-unsubscribe-worker-secret"),
    WORKER_SECRET,
  );
  const upstreamBody = String(observedInit?.body);
  assert.deepEqual(
    Object.fromEntries(new URLSearchParams(upstreamBody)),
    { interaction: "human", token: TOKEN },
  );
  assert.equal(observedUrl.includes(TOKEN), false);
  assert.equal(observedUrl.includes(USER_ID), false);
  assert.equal(observedUrl.includes(EMAIL), false);
  assert.equal(observedUrl.includes(WORKER_SECRET), false);
  assert.equal(upstreamBody.includes(WORKER_SECRET), false);
});

test("missing or short Worker secret fails closed without an upstream call", async () => {
  let upstreamCalls = 0;
  const handler = createUnsubscribeWorkerHandler(async () => {
    upstreamCalls += 1;
    return new Response(null, { status: 200 });
  });

  const response = await handler(
    formRequest(PUBLIC_URL, new URLSearchParams({ interaction: "human" })),
    { ...ENV, UNSUBSCRIBE_WORKER_SECRET: "too-short" },
  );

  assert.equal(response.status, 503);
  assert.equal(upstreamCalls, 0);
});

test("production upstream rejects every unpinned HTTPS endpoint", async () => {
  let upstreamCalls = 0;
  const handler = createUnsubscribeWorkerHandler(async () => {
    upstreamCalls += 1;
    return new Response(null, { status: 200 });
  });
  const invalidEndpoints = [
    "https://another-project.supabase.co/functions/v1/unsubscribe-email-notifications",
    "https://example.com/functions/v1/unsubscribe-email-notifications",
    "https://oocqyeariwuppkeaeioh.supabase.co:443/functions/v1/unsubscribe-email-notifications",
  ];

  for (const endpoint of invalidEndpoints) {
    const response = await handler(
      formRequest(PUBLIC_URL, new URLSearchParams({ interaction: "human" })),
      { ...ENV, SUPABASE_UNSUBSCRIBE_URL: endpoint },
    );
    assert.equal(response.status, 503);
  }
  assert.equal(upstreamCalls, 0);
});

test("localhost upstream remains available for local tests", async () => {
  const localUrl =
    "http://127.0.0.1:54321/functions/v1/unsubscribe-email-notifications";
  let observedUrl = "";
  const response = await createUnsubscribeWorkerHandler(async (input) => {
    observedUrl = input.toString();
    return new Response(null, { status: 200 });
  })(
    formRequest(PUBLIC_URL, new URLSearchParams({ interaction: "human" })),
    { ...ENV, SUPABASE_UNSUBSCRIBE_URL: localUrl },
  );

  assert.equal(response.status, 200);
  assert.equal(observedUrl, localUrl);
});

test("RFC 8058 POST becomes body-only one_click upstream and returns blank 200", async () => {
  let upstreamBody = "";
  const handler = createUnsubscribeWorkerHandler(async (_input, init) => {
    upstreamBody = String(init?.body);
    return new Response("ignored upstream body", { status: 200 });
  });

  const response = await handler(
    formRequest(
      PUBLIC_URL,
      new URLSearchParams({ "List-Unsubscribe": "One-Click" }),
    ),
    ENV,
  );

  assert.equal(response.status, 200);
  assert.equal(await response.text(), "");
  assert.equal(response.headers.get("content-length"), "0");
  assert.deepEqual(Object.fromEntries(new URLSearchParams(upstreamBody)), {
    interaction: "one_click",
    token: TOKEN,
  });
});

test("malformed path tokens have the same generic successes without upstream calls", async () => {
  let upstreamCalls = 0;
  const handler = createUnsubscribeWorkerHandler(async () => {
    upstreamCalls += 1;
    return new Response(null, { status: 200 });
  });

  const [human, oneClick] = await Promise.all([
    handler(
      formRequest(
        "https://unsubscribe.tenniscourtwatcher.com/u/not-a-token",
        new URLSearchParams({ interaction: "human" }),
      ),
      ENV,
    ),
    handler(
      formRequest(
        "https://unsubscribe.tenniscourtwatcher.com/u/not-a-token",
        new URLSearchParams({ "List-Unsubscribe": "One-Click" }),
      ),
      ENV,
    ),
  ]);

  assert.equal(human.status, 200);
  assert.match(await human.text(), /メール通知を停止しました/);
  assert.equal(oneClick.status, 200);
  assert.equal(await oneClick.text(), "");
  assert.equal(upstreamCalls, 0);
});

test("POST body is bounded before any upstream request", async () => {
  let upstreamCalls = 0;
  const handler = createUnsubscribeWorkerHandler(async () => {
    upstreamCalls += 1;
    return new Response(null, { status: 200 });
  });

  const declaredTooLarge = await handler(
    formRequest(
      PUBLIC_URL,
      new URLSearchParams({ interaction: "human" }),
      { "content-length": String(MAX_FORM_BODY_BYTES + 1) },
    ),
    ENV,
  );

  let cancelled = false;
  let chunkNumber = 0;
  const stream = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (chunkNumber === 0) {
        controller.enqueue(new Uint8Array(MAX_FORM_BODY_BYTES).fill(0x61));
      } else {
        controller.enqueue(new Uint8Array([0x61]));
      }
      chunkNumber += 1;
    },
    cancel() {
      cancelled = true;
    },
  });
  const streamingTooLarge = await handler(
    new Request(
      PUBLIC_URL,
      {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: stream,
        duplex: "half",
      } as RequestInit & { duplex: "half" },
    ),
    ENV,
  );

  assert.equal(declaredTooLarge.status, 400);
  assert.equal(streamingTooLarge.status, 400);
  assert.equal(cancelled, true);
  assert.equal(upstreamCalls, 0);
});

test("Supabase failures remain retryable 5xx and are never redirected", async () => {
  const upstreamFailure = await createUnsubscribeWorkerHandler(
    async () => new Response(null, { status: 503 }),
  )(
    formRequest(
      PUBLIC_URL,
      new URLSearchParams({ interaction: "human" }),
    ),
    ENV,
  );
  const upstreamRedirect = await createUnsubscribeWorkerHandler(
    async () => new Response(null, { status: 302 }),
  )(
    formRequest(
      PUBLIC_URL,
      new URLSearchParams({ "List-Unsubscribe": "One-Click" }),
    ),
    ENV,
  );
  const networkFailure: UpstreamFetch = async () => {
    throw new Error("network failure");
  };
  const unavailable = await createUnsubscribeWorkerHandler(networkFailure)(
    formRequest(
      PUBLIC_URL,
      new URLSearchParams({ "List-Unsubscribe": "One-Click" }),
    ),
    ENV,
  );

  assert.equal(upstreamFailure.status, 503);
  assert.equal(upstreamRedirect.status, 502);
  assert.equal(unavailable.status, 502);
  assert.equal(await upstreamRedirect.text(), "");
  assert.equal(await unavailable.text(), "");
});

test("unsupported form contracts and methods are rejected", async () => {
  const handler = createUnsubscribeWorkerHandler();
  const invalidForm = await handler(
    formRequest(
      PUBLIC_URL,
      new URLSearchParams({
        "List-Unsubscribe": "One-Click",
        unexpected: "value",
      }),
    ),
    ENV,
  );
  const method = await handler(new Request(PUBLIC_URL, { method: "PUT" }), ENV);

  assert.equal(invalidForm.status, 400);
  assert.equal(method.status, 405);
  assert.equal(method.headers.get("allow"), "GET, POST");
});
