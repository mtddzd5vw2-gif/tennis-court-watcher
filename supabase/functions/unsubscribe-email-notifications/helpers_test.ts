import assert from "node:assert/strict";
import { test } from "node:test";
import {
  createEmailUnsubscribeHandler,
  MAX_FORM_BODY_BYTES,
} from "./helpers.ts";
import type { UnsubscribeDependencies } from "./helpers.ts";

const TOKEN = "123e4567-e89b-42d3-a456-426614174000";
const USER_ID = "223e4567-e89b-42d3-a456-426614174000";
const EMAIL = "member@example.test";
const WORKER_SECRET = "worker-secret-value-that-is-at-least-32-bytes";
const ENDPOINT =
  "https://project.supabase.co/functions/v1/unsubscribe-email-notifications";

function makeHandler(options: {
  unsubscribe?: UnsubscribeDependencies["unsubscribe"];
  logs?: string[];
  secret?: string | null;
} = {}) {
  return createEmailUnsubscribeHandler({
    getEnv: (name) =>
      ({
        SUPABASE_URL: "https://project.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY: "service-role-key",
        UNSUBSCRIBE_WORKER_SECRET: options.secret === null
          ? undefined
          : options.secret ?? WORKER_SECRET,
      })[name],
    unsubscribe: options.unsubscribe ?? (async () => ({
      data: { outcome: "processed" },
      error: null,
    })),
    log: (value) => options.logs?.push(value),
  });
}

function formRequest(
  body: URLSearchParams,
  query = "",
  workerSecretHeader: string | null = WORKER_SECRET,
): Request {
  const headers = new Headers({
    "content-type": "application/x-www-form-urlencoded",
  });
  if (workerSecretHeader !== null) {
    headers.set("x-unsubscribe-worker-secret", workerSecretHeader);
  }
  return new Request(`${ENDPOINT}${query}`, {
    method: "POST",
    headers,
    body,
  });
}

test("GET is not part of the internal contract and cannot unsubscribe", async () => {
  let unsubscribed = 0;
  const response = await makeHandler({
    unsubscribe: async () => {
      unsubscribed += 1;
      return { data: { outcome: "processed" }, error: null };
    },
  })(new Request(`${ENDPOINT}?token=${TOKEN}`));

  assert.equal(response.status, 405);
  assert.equal(response.headers.get("allow"), "POST");
  assert.equal(await response.text(), "");
  assert.equal(unsubscribed, 0);
});

test("missing or short configured secret returns 503 before RPC", async () => {
  let rpcCalls = 0;
  for (const secret of [null, "too-short"] as const) {
    const response = await makeHandler({
      secret,
      unsubscribe: async () => {
        rpcCalls += 1;
        return { data: { outcome: "processed" }, error: null };
      },
    })(
      formRequest(new URLSearchParams({ interaction: "human", token: TOKEN })),
    );
    assert.equal(response.status, 503);
  }
  assert.equal(rpcCalls, 0);
});

test("only a valid custom secret header authenticates before body or RPC processing", async () => {
  let rpcCalls = 0;
  const handler = makeHandler({
    unsubscribe: async () => {
      rpcCalls += 1;
      return { data: { outcome: "processed" }, error: null };
    },
  });
  const body = new URLSearchParams({ interaction: "human", token: TOKEN });

  const missing = await handler(formRequest(body, "", null));
  const invalid = await handler(formRequest(body, "", "x".repeat(40)));
  const authorizationOnlyRequest = formRequest(body, "", null);
  authorizationOnlyRequest.headers.set(
    "authorization",
    `Bearer ${WORKER_SECRET}`,
  );
  const authorizationOnly = await handler(authorizationOnlyRequest);

  assert.equal(missing.status, 401);
  assert.equal(invalid.status, 401);
  assert.equal(authorizationOnly.status, 401);
  assert.equal(rpcCalls, 0);
});

for (const interaction of ["human", "one_click"] as const) {
  test(`body token ${interaction} POST uses the unsubscribe RPC`, async () => {
    let observed: unknown = null;
    const response = await makeHandler({
      unsubscribe: async (_url, _key, args) => {
        observed = args;
        return { data: { outcome: "processed" }, error: null };
      },
    })(formRequest(new URLSearchParams({ interaction, token: TOKEN })));

    assert.equal(response.status, 200);
    assert.equal(await response.text(), "");
    assert.deepEqual(observed, { p_token: TOKEN });
  });
}

