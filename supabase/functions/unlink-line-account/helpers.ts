export type UnlinkLineAccountOutcome =
  | "unlinked"
  | "not_linked"
  | "inactive_member"
  | "unauthorized"
  | "internal_error";

export interface UnlinkLineAccountDependencies {
  getEnv: (name: string) => string | undefined;
  unlinkAccount: (input: {
    supabaseUrl: string;
    serviceRoleKey: string;
    accessToken: string;
  }) => Promise<UnlinkLineAccountOutcome>;
  log?: (value: string) => void;
}

export const MAX_UNLINK_BODY_BYTES = 64;
const CONFIRMATION_VALUE = "unlink-line-account";
const ALLOWED_HEADERS = "authorization, x-client-info, apikey, content-type";

const ALLOWED_BROWSER_ORIGINS = new Set([
  "https://tenniscourtwatcher.com",
  "http://localhost:8765",
  "http://127.0.0.1:8765",
]);

export function createUnlinkLineAccountHandler(
  dependencies: UnlinkLineAccountDependencies,
): (request: Request) => Promise<Response> {
  const log = dependencies.log ?? console.log;

  return async (request: Request): Promise<Response> => {
    const finish = (response: Response, outcome: string): Response => {
      log(JSON.stringify({ outcome }));
      return response;
    };

    if (!allowedOrigin(request.headers.get("origin"))) {
      return finish(responseFor(request, 403), "origin_forbidden");
    }

    if (request.method === "OPTIONS") {
      return finish(preflightResponse(request), "preflight");
    }

    if (request.method !== "POST") {
      const response = responseFor(request, 405);
      response.headers.set("allow", "POST, OPTIONS");
      return finish(response, "method_not_allowed");
    }

    const accessToken = readBearerToken(
      request.headers.get("authorization"),
    );
    if (accessToken === null) {
      return finish(responseFor(request, 401), "unauthorized");
    }

    const supabaseUrl = dependencies.getEnv("SUPABASE_URL") ?? "";
    const serviceRoleKey = dependencies.getEnv("SUPABASE_SERVICE_ROLE_KEY") ??
      "";
    if (supabaseUrl.length === 0 || serviceRoleKey.length === 0) {
      return finish(responseFor(request, 503), "configuration_error");
    }

    if (!await validConfirmationBody(request)) {
      return finish(responseFor(request, 400), "invalid_request");
    }

    let outcome: UnlinkLineAccountOutcome;
    try {
      outcome = await dependencies.unlinkAccount({
        supabaseUrl,
        serviceRoleKey,
        accessToken,
      });
    } catch {
      outcome = "internal_error";
    }

    if (outcome === "unlinked" || outcome === "not_linked") {
      return finish(responseFor(request, 204), outcome);
    }
    if (outcome === "unauthorized") {
      return finish(responseFor(request, 401), outcome);
    }
    if (outcome === "inactive_member") {
      return finish(responseFor(request, 403), outcome);
    }
    return finish(responseFor(request, 502), outcome);
  };
}

async function validConfirmationBody(request: Request): Promise<boolean> {
  const contentType = request.headers.get("content-type") ?? "";
  if (
    contentType.split(";", 1)[0].trim().toLowerCase() !==
      "application/json"
  ) {
    return false;
  }

  const declaredLength = request.headers.get("content-length");
  if (declaredLength !== null) {
    const parsedLength = Number(declaredLength);
    if (
      !Number.isFinite(parsedLength) ||
      parsedLength < 0 ||
      parsedLength > MAX_UNLINK_BODY_BYTES
    ) {
      return false;
    }
  }

  let payload: unknown;
  try {
    const body = await readBoundedUtf8Body(request, MAX_UNLINK_BODY_BYTES);
    payload = JSON.parse(body);
  } catch {
    return false;
  }

  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    return false;
  }
  const record = payload as Record<string, unknown>;
  return (
    Object.keys(record).length === 1 &&
    record.confirmation === CONFIRMATION_VALUE
  );
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

function readBearerToken(value: string | null): string | null {
  const match = value === null ? null : /^Bearer ([^\s]+)$/i.exec(value);
  return match?.[1] ?? null;
}

function allowedOrigin(value: string | null): boolean {
  return value === null || ALLOWED_BROWSER_ORIGINS.has(value);
}

function responseFor(request: Request, status: number): Response {
  const headers = new Headers({
    "cache-control": "no-store",
    "content-length": "0",
    "x-content-type-options": "nosniff",
  });
  const origin = request.headers.get("origin");
  if (origin !== null && ALLOWED_BROWSER_ORIGINS.has(origin)) {
    headers.set("access-control-allow-origin", origin);
    headers.set("vary", "Origin");
  }
  return new Response(null, { status, headers });
}

function preflightResponse(request: Request): Response {
  const response = responseFor(request, 204);
  response.headers.set("access-control-allow-methods", "POST, OPTIONS");
  response.headers.set("access-control-allow-headers", ALLOWED_HEADERS);
  response.headers.set("access-control-max-age", "600");
  return response;
}
