import assert from "node:assert/strict";
import { test } from "node:test";
import { completeLineLogin } from "./line-api.ts";
import type { CompleteLineLoginDependencies } from "./line-api.ts";

const CHANNEL_ID = "1234567890";
const CHANNEL_SECRET = "line-login-channel-secret";
const CALLBACK =
  "https://project.supabase.co/functions/v1/complete-line-account-link";
const CODE = "authorization-code";
const STATE = "a".repeat(64);
const NONCE = "b".repeat(64);
const LINE_USER_ID = `U${"c".repeat(32)}`;

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function validResponses(friendFlag = true): Response[] {
  return [
    jsonResponse({
      access_token: "line-user-access-token-value",
      id_token: "header.payload.signature-value",
      token_type: "Bearer",
      scope: "openid profile",
    }),
    jsonResponse({
      iss: "https://access.line.me",
      sub: LINE_USER_ID,
      aud: CHANNEL_ID,
      exp: 1_800_000_000,
      iat: 1_799_999_900,
      nonce: NONCE,
    }),
    jsonResponse({ friendFlag }),
    new Response(null, { status: 200 }),
  ];
}

function makeFetch(responses: Response[], requests: Request[] = []): typeof fetch {
  return (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push(new Request(input, init));
    const response = responses.shift();
    if (!response) {
      throw new Error("unexpected provider request");
    }
    return response;
  }) as typeof fetch;
}

function run(options: {
  responses?: Response[];
  requests?: Request[];
  persistLink?: CompleteLineLoginDependencies["persistLink"];
} = {}) {
  return completeLineLogin(
    {
      code: CODE,
      state: STATE,
      configuration: {
        channelId: CHANNEL_ID,
        channelSecret: CHANNEL_SECRET,
        callbackUrl: CALLBACK,
      },
    },
    {
      fetch: makeFetch(options.responses ?? validResponses(), options.requests),
      persistLink: options.persistLink ?? (async () => "linked"),
      now: () => new Date(1_799_999_950 * 1000),
    },
  );
}

test("exchanges code, verifies ID token and friendship, then persists hashes", async () => {
  const requests: Request[] = [];
  let persisted: Parameters<CompleteLineLoginDependencies["persistLink"]>[0]
    | null = null;
  const outcome = await run({
    requests,
    persistLink: async (input) => {
      persisted = input;
      return "linked";
    },
  });

  assert.equal(outcome, "linked");
  assert.ok(persisted);
  assert.match(persisted.stateHash, /^[0-9a-f]{64}$/);
  assert.match(persisted.nonceHash, /^[0-9a-f]{64}$/);
  assert.equal(persisted.lineUserId, LINE_USER_ID);
  assert.equal(persisted.isFriend, true);
  assert.equal(requests.length, 4);
  assert.equal(requests[0].url, "https://api.line.me/oauth2/v2.1/token");
  assert.equal(requests[1].url, "https://api.line.me/oauth2/v2.1/verify");
  assert.equal(requests[2].url, "https://api.line.me/friendship/v1/status");
  assert.equal(requests[3].url, "https://api.line.me/oauth2/v2.1/revoke");

  const tokenBody = new URLSearchParams(await requests[0].text());
  assert.equal(tokenBody.get("grant_type"), "authorization_code");
  assert.equal(tokenBody.get("code"), CODE);
  assert.equal(tokenBody.get("redirect_uri"), CALLBACK);
  assert.equal(tokenBody.get("client_secret"), CHANNEL_SECRET);

  const verifyBody = new URLSearchParams(await requests[1].text());
  assert.equal(verifyBody.get("client_id"), CHANNEL_ID);
  assert.equal(verifyBody.has("nonce"), false);
  assert.equal(requests[2].headers.get("authorization"), "Bearer line-user-access-token-value");
});

test("friendship false is persisted for a safe friend-required status", async () => {
  let friend: boolean | null = null;
  const outcome = await run({
    responses: validResponses(false),
    persistLink: async (input) => {
      friend = input.isFriend;
      return "friend_required";
    },
  });
  assert.equal(outcome, "friend_required");
  assert.equal(friend, false);
});

test("invalid issuer, audience, nonce, expiry, or LINE identifier is rejected", async () => {
  const invalidClaims = [
    { iss: "https://example.invalid" },
    { aud: "other-channel" },
    { nonce: "short" },
    { exp: 1_799_999_000 },
    { sub: "not-a-line-user" },
  ];
  for (const patch of invalidClaims) {
    let persisted = false;
    const responses = validResponses();
    responses[1] = jsonResponse({
      iss: "https://access.line.me",
      sub: LINE_USER_ID,
      aud: CHANNEL_ID,
      exp: 1_800_000_000,
      iat: 1_799_999_900,
      nonce: NONCE,
      ...patch,
    });
    const outcome = await run({
      responses,
      persistLink: async () => {
        persisted = true;
        return "linked";
      },
    });
    assert.equal(outcome, "provider_error");
    assert.equal(persisted, false);
  }
});

test("provider and malformed responses fail without persisting identity", async () => {
  let persisted = false;
  const outcome = await run({
    responses: [jsonResponse({ message: "invalid_grant" }, 400)],
    persistLink: async () => {
      persisted = true;
      return "linked";
    },
  });
  assert.equal(outcome, "provider_error");
  assert.equal(persisted, false);
});

test("access token is revoked even when ID-token verification fails", async () => {
  const requests: Request[] = [];
  const responses = validResponses();
  responses[1] = jsonResponse({ message: "invalid id token" }, 400);
  responses.splice(2, 1);
  const outcome = await run({ responses, requests });
  assert.equal(outcome, "provider_error");
  assert.equal(requests.at(-1)?.url, "https://api.line.me/oauth2/v2.1/revoke");
});
