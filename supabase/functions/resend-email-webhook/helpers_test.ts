import assert from "node:assert/strict";
import { test } from "node:test";
import { Webhook } from "npm:svix@1.99.1";
import {
  createResendWebhookHandler,
  MAX_WEBHOOK_BODY_BYTES,
  RecordResendEmailEventArgs,
  WebhookDependencies,
} from "./helpers.ts";

const SIGNING_SECRET = `whsec_${btoa("01234567890123456789012345678901")}`;
const SVIX_ID = "msg_2KWPBgLlAfxdpx2AI54pPJ85f4W";
const MESSAGE_ID = "123e4567-e89b-42d3-a456-426614174000";
const PROVIDER_MESSAGE_ID = "56761188-7520-42d8-8898-ff6fc54ce618";

function supportedPayload(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    type: "email.delivered",
    created_at: new Date().toISOString(),
    data: {
      email_id: PROVIDER_MESSAGE_ID,
      tags: {
        tcw_source: "user_notification",
        tcw_message_id: MESSAGE_ID,
      },
      to: ["member@example.test"],
      from: "notify@example.test",
      subject: "Private subject",
    },
    ...overrides,
  };
}

function signedRequest(
  rawBody: string,
  options: {
    method?: string;
    origin?: string;
    signature?: string;
  } = {},
): Request {
  const timestamp = new Date();
  const webhook = new Webhook(SIGNING_SECRET);
  const signature = options.signature ??
    webhook.sign(SVIX_ID, timestamp, rawBody);
  const headers = new Headers({
    "content-type": "application/json",
    "svix-id": SVIX_ID,
    "svix-timestamp": String(Math.floor(timestamp.getTime() / 1000)),
    "svix-signature": signature,
  });
  if (options.origin !== undefined) {
    headers.set("origin", options.origin);
  }
  return new Request("https://example.test/resend-email-webhook", {
    method: options.method ?? "POST",
    headers,
    body: options.method === "GET" ? undefined : rawBody,
  });
}

function makeHandler(options: {
  recordEvent?: WebhookDependencies["recordEvent"];
  getEnv?: WebhookDependencies["getEnv"];
  logs?: string[];
} = {}) {
  return createResendWebhookHandler({
    getEnv: options.getEnv ?? ((name) =>
      ({
        RESEND_WEBHOOK_SIGNING_SECRET: SIGNING_SECRET,
        SUPABASE_URL: "https://project.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY: "service-role-key",
      })[name]),
    recordEvent: options.recordEvent ?? (async () => ({
      data: {
        outcome: "recorded",
        stored_event_count: 1,
        preference_disabled_count: 0,
      },
      error: null,
    })),
    log: (value) => options.logs?.push(value),
  });
}

async function responseBody(response: Response) {
  return await response.json() as Record<string, unknown>;
}

test("valid signed webhook records only the required fields", async () => {
  let observed: RecordResendEmailEventArgs | null = null;
  const rawBody = JSON.stringify(supportedPayload());
  const handler = makeHandler({
    recordEvent: async (_url, _key, args) => {
      observed = args;
      return {
        data: {
          outcome: "recorded",
          stored_event_count: 1,
          preference_disabled_count: 0,
        },
        error: null,
      };
    },
  });

  const response = await handler(signedRequest(rawBody));

  assert.equal(response.status, 200);
  assert.deepEqual(observed, {
    p_provider_event_id: SVIX_ID,
    p_provider_message_id: PROVIDER_MESSAGE_ID,
    p_event_type: "email.delivered",
    p_occurred_at: (JSON.parse(rawBody) as Record<string, string>).created_at,
    p_source_tag: "user_notification",
    p_message_id_tag: MESSAGE_ID,
  });
  assert.deepEqual(await responseBody(response), {
    outcome: "recorded",
    event_type: "email.delivered",
    stored_event_count: 1,
    preference_disabled_count: 0,
  });
});

test("signature verification uses the exact raw body", async () => {
  const rawBody = JSON.stringify(supportedPayload(), null, 2) + "\n";
  const response = await makeHandler()(signedRequest(rawBody));

  assert.equal(response.status, 200);
  assert.equal((await responseBody(response)).outcome, "recorded");
});

