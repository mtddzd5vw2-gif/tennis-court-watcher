import assert from "node:assert/strict";
import { test } from "node:test";
import { createUnlinkLineAccountHandler } from "./helpers.ts";
import type { UnlinkLineAccountDependencies } from "./helpers.ts";

const ENDPOINT = "https://project.supabase.co/functions/v1/unlink-line-account";
const ORIGIN = "https://tenniscourtwatcher.com";
const TOKEN = "header.payload.signature";

function request(options: {
  body?: unknown;
  method?: string;
  origin?: string | null;
  token?: string | null;
} = {}): Request {
  const headers = new Headers({ "content-type": "application/json" });
  if (options.origin !== null) {
    headers.set("origin", options.origin ?? ORIGIN);
  }
  if (options.token !== null) {
    headers.set("authorization", `Bearer ${options.token ?? TOKEN}`);
  }
  return new Request(ENDPOINT, {
    method: options.method ?? "POST",
    headers,
    body: (options.method ?? "POST") === "POST"
      ? JSON.stringify(options.body ?? { confirmation: "unlink-line-account" })
      : undefined,
  });
}

function makeHandler(options: {
  unlinkAccount?: UnlinkLineAccountDependencies["unlinkAccount"];
  logs?: string[];
} = {}) {
  return createUnlinkLineAccountHandler({
    getEnv: (name) =>
      ({
        SUPABASE_URL: "https://project.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY: "service-role-key",
      })[name],
    unlinkAccount: options.unlinkAccount ?? (async () => "unlinked"),
    log: (value) => options.logs?.push(value),
  });
}

test("explicit authenticated confirmation unlinks idempotently", async () => {
  let observedToken = "";
  const response = await makeHandler({
    unlinkAccount: async (input) => {
      observedToken = input.accessToken;
      return "unlinked";
    },
  })(request());
  assert.equal(response.status, 204);
  assert.equal(observedToken, TOKEN);
  assert.equal(await response.text(), "");

  const repeated = await makeHandler({
    unlinkAccount: async () => "not_linked",
  })(request());
  assert.equal(repeated.status, 204);
});

test("body cannot choose another account", async () => {
  let called = false;
  const response = await makeHandler({
    unlinkAccount: async () => {
      called = true;
      return "unlinked";
    },
  })(request({ body: { confirmation: "unlink-line-account", user_id: "other" } }));
  assert.equal(response.status, 400);
  assert.equal(called, false);
});

test("missing JWT, wrong origin, and inactive member fail closed", async () => {
  assert.equal((await makeHandler()(request({ token: null }))).status, 401);
  assert.equal(
    (await makeHandler()(request({ origin: "https://example.invalid" }))).status,
    403,
  );
  assert.equal(
    (await makeHandler({ unlinkAccount: async () => "inactive_member" })(request()))
      .status,
    403,
  );
});

test("logs never contain bearer token", async () => {
  const logs: string[] = [];
  await makeHandler({ logs })(request());
  assert.equal(logs.some((entry) => entry.includes(TOKEN)), false);
  assert.deepEqual(logs.map((entry) => JSON.parse(entry)), [{ outcome: "unlinked" }]);
});
