import assert from "node:assert/strict";
import { test } from "node:test";
import {
  createLineWebhookHandler,
  extractRelevantLineEvents,
  normalizeLegacyWebhookUrl,
  verifyLineWebhookSignature,
} from "./helpers.ts";

const SECRET = "line-messaging-channel-secret";
const USER_ID = `U${"a".repeat(32)}`;
const NOW = Date.UTC(2026, 7, 21, 10, 0, 0);
const LEGACY_WEBHOOK_URL = `https://script.google.com/macros/s/${
  "A".repeat(40)
}/exec`;

async function signature(body: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(SECRET),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const bytes = new Uint8Array(
    await crypto.subtle.sign(
      "HMAC",
      key,
      new TextEncoder().encode(body),
    ),
  );
  return Buffer.from(bytes).toString("base64");
}

test("signature verification uses the untouched body", async () => {
  const body = '{"events":[]}\n';
  const value = await signature(body);
  assert.equal(
    await verifyLineWebhookSignature(
      new TextEncoder().encode(body),
      SECRET,
      value,
    ),
    true,
  );
  assert.equal(
    await verifyLineWebhookSignature(
      new TextEncoder().encode(body.trim()),
      SECRET,
      value,
    ),
    false,
  );
});

test("only valid user follow state events are normalized", () => {
  const events = extractRelevantLineEvents({
    events: [
      {
        type: "follow",
        webhookEventId: "01TESTFOLLOWEVENT0000000000",
        timestamp: NOW,
        source: { type: "user", userId: USER_ID },
      },
      {
        type: "message",
        webhookEventId: "01IGNORED00000000000000000",
        timestamp: NOW,
        source: { type: "user", userId: USER_ID },
      },
    ],
  }, NOW);
  assert.deepEqual(events, [{
    webhook_event_id: "01TESTFOLLOWEVENT0000000000",
    event_type: "follow",
    line_user_id: USER_ID,
    occurred_at: "2026-08-21T10:00:00.000Z",
  }]);
  assert.throws(() =>
    extractRelevantLineEvents({
      events: [{
        type: "unfollow",
        webhookEventId: "bad id",
        timestamp: NOW,
        source: { type: "user", userId: USER_ID },
      }],
    }, NOW)
  );
});

test("legacy webhook URL is restricted to one HTTPS Apps Script deployment", () => {
  assert.equal(
    normalizeLegacyWebhookUrl(LEGACY_WEBHOOK_URL),
    LEGACY_WEBHOOK_URL,
  );
  for (
    const value of [
      "http://script.google.com/macros/s/AAAAAAAAAAAAAAAAAAAA/exec",
      "https://evil.example/macros/s/AAAAAAAAAAAAAAAAAAAA/exec",
      "https://script.google.com/macros/s/short/exec",
      `${LEGACY_WEBHOOK_URL}?redirect=https://evil.example`,
      "https://script.google.com/macros/s/AAAAAAAAAAAAAAAAAAAA/dev",
    ]
  ) {
    assert.equal(normalizeLegacyWebhookUrl(value), null);
  }
});

test("handler rejects bad signatures and returns aggregate metrics only", async () => {
  const body = JSON.stringify({
    events: [{
      type: "unfollow",
      webhookEventId: "01TESTUNFOLLOWEVENT00000000",
      timestamp: NOW,
      source: { type: "user", userId: USER_ID },
    }],
  });
  let recorded: unknown = null;
  const handler = createLineWebhookHandler({
    getEnv: (name) =>
      ({
        LINE_MESSAGING_CHANNEL_SECRET: SECRET,
        SUPABASE_URL: "https://abcdefghijklmnopqrst.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY: "service-role-secret",
      })[name],
    now: () => NOW,
    recordEvents: async (_url, _key, events) => {
      recorded = events;
      return {
        data: [{
          relevant_event_count: 1,
          inserted_event_count: 1,
          updated_link_count: 1,
        }],
        error: null,
      };
    },
  });

  const rejected = await handler(
    new Request("https://example.test", {
      method: "POST",
      body,
      headers: { "x-line-signature": "invalid" },
    }),
  );
  assert.equal(rejected.status, 401);

  const accepted = await handler(
    new Request("https://example.test", {
      method: "POST",
      body,
      headers: { "x-line-signature": await signature(body) },
    }),
  );
  assert.equal(accepted.status, 200);
  assert.deepEqual(await accepted.json(), {
    relevant_event_count: 1,
    inserted_event_count: 1,
    updated_link_count: 1,
  });
  assert.equal((recorded as unknown[]).length, 1);
});

