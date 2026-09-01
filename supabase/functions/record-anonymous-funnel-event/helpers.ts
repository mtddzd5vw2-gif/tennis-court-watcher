export type AnonymousFunnelEvent =
  | "login_page_view"
  | "line_start_click"
  | "terms_prompt_view";

export interface AnonymousFunnelDependencies {
  getEnv(name: string): string | undefined;
  recordEvent(
    supabaseUrl: string,
    serviceRoleKey: string,
    eventName: AnonymousFunnelEvent,
  ): Promise<{ data: unknown; error: unknown }>;
}

const ALLOWED_ORIGIN = "https://tenniscourtwatcher.com";
const MAX_BODY_BYTES = 128;
const SUPABASE_PROJECT_URL_PATTERN = /^https:\/\/[a-z0-9]{20}\.supabase\.co$/;
const EVENT_NAMES = new Set<AnonymousFunnelEvent>([
  "login_page_view",
  "line_start_click",
  "terms_prompt_view",
]);

class BodyTooLargeError extends Error {}

export function createAnonymousFunnelHandler(
  dependencies: AnonymousFunnelDependencies,
): (request: Request) => Promise<Response> {
  return async (request: Request): Promise<Response> => {
    const origin = request.headers.get("origin") ?? "";
    if (origin !== ALLOWED_ORIGIN) {
      return emptyResponse(403);
    }

    if (request.method === "OPTIONS") {
      return corsResponse(204, origin, {
        allow: "POST, OPTIONS",
        allowHeaders: "content-type",
      });
    }
    if (request.method !== "POST") {
      return corsResponse(405, origin, { allow: "POST, OPTIONS" });
    }

    const requestUrl = new URL(request.url);
    if (requestUrl.search !== "") {
      return corsResponse(400, origin);
    }

    const contentType = request.headers.get("content-type") ?? "";
    if (contentType.split(";", 1)[0].trim().toLowerCase() !== "text/plain") {
      return corsResponse(415, origin);
    }

    const declaredLength = Number(request.headers.get("content-length") ?? "0");
    if (
      !Number.isFinite(declaredLength) ||
      declaredLength < 0 ||
      declaredLength > MAX_BODY_BYTES
    ) {
      return corsResponse(413, origin);
    }

    let body: unknown;
    try {
      body = JSON.parse(await readBoundedUtf8Body(request, MAX_BODY_BYTES));
    } catch (error) {
      return corsResponse(
        error instanceof BodyTooLargeError ? 413 : 400,
        origin,
      );
    }
    const eventName = parseEventName(body);
    if (eventName === null) {
      return corsResponse(400, origin);
    }

    const supabaseUrl = dependencies.getEnv("SUPABASE_URL") ?? "";
    const serviceRoleKey = dependencies.getEnv("SUPABASE_SERVICE_ROLE_KEY") ??
      "";
    if (
      !SUPABASE_PROJECT_URL_PATTERN.test(supabaseUrl) ||
      serviceRoleKey.trim().length === 0
    ) {
      return corsResponse(503, origin);
    }

    let result: { data: unknown; error: unknown };
    try {
      result = await dependencies.recordEvent(
        supabaseUrl,
        serviceRoleKey,
        eventName,
      );
    } catch {
      return corsResponse(502, origin);
    }
    if (result.error !== null || result.data !== true) {
      return corsResponse(502, origin);
    }

    return corsResponse(204, origin);
  };
}

function parseEventName(value: unknown): AnonymousFunnelEvent | null {
  if (!isRecord(value) || Object.keys(value).length !== 1) {
    return null;
  }
  const eventName = value.event_name;
  return typeof eventName === "string" &&
      EVENT_NAMES.has(eventName as AnonymousFunnelEvent)
    ? eventName as AnonymousFunnelEvent
    : null;
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
      throw new BodyTooLargeError();
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

function corsResponse(
  status: number,
  origin: string,
  options: { allow?: string; allowHeaders?: string } = {},
): Response {
  const response = emptyResponse(status);
  response.headers.set("access-control-allow-origin", origin);
  response.headers.set("vary", "Origin");
  if (options.allow) {
    response.headers.set("allow", options.allow);
    response.headers.set("access-control-allow-methods", options.allow);
  }
  if (options.allowHeaders) {
    response.headers.set(
      "access-control-allow-headers",
      options.allowHeaders,
    );
  }
  return response;
}

function emptyResponse(status: number): Response {
  return new Response(null, {
    status,
    headers: {
      "cache-control": "no-store",
      "content-length": "0",
      "x-content-type-options": "nosniff",
    },
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
