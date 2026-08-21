import type { CompleteLineAccountLinkOutcome } from "./helpers.ts";

export interface LineLoginConfiguration {
  channelId: string;
  channelSecret: string;
  callbackUrl: string;
}

export interface CompleteLineLoginDependencies {
  fetch: typeof fetch;
  persistLink: (input: {
    stateHash: string;
    nonceHash: string;
    lineUserId: string;
    isFriend: boolean;
  }) => Promise<CompleteLineAccountLinkOutcome>;
  now?: () => Date;
}

const LINE_TOKEN_URL = "https://api.line.me/oauth2/v2.1/token";
const LINE_ID_TOKEN_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify";
const LINE_FRIENDSHIP_URL = "https://api.line.me/friendship/v1/status";
const LINE_REVOKE_URL = "https://api.line.me/oauth2/v2.1/revoke";
const MAX_PROVIDER_RESPONSE_BYTES = 32 * 1024;
const USER_AGENT = "tennis-court-watcher-line-link/1.0";

export async function completeLineLogin(
  input: {
    code: string;
    state: string;
    configuration: LineLoginConfiguration;
  },
  dependencies: CompleteLineLoginDependencies,
): Promise<CompleteLineAccountLinkOutcome> {
  const now = dependencies.now ?? (() => new Date());
  let accessToken = "";
  let idToken = "";

  try {
    const tokenResponse = await dependencies.fetch(LINE_TOKEN_URL, {
      method: "POST",
      headers: providerHeaders(),
      body: new URLSearchParams({
        grant_type: "authorization_code",
        code: input.code,
        redirect_uri: input.configuration.callbackUrl,
        client_id: input.configuration.channelId,
        client_secret: input.configuration.channelSecret,
      }),
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
    });
    const tokenPayload = await readJsonResponse(tokenResponse);
    if (
      !tokenResponse.ok ||
      !isRecord(tokenPayload) ||
      typeof tokenPayload.access_token !== "string" ||
      tokenPayload.access_token.length < 20 ||
      typeof tokenPayload.id_token !== "string" ||
      tokenPayload.id_token.length < 20 ||
      tokenPayload.token_type !== "Bearer" ||
      typeof tokenPayload.scope !== "string" ||
      !tokenPayload.scope.split(/\s+/).includes("openid")
    ) {
      return "provider_error";
    }
    accessToken = tokenPayload.access_token;
    idToken = tokenPayload.id_token;

    const verifyResponse = await dependencies.fetch(
      LINE_ID_TOKEN_VERIFY_URL,
      {
        method: "POST",
        headers: providerHeaders(),
        body: new URLSearchParams({
          id_token: idToken,
          client_id: input.configuration.channelId,
        }),
        redirect: "error",
        signal: AbortSignal.timeout(10_000),
      },
    );
    const verified = await readJsonResponse(verifyResponse);
    if (
      !verifyResponse.ok ||
      !validVerifiedIdentity(
        verified,
        input.configuration.channelId,
        now(),
      )
    ) {
      return "provider_error";
    }

    const friendshipResponse = await dependencies.fetch(
      LINE_FRIENDSHIP_URL,
      {
        method: "GET",
        headers: {
          accept: "application/json",
          authorization: `Bearer ${accessToken}`,
          "user-agent": USER_AGENT,
        },
        redirect: "error",
        signal: AbortSignal.timeout(10_000),
      },
    );
    const friendship = await readJsonResponse(friendshipResponse);
    if (
      !friendshipResponse.ok ||
      !isRecord(friendship) ||
      typeof friendship.friendFlag !== "boolean"
    ) {
      return "provider_error";
    }

    return await dependencies.persistLink({
      stateHash: await sha256Bytea(input.state),
      nonceHash: await sha256Bytea(verified.nonce),
      lineUserId: verified.sub,
      isFriend: friendship.friendFlag,
    });
  } catch {
    return "provider_error";
  } finally {
    if (accessToken.length > 0) {
      await revokeAccessToken(
        accessToken,
        input.configuration,
        dependencies.fetch,
      );
    }
    accessToken = "";
    idToken = "";
  }
}

async function revokeAccessToken(
  accessToken: string,
  configuration: LineLoginConfiguration,
  request: typeof fetch,
): Promise<void> {
  try {
    await request(LINE_REVOKE_URL, {
      method: "POST",
      headers: providerHeaders(),
      body: new URLSearchParams({
        access_token: accessToken,
        client_id: configuration.channelId,
        client_secret: configuration.channelSecret,
      }),
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    // The short-lived user token is never persisted. Revocation is best effort
    // because failing after the database commit would make the result unclear.
  }
}

function providerHeaders(): Record<string, string> {
  return {
    accept: "application/json",
    "content-type": "application/x-www-form-urlencoded",
    "user-agent": USER_AGENT,
  };
}

async function readJsonResponse(response: Response): Promise<unknown> {
  const text = await response.text();
  if (new TextEncoder().encode(text).byteLength > MAX_PROVIDER_RESPONSE_BYTES) {
    throw new Error("provider_response_too_large");
  }
  return JSON.parse(text);
}

function validVerifiedIdentity(
  value: unknown,
  channelId: string,
  now: Date,
): value is Record<string, unknown> & {
  nonce: string;
  sub: string;
} {
  if (!isRecord(value)) {
    return false;
  }
  const nowSeconds = Math.floor(now.getTime() / 1000);
  return (
    value.iss === "https://access.line.me" &&
    value.aud === channelId &&
    typeof value.exp === "number" &&
    Number.isFinite(value.exp) &&
    value.exp > nowSeconds &&
    typeof value.iat === "number" &&
    Number.isFinite(value.iat) &&
    value.iat <= nowSeconds + 60 &&
    typeof value.nonce === "string" &&
    /^[0-9a-f]{64}$/.test(value.nonce) &&
    typeof value.sub === "string" &&
    /^U[0-9a-f]{32}$/i.test(value.sub)
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function sha256Bytea(value: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  const hex = Array.from(
    new Uint8Array(digest),
    (byte) => byte.toString(16).padStart(2, "0"),
  ).join("");
  return hex;
}