test("invalid signature is rejected", async () => {
  const rawBody = JSON.stringify(supportedPayload());
  const response = await makeHandler()(
    signedRequest(rawBody, { signature: "v1,invalid" }),
  );

  assert.equal(response.status, 401);
  assert.equal((await responseBody(response)).outcome, "unauthorized");
});

test("missing Svix headers are rejected", async () => {
  const response = await makeHandler()(
    new Request(
      "https://example.test/resend-email-webhook",
      { method: "POST", body: "{}" },
    ),
  );

  assert.equal(response.status, 401);
});

test("only POST is allowed", async () => {
  const response = await makeHandler()(signedRequest("", { method: "GET" }));
  assert.equal(response.status, 405);
});

test("browser origins are rejected", async () => {
  const rawBody = JSON.stringify(supportedPayload());
  const response = await makeHandler()(
    signedRequest(rawBody, { origin: "https://attacker.example" }),
  );
  assert.equal(response.status, 403);
});

test("oversized bodies are rejected", async () => {
  const body = "x".repeat(MAX_WEBHOOK_BODY_BYTES + 1);
  const response = await makeHandler()(signedRequest(body));
  assert.equal(response.status, 413);
});

test("signed malformed JSON returns 400", async () => {
  const response = await makeHandler()(signedRequest('{"type":'));
  assert.equal(response.status, 400);
});

test("signed malformed supported event returns 422", async () => {
  const rawBody = JSON.stringify(supportedPayload({ data: { tags: {} } }));
  const response = await makeHandler()(signedRequest(rawBody));
  assert.equal(response.status, 422);
  assert.equal((await responseBody(response)).event_type, "email.delivered");
});

test("valid unsupported event is ignored without an RPC", async () => {
  let rpcCalled = false;
  const rawBody = JSON.stringify(supportedPayload({ type: "email.opened" }));
  const response = await makeHandler({
    recordEvent: async () => {
      rpcCalled = true;
      throw new Error("unexpected RPC");
    },
  })(signedRequest(rawBody));

  assert.equal(response.status, 200);
  assert.equal((await responseBody(response)).outcome, "ignored_unsupported");
  assert.equal(rpcCalled, false);
});

test("database errors return a retryable non-200 response", async () => {
  const rawBody = JSON.stringify(supportedPayload());
  const response = await makeHandler({
    recordEvent: async () => ({ data: null, error: { code: "XX000" } }),
  })(signedRequest(rawBody));

  assert.equal(response.status, 502);
  assert.equal((await responseBody(response)).outcome, "retryable_error");
});

test("duplicate RPC results return 200", async () => {
  const rawBody = JSON.stringify(supportedPayload());
  const response = await makeHandler({
    recordEvent: async () => ({
      data: {
        outcome: "duplicate",
        stored_event_count: 0,
        preference_disabled_count: 0,
      },
      error: null,
    }),
  })(signedRequest(rawBody));

  assert.equal(response.status, 200);
  assert.equal((await responseBody(response)).outcome, "duplicate");
});

test("responses and logs contain aggregates but no PII or identifiers", async () => {
  const logs: string[] = [];
  const rawBody = JSON.stringify(supportedPayload());
  const response = await makeHandler({ logs })(signedRequest(rawBody));
  const serializedResponse = JSON.stringify(await responseBody(response));
  const output = `${logs.join("\n")}\n${serializedResponse}`;

  for (
    const forbidden of [
      "member@example.test",
      "notify@example.test",
      "Private subject",
      PROVIDER_MESSAGE_ID,
      MESSAGE_ID,
      SVIX_ID,
    ]
  ) {
    assert.doesNotMatch(output, new RegExp(forbidden));
  }
  assert.match(output, /"outcome":"recorded"/);
  assert.match(output, /"event_type":"email.delivered"/);
});

test("missing webhook configuration returns 503", async () => {
  const rawBody = JSON.stringify(supportedPayload());
  const response = await makeHandler({ getEnv: () => undefined })(
    signedRequest(rawBody),
  );
  assert.equal(response.status, 503);
});
