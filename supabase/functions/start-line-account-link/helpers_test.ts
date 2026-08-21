import assert from "node:assert/strict";
import { test } from "node:test";
import {
  buildAuthorizationUrl,
  createStartLineAccountLinkHandler,
} from "./helpers.ts";
import type { StartLineAccountLinkDependencies } from "./helpers.ts";

const ENDPOINT =
  "https://project.supabase.co/functions/v1/start-line-account-link";
const CALLBACK =
  "https://project.supabase.co/functions/v1/complete-line-account-link";
const ORIGIN = "https://tenniscourtwatcher.com";
const TOKEN = "header.payload.signature";

function request(options: {
  body?: string | null;
  method?: string;
  origin?: string | null;
  token?: string | null;
} = {}): Request {
  const headers = new Headers();
  if (options.origin !== null) {
    headers.set("origin", options.origin ?? ORIGIN);
  }
  if (options.token !== null) {
    headers.set("authorization", `Bearer ${options.token ?? TOKEN}`);
  }
  const body = options.body === undefined ? "{}" : options.body;
  if (body !== null) {
    headers.set("content-type", "application/json");
  }
  return new Request(ENDPOINT, {
    method: options.method ?? "POST",
    headers,
    body: (options.method ?? "POST") === "POST" ? body : undefined,
  });
}

function makeHandler(options: {
  createSession?: StartLineAccountLinkDependencies["createSession"];
  callbackUrl?: string | null;
  logs?: string[];
  randomBytes?: (length: number) => Uint8Array;
} = {}) {
  let randomCall = 0;
  return createStartLineAccountLinkHandler({
    getEnv: (name) =>
      ({
        SUPABASE_URL: "https://project.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY: "service-role-key",
        LINE_LOGIN_CHANNEL_ID: "1234567890",
        LINE_LOGIN_CALLBACK_URL: options.callbackUrl === null
          ? undefined
          : options.callbackUrl ?? CALLBACK,
      })[name],
    createSession: options.createSession ?? (async () => "created"),
    randomBytes: options.randomBytes ?? ((length) => {
      randomCall += 1;
      return new Uint8Array(length).fill(randomCall);
    }),
    now: () => new Date("2026-08-21T08:00:00.000Z"),
    log: (value) => options.logs?.push(value),
  });
}

test("creates hashed session and returns a safe LINE authorization URL", async () => {
  let observed: Parameters<StartLineAccountLinkDependencies["createSession"]>[0]
    | null = null;
  const response = await makeHandler({
    createSession: async (input) => {
      observed = input;
      return "created";
    },
  })(request());

  assert.equal(response.status, 200);
  assert.ok(observed);
  assert.match(observed.stateHash, /^[0-9a-f]{64}$/);
  assert.match(observed.nonceHash, /^[0-9a-f]{64}$/);
  assert.notEqual(observed.stateHash, observed.nonceHash);
  assert.equal(observed.accessToken, TOKEN);
  assert.equal(observed.expiresAt, "2026-08-21T08:09:00.000Z");

  const payload = await response.json();
  const url = new URL(payload.authorization_url);
  assert.equal(url.origin, "https://access.line.me");
  assert.equal(url.pathname, "/oauth2/v2.1/authorize");
  assert.equal(url.searchParams.get("response_type"), "code");
  assert.equal(url.searchParams.get("client_id"), "1234567890");
  assert.equal(url.searchParams.get("redirect_uri"), CALLBACK);
  assert.equal(url.searchParams.get("scope"), "openid profile");
  assert.equal(url.searchParams.get("bot_prompt"), "aggressive");
  assert.match(url.searchParams.get("state") ?? "", /^[0-9a-f]{64}$/);
  assert.match(url.searchParams.get("nonce") ?? "", /^[0-9a-f]{64}$/);
  assert.equal(payload.authorization_url.includes(TOKEN), false);
});

test("authorization URL contains no email scope or browser identity", () => {
  const url = buildAuthorizationUrl({
    channelId: "1234567890",
    callbackUrl: CALLBACK,
    state: "a".repeat(64),
    nonce: "b".repeat(64),
  });
  assert.equal(url.includes("email"), false);
  assert.equal(url.includes("user_id"), false);
  assert.equal(url.includes("access_token"), false);
});

test("rejects missing bearer token and forbidden origin", async () => {
  assert.equal((await makeHandler()(request({ token: null }))).status, 401);
  assert.equal(
    (await makeHandler()(request({ origin: "https://example.invalid" }))).status,
    403,
  );
});

test("accepts only an empty JSON object", async () => {
  assert.equal((await makeHandler()(request({ body: "" }))).status, 200);
  assert.equal((await makeHandler()(request({ body: "{\"x\":1}" }))).status, 400);
  assert.equal((await makeHandler()(request({ body: "x".repeat(17) }))).status, 400);
});

test("invalid callback configuration fails closed", async () => {
  assert.equal(
    (await makeHandler({ callbackUrl: "https://example.com/callback" })(request()))
      .status,
    503,
  );
  assert.equal((await makeHandler({ callbackUrl: null })(request())).status, 503);
});

test("inactive membership remains forbidden", async () => {
  const response = await makeHandler({
    createSession: async () => "inactive_member",
  })(request());
  assert.equal(response.status, 403);
});

test("identical random values fail closed", async () => {
  const response = await makeHandler({
    randomBytes: (length) => new Uint8Array(length).fill(7),
  })(request());
  assert.equal(response.status, 503);
});

test("logs contain outcomes but no OAuth material", async () => {
  const logs: string[] = [];
  const response = await makeHandler({ logs })(request());
  const payload = await response.json();
  assert.equal(logs.some((value) => value.includes(TOKEN)), false);
  assert.equal(
    logs.some((value) => value.includes(payload.authorization_url)),
    false,
  );
  assert.deepEqual(logs.map((value) => JSON.parse(value)), [{ outcome: "created" }]);
});
