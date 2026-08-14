import assert from "node:assert/strict";
import { test } from "node:test";
import {
  createEmailUnsubscribeHandler,
  MAX_FORM_BODY_BYTES,
  UnsubscribeDependencies,
} from "./helpers.ts";

const TOKEN = "123e4567-e89b-42d3-a456-426614174000";
const USER_ID = "223e4567-e89b-42d3-a456-426614174000";
const EMAIL = "member@example.test";
const ENDPOINT =
  "https://project.supabase.co/functions/v1/unsubscribe-email-notifications";

function makeHandler(options: {
  tokenExists?: UnsubscribeDependencies["tokenExists"];
  unsubscribe?: UnsubscribeDependencies["unsubscribe"];
  logs?: string[];
} = {}) {
  return createEmailUnsubscribeHandler({
    getEnv: (name) =>
      ({
        SUPABASE_URL: "https://project.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY: "service-role-key",
      })[name],
    tokenExists: options.tokenExists ?? (async () => ({
      data: true,
      error: null,
    })),
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
): Request {
  return new Request(`${ENDPOINT}${query}`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body,
  });
}

test("GET validates the token and renders confirmation without unsubscribe", async () => {
  let validated = 0;
  let unsubscribed = 0;
  const handler = makeHandler({
    tokenExists: async (_url, _key, args) => {
      assert.deepEqual(args, { p_token: TOKEN });
      validated += 1;
      return { data: true, error: null };
    },
    unsubscribe: async () => {
      unsubscribed += 1;
      return { data: { outcome: "processed" }, error: null };
    },
  });

  const response = await handler(new Request(`${ENDPOINT}?token=${TOKEN}`));
  const body = await response.text();

  assert.equal(response.status, 200);
  assert.equal(validated, 1);
  assert.equal(unsubscribed, 0);
  assert.match(body, /メール通知の停止/);
  assert.match(body, /method="post"/);
});

test("human confirmation POST unsubscribes and renders Japanese success", async () => {
  let observed: unknown = null;
  const response = await makeHandler({
    unsubscribe: async (_url, _key, args) => {
      observed = args;
      return { data: { outcome: "processed" }, error: null };
    },
  })(formRequest(
    new URLSearchParams({
      interaction: "human",
      token: TOKEN,
    }),
  ));

  assert.equal(response.status, 200);
  assert.deepEqual(observed, { p_token: TOKEN });
  assert.match(await response.text(), /メール通知を停止しました/);
});

test("RFC 8058 one-click POST uses query token and has an empty response", async () => {
  let observed: unknown = null;
  const response = await makeHandler({
    unsubscribe: async (_url, _key, args) => {
      observed = args;
      return { data: { outcome: "processed" }, error: null };
    },
  })(formRequest(
    new URLSearchParams({ "List-Unsubscribe": "One-Click" }),
    `?token=${TOKEN}`,
  ));

  assert.equal(response.status, 200);
  assert.deepEqual(observed, { p_token: TOKEN });
  assert.equal(await response.text(), "");
});

test("unknown and malformed tokens have the same generic responses", async () => {
  const handler = makeHandler({
    tokenExists: async () => ({ data: false, error: null }),
  });
  const [unknownGet, malformedGet, unknownPost, malformedPost] = await Promise
    .all([
      handler(new Request(`${ENDPOINT}?token=${TOKEN}`)),
      handler(new Request(`${ENDPOINT}?token=not-a-token`)),
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

  const unknownGetBody = await unknownGet.text();
  const malformedGetBody = await malformedGet.text();
  assert.match(unknownGetBody, /<h1>メール通知の停止<\/h1>/);
  assert.match(malformedGetBody, /<h1>メール通知の停止<\/h1>/);
  assert.equal(await unknownPost.text(), await malformedPost.text());
});

test("methods other than GET and POST return 405", async () => {
  const response = await makeHandler()(
    new Request(ENDPOINT, {
      method: "PUT",
    }),
  );
  assert.equal(response.status, 405);
  assert.equal(response.headers.get("allow"), "GET, POST");
});

test("one-click body must contain the single RFC 8058 key-value pair", async () => {
  const response = await makeHandler()(formRequest(
    new URLSearchParams({
      "List-Unsubscribe": "One-Click",
      unexpected: "value",
    }),
    `?token=${TOKEN}`,
  ));
  assert.equal(response.status, 400);
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
    new Request(`${ENDPOINT}?token=${TOKEN}`, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body,
    }),
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
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new Uint8Array([0xc3, 0x28]),
    }),
  );

  assert.equal(response.status, 400);
  assert.equal(rpcCalled, false);
});

test("logs contain aggregate outcomes but no capability or PII", async () => {
  const logs: string[] = [];
  const handler = makeHandler({ logs });
  const response = await handler(formRequest(
    new URLSearchParams({ "List-Unsubscribe": "One-Click" }),
    `?token=${TOKEN}&user_id=${USER_ID}&email=${encodeURIComponent(EMAIL)}`,
  ));
  assert.equal(response.status, 200);

  const output = logs.join("\n");
  for (const forbidden of [TOKEN, USER_ID, EMAIL, "?token="]) {
    assert.equal(output.includes(forbidden), false);
  }
  assert.match(output, /"outcome":"processed"/);
});

test("database failures return retryable 5xx responses", async () => {
  const getFailure = await makeHandler({
    tokenExists: async () => ({ data: null, error: { code: "XX000" } }),
  })(new Request(`${ENDPOINT}?token=${TOKEN}`));
  const postFailure = await makeHandler({
    unsubscribe: async () => ({ data: null, error: { code: "XX000" } }),
  })(formRequest(
    new URLSearchParams({ "List-Unsubscribe": "One-Click" }),
    `?token=${TOKEN}`,
  ));

  assert.equal(getFailure.status, 502);
  assert.equal(postFailure.status, 502);
});