test("bridge forwards the untouched signed body after database processing", async () => {
  const body = JSON.stringify({
    destination: `U${"b".repeat(32)}`,
    events: [{
      type: "follow",
      webhookEventId: "01TESTBRIDGEFOLLOW000000000",
      timestamp: NOW,
      source: { type: "user", userId: USER_ID },
    }],
  });
  const suppliedSignature = await signature(body);
  let recorded = false;
  let forwarded: { url: string; body: string; signature: string } | null = null;
  const handler = createLineWebhookHandler({
    getEnv: (name) =>
      ({
        LINE_MESSAGING_CHANNEL_SECRET: SECRET,
        LINE_WEBHOOK_BRIDGE_ENABLED: "true",
        LINE_LEGACY_WEBHOOK_URL: LEGACY_WEBHOOK_URL,
        SUPABASE_URL: "https://abcdefghijklmnopqrst.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY: "service-role-secret",
      })[name],
    now: () => NOW,
    recordEvents: async () => {
      recorded = true;
      return {
        data: [{
          relevant_event_count: 1,
          inserted_event_count: 1,
          updated_link_count: 1,
        }],
        error: null,
      };
    },
    forwardLegacyWebhook: async (url, rawBody, webhookSignature) => {
      assert.equal(recorded, true);
      forwarded = {
        url,
        body: new TextDecoder().decode(rawBody),
        signature: webhookSignature,
      };
      return true;
    },
  });

  const rejected = await handler(
    new Request("https://example.test", {
      method: "POST",
      body,
      headers: { "x-line-signature": "invalid" },
    }),
  );
  assert.equal(rejected.status, 401);
  assert.equal(recorded, false);
  assert.equal(forwarded, null);

  const response = await handler(
    new Request("https://example.test", {
      method: "POST",
      body,
      headers: { "x-line-signature": suppliedSignature },
    }),
  );
  assert.equal(response.status, 200);
  assert.deepEqual(forwarded, {
    url: LEGACY_WEBHOOK_URL,
    body,
    signature: suppliedSignature,
  });
});

test("bridge preserves message-only and empty verification webhooks", async () => {
  for (
    const events of [
      [{
        type: "message",
        webhookEventId: "01TESTBRIDGEMESSAGE00000000",
        timestamp: NOW,
        source: { type: "user", userId: USER_ID },
        message: { type: "text", id: "1", text: "test" },
      }],
      [],
    ]
  ) {
    const body = JSON.stringify({ destination: `U${"b".repeat(32)}`, events });
    let forwardedBody = "";
    const handler = createLineWebhookHandler({
      getEnv: (name) =>
        ({
          LINE_MESSAGING_CHANNEL_SECRET: SECRET,
          LINE_WEBHOOK_BRIDGE_ENABLED: "true",
          LINE_LEGACY_WEBHOOK_URL: LEGACY_WEBHOOK_URL,
          SUPABASE_URL: "https://abcdefghijklmnopqrst.supabase.co",
          SUPABASE_SERVICE_ROLE_KEY: "service-role-secret",
        })[name],
      recordEvents: async () => {
        throw new Error("message-only webhook must not touch follow state");
      },
      forwardLegacyWebhook: async (_url, rawBody) => {
        forwardedBody = new TextDecoder().decode(rawBody);
        return true;
      },
    });
    const response = await handler(
      new Request("https://example.test", {
        method: "POST",
        body,
        headers: { "x-line-signature": await signature(body) },
      }),
    );
    assert.equal(response.status, 200);
    assert.equal(forwardedBody, body);
    assert.deepEqual(await response.json(), {
      relevant_event_count: 0,
      inserted_event_count: 0,
      updated_link_count: 0,
    });
  }
});

test("bridge fails closed on configuration or legacy delivery errors", async () => {
  const body = '{"events":[]}';
  const base = {
    LINE_MESSAGING_CHANNEL_SECRET: SECRET,
    LINE_WEBHOOK_BRIDGE_ENABLED: "true",
    SUPABASE_URL: "https://abcdefghijklmnopqrst.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "service-role-secret",
  };
  const missingUrl = createLineWebhookHandler({
    getEnv: (name) => base[name as keyof typeof base],
    recordEvents: async () => ({ data: [], error: null }),
    forwardLegacyWebhook: async () => true,
  });
  assert.equal(
    (await missingUrl(
      new Request("https://example.test", {
        method: "POST",
        body,
        headers: { "x-line-signature": await signature(body) },
      }),
    )).status,
    503,
  );

  const failedForward = createLineWebhookHandler({
    getEnv: (name) =>
      ({
        ...base,
        LINE_LEGACY_WEBHOOK_URL: LEGACY_WEBHOOK_URL,
      })[name],
    recordEvents: async () => ({ data: [], error: null }),
    forwardLegacyWebhook: async () => false,
  });
  assert.equal(
    (await failedForward(
      new Request("https://example.test", {
        method: "POST",
        body,
        headers: { "x-line-signature": await signature(body) },
      }),
    )).status,
    502,
  );
});
