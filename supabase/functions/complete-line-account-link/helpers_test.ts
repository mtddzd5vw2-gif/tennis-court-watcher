import assert from "node:assert/strict";
import { test } from "node:test";
import { createCompleteLineAccountLinkHandler } from "./helpers.ts";
import type {
  CompleteLineAccountLinkDependencies,
  CompleteLineAccountLinkOutcome,
} from "./helpers.ts";

const ENDPOINT =
  "https://project.supabase.co/functions/v1/complete-line-account-link";
const RESULT = "https://tenniscourtwatcher.com/account/index.html";
const CODE = "valid-authorization-code";
const STATE = "a".repeat(64);

function callbackUrl(query = `code=${CODE}&state=${STATE}`): string {
  return `${ENDPOINT}?${query}`;
}

function makeHandler(options: {
  completeLink?: CompleteLineAccountLinkDependencies["completeLink"];
  logs?: string[];
  resultUrl?: string | null;
} = {}) {
  return createCompleteLineAccountLinkHandler({
    getEnv: (name) =>
      name === "LINE_LINK_RESULT_URL"
        ? options.resultUrl === null
          ? undefined
          : options.resultUrl ?? RESULT
        : undefined,
    completeLink: options.completeLink ?? (async () => "linked"),
    log: (value) => options.logs?.push(value),
  });
}

test("valid callback redirects with only a coarse success result", async () => {
  let observed: { code: string; state: string } | null = null;
  const response = await makeHandler({
    completeLink: async (input) => {
      observed = input;
      return "linked";
    },
  })(new Request(callbackUrl()));

  assert.equal(response.status, 303);
  assert.deepEqual(observed, { code: CODE, state: STATE });
  const location = response.headers.get("location") ?? "";
  assert.equal(location, `${RESULT}?line_link=success`);
  assert.equal(location.includes(CODE), false);
  assert.equal(location.includes(STATE), false);
  assert.equal(response.headers.get("referrer-policy"), "no-referrer");
});

test("provider cancellation does not exchange an authorization code", async () => {
  let called = false;
  const response = await makeHandler({
    completeLink: async () => {
      called = true;
      return "linked";
    },
  })(new Request(callbackUrl(`error=access_denied&state=${STATE}`)));
  assert.equal(response.status, 303);
  assert.equal(response.headers.get("location"), `${RESULT}?line_link=cancelled`);
  assert.equal(called, false);
});

test("duplicate and malformed callback parameters fail before exchange", async () => {
  let called = false;
  const handler = makeHandler({
    completeLink: async () => {
      called = true;
      return "linked";
    },
  });
  const duplicate = await handler(
    new Request(callbackUrl(`code=${CODE}&code=other&state=${STATE}`)),
  );
  const malformed = await handler(
    new Request(callbackUrl(`code=${CODE}&state=not-hex`)),
  );
  assert.equal(duplicate.headers.get("location"), `${RESULT}?line_link=failed`);
  assert.equal(malformed.headers.get("location"), `${RESULT}?line_link=failed`);
  assert.equal(called, false);
});

const publicOutcomes: Array<[CompleteLineAccountLinkOutcome, string]> = [
  ["friend_required", "friend_required"],
  ["invalid_session", "expired"],
  ["inactive_member", "inactive"],
  ["member_conflict", "already_linked"],
  ["line_conflict", "line_in_use"],
  ["provider_error", "failed"],
  ["internal_error", "failed"],
];

for (const [privateOutcome, publicOutcome] of publicOutcomes) {
  test(`${privateOutcome} maps to safe public result ${publicOutcome}`, async () => {
    const response = await makeHandler({
      completeLink: async () => privateOutcome,
    })(new Request(callbackUrl()));
    assert.equal(
      response.headers.get("location"),
      `${RESULT}?line_link=${publicOutcome}`,
    );
  });
}

test("callback is GET-only and result URL cannot be an open redirect", async () => {
  assert.equal(
    (await makeHandler()(new Request(ENDPOINT, { method: "POST" }))).status,
    405,
  );
  assert.equal(
    (await makeHandler({ resultUrl: "https://example.com/steal" })(
      new Request(callbackUrl()),
    )).status,
    503,
  );
});

test("logs never contain authorization code or state", async () => {
  const logs: string[] = [];
  await makeHandler({ logs })(new Request(callbackUrl()));
  assert.equal(logs.some((entry) => entry.includes(CODE)), false);
  assert.equal(logs.some((entry) => entry.includes(STATE)), false);
  assert.deepEqual(logs.map((entry) => JSON.parse(entry)), [{ outcome: "linked" }]);
});
