import assert from "node:assert/strict";
import { test } from "node:test";
import {
  createLineWebhookHandler,
  extractRelevantLineEvents,
  verifyLineWebhookSignature,
} from "./helpers.ts";

const SECRET = "line-messaging-channel-secret";
const USER_ID = `U${"a".repeat(32)}`;
const NOW = Date.UTC(2026, 7, 21, 10, 0, 0);

async function signature(body: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(SECRET),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const bytes = new Uint8Array(await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(body),
  ));
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
  assert.throws(() => extractRelevantLineEvents({
    events: [{
      type: "unfollow",
      webhookEventId: "bad id",
      timestamp: NOW,
      source: { type: "user", userId: USER_ID },
    }],
  }, NOW));
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
    getEnv: (name) => ({
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

  const rejected = await handler(new Request("https://example.test", {
    method: "POST",
    body,
    headers: { "x-line-signature": "invalid" },
  }));
  assert.equal(rejected.status, 401);

  const accepted = await handler(new Request("https://example.test", {
    method: "POST",
    body,
    headers: { "x-line-signature": await signature(body) },
  }));
  assert.equal(accepted.status, 200);
  assert.deepEqual(await accepted.json(), {
    relevant_event_count: 1,
    inserted_event_count: 1,
    updated_link_count: 1,
  });
  assert.equal((recorded as unknown[]).length, 1);
});
