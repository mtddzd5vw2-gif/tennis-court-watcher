export type DeleteAccountOutcome =
  | "deleted"
  | "unauthorized"
  | "profile_lock_failed"
  | "delete_failed"
  | "internal_error";

export interface DeleteAccountDependencies {
  getEnv: (name: string) => string | undefined;
  deleteAccount: (
    supabaseUrl: string,
    serviceRoleKey: string,
    accessToken: string,
  ) => Promise<DeleteAccountOutcome>;
  log?: (value: string) => void;
}

const CONFIRMATION_VALUE = "delete-my-account";
export const MAX_JSON_BODY_BYTES = 256;

const ALLOWED_BROWSER_ORIGINS = new Set([
  "https://tenniscourtwatcher.com",
  "http://localhost:8765",
  "http://127.0.0.1:8765",
]);

const ALLOWED_HEADERS = "authorization, x-client-info, apikey, content-type";

export function createDeleteAccountHandler(
  dependencies: DeleteAccountDependencies,
): (request: Request) => Promise<Response> {
  const log = dependencies.log ?? console.log;

  return async (request: Request): Promise<Response> => {
    const origin = request.headers.get("origin");

    const finish = (
      response: Response,
      outcome: string,
    ): Response => {
      log(JSON.stringify({ outcome }));
      return response;
    };

    if (
      origin !== null &&
      !ALLOWED_BROWSER_ORIGINS.has(origin)
    ) {
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

    if (
      supabaseUrl.length === 0 ||
      serviceRoleKey.length === 0
    ) {
      return finish(
        responseFor(request, 503),
        "configuration_error",
      );
    }

    const contentType = request.headers.get("content-type") ?? "";
    if (
      contentType.split(";", 1)[0].trim().toLowerCase() !==
        "application/json"
    ) {
      return finish(
        responseFor(request, 400),
        "invalid_request",
      );
    }

    const declaredLengthHeader = request.headers.get("content-length");
    if (declaredLengthHeader !== null) {
      const declaredLength = Number(declaredLengthHeader);
      if (
        !Number.isFinite(declaredLength) ||
        declaredLength < 0 ||
        declaredLength > MAX_JSON_BODY_BYTES
      ) {
        return finish(
          responseFor(request, 400),
          "invalid_request",
        );
      }
    }

    let payload: unknown;
    try {
      const body = await readBoundedUtf8Body(
        request,
        MAX_JSON_BODY_BYTES,
      );
      payload = JSON.parse(body);
    } catch {
      return finish(
        responseFor(request, 400),
        "invalid_request",
      );
    }

    if (!validConfirmationPayload(payload)) {
      return finish(
        responseFor(request, 400),
        "invalid_request",
      );
    }

    let outcome: DeleteAccountOutcome;
    try {
      outcome = await dependencies.deleteAccount(
        supabaseUrl,
        serviceRoleKey,
        accessToken,
      );
    } catch {
      outcome = "internal_error";
    }

    if (outcome === "deleted") {
      return finish(
        responseFor(request, 204),
        "deleted",
      );
    }

    if (outcome === "unauthorized") {
      return finish(
        responseFor(request, 401),
        "unauthorized",
      );
    }

    return finish(
      responseFor(request, 502),
      outcome,
    );
  };
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

function readBearerToken(
  value: string | null,
): string | null {
  if (value === null) {
    return null;
  }

  const match = /^Bearer ([^\s]+)$/i.exec(value);
  if (!match) {
    return null;
  }

  return match[1];
}

function validConfirmationPayload(
  value: unknown,
): boolean {
  if (
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value)
  ) {
    return false;
  }

  const record = value as Record<string, unknown>;
  const keys = Object.keys(record);

  return (
    keys.length === 1 &&
    keys[0] === "confirmation" &&
    record.confirmation === CONFIRMATION_VALUE
  );
}

function responseFor(
  request: Request,
  status: number,
): Response {
  const headers = new Headers({
    "cache-control": "no-store",
    "content-length": "0",
    "x-content-type-options": "nosniff",
  });

  addCorsHeaders(request, headers);

  return new Response(null, {
    status,
    headers,
  });
}

function preflightResponse(
  request: Request,
): Response {
  const response = responseFor(request, 204);
  response.headers.set(
    "access-control-allow-methods",
    "POST, OPTIONS",
  );
  response.headers.set(
    "access-control-allow-headers",
    ALLOWED_HEADERS,
  );
  response.headers.set(
    "access-control-max-age",
    "600",
  );
  return response;
}

function addCorsHeaders(
  request: Request,
  headers: Headers,
): void {
  const origin = request.headers.get("origin");
  if (
    origin !== null &&
    ALLOWED_BROWSER_ORIGINS.has(origin)
  ) {
    headers.set(
      "access-control-allow-origin",
      origin,
    );
    headers.set("vary", "Origin");
  }
}
