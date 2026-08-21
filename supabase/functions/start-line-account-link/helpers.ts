export type StartLineAccountLinkOutcome =
  | "created"
  | "inactive_member"
  | "retry"
  | "unauthorized"
  | "internal_error";

export interface StartLineAccountLinkDependencies {
  getEnv: (name: string) => string | undefined;
  createSession: (input: {
    supabaseUrl: string;
    serviceRoleKey: string;
    accessToken: string;
    stateHash: string;
    nonceHash: string;
    expiresAt: string;
  }) => Promise<StartLineAccountLinkOutcome>;
  randomBytes?: (length: number) => Uint8Array;
  now?: () => Date;
  log?: (value: string) => void;
}

export const MAX_START_BODY_BYTES = 16;
const SESSION_LIFETIME_MS = 9 * 60 * 1000;
const ALLOWED_HEADERS = "authorization, x-client-info, apikey, content-type";

const ALLOWED_BROWSER_ORIGINS = new Set([
  "https://tenniscourtwatcher.com",
  "http://localhost:8765",
  "http://127.0.0.1:8765",
]);

export function createStartLineAccountLinkHandler(
  dependencies: StartLineAccountLinkDependencies,
): (request: Request) => Promise<Response> {
  const log = dependencies.log ?? console.log;
  const randomBytes = dependencies.randomBytes ?? secureRandomBytes;
  const now = dependencies.now ?? (() => new Date());

  return async (request: Request): Promise<Response> => {
    const finish = (response: Response, outcome: string): Response => {
      log(JSON.stringify({ outcome }));
      return response;
    };

    if (!allowedOrigin(request.headers.get("origin"))) {
      return finish(emptyResponse(request, 403), "origin_forbidden");
    }

    if (request.method === "OPTIONS") {
      return finish(preflightResponse(request), "preflight");
    }

    if (request.method !== "POST") {
      const response = emptyResponse(request, 405);
      response.headers.set("allow", "POST, OPTIONS");
      return finish(response, "method_not_allowed");
    }

    const accessToken = readBearerToken(
      request.headers.get("authorization"),
    );
    if (accessToken === null) {
      return finish(emptyResponse(request, 401), "unauthorized");
    }

    if (!await hasEmptyJsonBody(request)) {
      return finish(emptyResponse(request, 400), "invalid_request");
    }

    const supabaseUrl = dependencies.getEnv("SUPABASE_URL") ?? "";
    const serviceRoleKey = dependencies.getEnv("SUPABASE_SERVICE_ROLE_KEY") ??
      "";
    const channelId = dependencies.getEnv("LINE_LOGIN_CHANNEL_ID") ?? "";
    const callbackUrl = dependencies.getEnv("LINE_LOGIN_CALLBACK_URL") ?? "";

    if (
      supabaseUrl.length === 0 ||
      serviceRoleKey.length === 0 ||
      !validChannelId(channelId) ||
      !validCallbackUrl(callbackUrl)
    ) {
      return finish(emptyResponse(request, 503), "configuration_error");
    }

    const state = bytesToHex(randomBytes(32));
    const nonce = bytesToHex(randomBytes(32));
    if (state.length !== 64 || nonce.length !== 64 || state === nonce) {
      return finish(emptyResponse(request, 503), "randomness_error");
    }

    const stateHash = await sha256Bytea(state);
    const nonceHash = await sha256Bytea(nonce);
    const expiresAt = new Date(now().getTime() + SESSION_LIFETIME_MS)
      .toISOString();

    let outcome: StartLineAccountLinkOutcome;
    try {
      outcome = await dependencies.createSession({
        supabaseUrl,
        serviceRoleKey,
        accessToken,
        stateHash,
        nonceHash,
        expiresAt,
      });
    } catch {
      outcome = "internal_error";
    }

    if (outcome === "unauthorized") {
      return finish(emptyResponse(request, 401), outcome);
    }
    if (outcome === "inactive_member") {
      return finish(emptyResponse(request, 403), outcome);
    }
    if (outcome !== "created") {
      return finish(emptyResponse(request, 502), outcome);
    }

    const authorizationUrl = buildAuthorizationUrl({
      channelId,
      callbackUrl,
      state,
      nonce,
    });

    return finish(
      jsonResponse(request, 200, {
        authorization_url: authorizationUrl,
      }),
      "created",
    );
  };
}