test("query token contract is removed and query values are never used", async () => {
  let unsubscribed = 0;
  const handler = makeHandler({
    unsubscribe: async () => {
      unsubscribed += 1;
      return { data: { outcome: "processed" }, error: null };
    },
  });

  const oldOneClick = await handler(formRequest(
    new URLSearchParams({ "List-Unsubscribe": "One-Click" }),
    `?token=${TOKEN}&user_id=${USER_ID}&email=${encodeURIComponent(EMAIL)}`,
  ));
  const bodyWithoutToken = await handler(formRequest(
    new URLSearchParams({ interaction: "one_click" }),
    `?token=${TOKEN}`,
  ));

  assert.equal(oldOneClick.status, 400);
  assert.equal(bodyWithoutToken.status, 400);
  assert.equal(unsubscribed, 0);
});

test("unknown, repeated, and malformed tokens have generic idempotent success", async () => {
  let rpcCalls = 0;
  const handler = makeHandler({
    unsubscribe: async () => {
      rpcCalls += 1;
      return { data: { outcome: "processed" }, error: null };
    },
  });

  const [first, repeated, malformed] = await Promise.all([
    handler(formRequest(
      new URLSearchParams({
        interaction: "human",
        token: TOKEN,
      }),
    )),
    handler(formRequest(
      new URLSearchParams({
        interaction: "human",
        token: TOKEN,
      }),
    )),
    handler(formRequest(
      new URLSearchParams({
        interaction: "human",
        token: "not-a-token",
      }),
    )),
  ]);

  assert.equal(first.status, 200);
  assert.equal(repeated.status, 200);
  assert.equal(malformed.status, 200);
  assert.equal(await first.text(), await malformed.text());
  assert.equal(await repeated.text(), "");
  assert.equal(rpcCalls, 2);
});

test("internal POST requires exactly interaction and token", async () => {
  const handler = makeHandler();
  const oldRfcBody = await handler(formRequest(
    new URLSearchParams({ "List-Unsubscribe": "One-Click", token: TOKEN }),
  ));
  const additionalField = await handler(formRequest(
    new URLSearchParams({
      interaction: "human",
      token: TOKEN,
      unexpected: "value",
    }),
  ));

  assert.equal(oldRfcBody.status, 400);
  assert.equal(additionalField.status, 400);
});

test("streaming POST over 2048 bytes is cancelled before unsubscribe", async () => {
  let cancelled = false;
  let rpcCalled = false;
  let chunkNumber = 0;
  const body = new ReadableStream<Uint8Array>({
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
  const logs: string[] = [];
  const response = await makeHandler({
    logs,
    unsubscribe: async () => {
      rpcCalled = true;
      return { data: { outcome: "processed" }, error: null };
    },
  })(
    new Request(
      ENDPOINT,
      {
        method: "POST",
        headers: {
          "content-type": "application/x-www-form-urlencoded",
          "x-unsubscribe-worker-secret": WORKER_SECRET,
        },
        body,
        duplex: "half",
      } as RequestInit & { duplex: "half" },
    ),
  );

  assert.equal(response.status, 400);
  assert.equal(rpcCalled, false);
  assert.equal(cancelled, true);
  assert.match(logs.join("\n"), /"outcome":"invalid_request"/);
});

test("invalid UTF-8 POST is rejected before unsubscribe", async () => {
  let rpcCalled = false;
  const response = await makeHandler({
    unsubscribe: async () => {
      rpcCalled = true;
      return { data: { outcome: "processed" }, error: null };
    },
  })(
    new Request(ENDPOINT, {
      method: "POST",
      headers: {
        "content-type": "application/x-www-form-urlencoded",
        "x-unsubscribe-worker-secret": WORKER_SECRET,
      },
      body: new Uint8Array([0xc3, 0x28]),
    }),
  );

  assert.equal(response.status, 400);
  assert.equal(rpcCalled, false);
});

test("custom logs contain only aggregate outcomes and interaction", async () => {
  const logs: string[] = [];
  const response = await makeHandler({ logs })(formRequest(
    new URLSearchParams({ interaction: "one_click", token: TOKEN }),
    `?user_id=${USER_ID}&email=${encodeURIComponent(EMAIL)}`,
  ));
  assert.equal(response.status, 200);

  const output = logs.join("\n");
  for (
    const forbidden of [
      TOKEN,
      WORKER_SECRET,
      USER_ID,
      EMAIL,
      ENDPOINT,
      "?token=",
      "authorization",
      "x-unsubscribe-worker-secret",
    ]
  ) {
    assert.equal(output.includes(forbidden), false);
  }
  assert.match(output, /"outcome":"processed"/);
  assert.match(output, /"interaction":"one_click"/);
});

test("database failures return a retryable 5xx response", async () => {
  const response = await makeHandler({
    unsubscribe: async () => ({ data: null, error: { code: "XX000" } }),
  })(formRequest(
    new URLSearchParams({
      interaction: "human",
      token: TOKEN,
    }),
  ));

  assert.equal(response.status, 502);
  assert.equal(await response.text(), "");
});