export function buildAuthorizationUrl(input: {
  channelId: string;
  callbackUrl: string;
  state: string;
  nonce: string;
}): string {
  const url = new URL("https://access.line.me/oauth2/v2.1/authorize");
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", input.channelId);
  url.searchParams.set("redirect_uri", input.callbackUrl);
  url.searchParams.set("state", input.state);
  url.searchParams.set("scope", "openid profile");
  url.searchParams.set("nonce", input.nonce);
  url.searchParams.set("bot_prompt", "aggressive");
  return url.toString();
}

async function hasEmptyJsonBody(request: Request): Promise<boolean> {
  const declaredLength = request.headers.get("content-length");
  if (declaredLength !== null) {
    const parsedLength = Number(declaredLength);
    if (
      !Number.isFinite(parsedLength) ||
      parsedLength < 0 ||
      parsedLength > MAX_START_BODY_BYTES
    ) {
      return false;
    }
  }

  let body: string;
  try {
    body = await readBoundedUtf8Body(request, MAX_START_BODY_BYTES);
  } catch {
    return false;
  }

  if (body.trim() === "") {
    return true;
  }

  const contentType = request.headers.get("content-type") ?? "";
  if (
    contentType.split(";", 1)[0].trim().toLowerCase() !==
      "application/json"
  ) {
    return false;
  }

  try {
    const payload = JSON.parse(body);
    return (
      typeof payload === "object" &&
      payload !== null &&
      !Array.isArray(payload) &&
      Object.keys(payload as Record<string, unknown>).length === 0
    );
  } catch {
    return false;
  }
}

async function readBoundedUtf8Body(
  request: Request,
  maximumBytes: number,
): Promise<string> {
  if (request.body === null) {
    return "";
  }

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let byteCount = 0;

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    byteCount += value.byteLength;
    if (byteCount > maximumBytes) {
      await reader.cancel();
      throw new Error("request_body_too_large");
    }
    chunks.push(value);
  }

  const body = new Uint8Array(byteCount);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(body);
}

async function sha256Bytea(value: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(value),
  );
  return bytesToHex(new Uint8Array(digest));
}

function secureRandomBytes(length: number): Uint8Array {
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  return bytes;
}

function bytesToHex(value: Uint8Array): string {
  return Array.from(value, (byte) => byte.toString(16).padStart(2, "0")).join(
    "",
  );
}

function validChannelId(value: string): boolean {
  return /^[0-9]{5,20}$/.test(value);
}

function validCallbackUrl(value: string): boolean {
  try {
    const url = new URL(value);
    const production =
      url.protocol === "https:" &&
      url.hostname.endsWith(".supabase.co");
    const local =
      url.protocol === "http:" &&
      (url.hostname === "127.0.0.1" || url.hostname === "localhost");
    return (
      (production || local) &&
      url.pathname.endsWith("/functions/v1/complete-line-account-link") &&
      url.username === "" &&
      url.password === "" &&
      url.search === "" &&
      url.hash === ""
    );
  } catch {
    return false;
  }
}

function readBearerToken(value: string | null): string | null {
  const match = value === null ? null : /^Bearer ([^\s]+)$/i.exec(value);
  return match?.[1] ?? null;
}

function allowedOrigin(value: string | null): boolean {
  return value === null || ALLOWED_BROWSER_ORIGINS.has(value);
}

function baseHeaders(request: Request): Headers {
  const headers = new Headers({
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
  });
  const origin = request.headers.get("origin");
  if (origin !== null && ALLOWED_BROWSER_ORIGINS.has(origin)) {
    headers.set("access-control-allow-origin", origin);
    headers.set("vary", "Origin");
  }
  return headers;
}

function emptyResponse(request: Request, status: number): Response {
  const headers = baseHeaders(request);
  headers.set("content-length", "0");
  return new Response(null, { status, headers });
}

function jsonResponse(
  request: Request,
  status: number,
  payload: Record<string, string>,
): Response {
  const body = JSON.stringify(payload);
  const headers = baseHeaders(request);
  headers.set("content-type", "application/json; charset=utf-8");
  return new Response(body, { status, headers });
}

function preflightResponse(request: Request): Response {
  const response = emptyResponse(request, 204);
  response.headers.set("access-control-allow-methods", "POST, OPTIONS");
  response.headers.set("access-control-allow-headers", ALLOWED_HEADERS);
  response.headers.set("access-control-max-age", "600");
  return response;
}
