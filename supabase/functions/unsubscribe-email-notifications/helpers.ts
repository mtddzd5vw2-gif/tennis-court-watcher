export interface UnsubscribeRpcArgs {
  p_token: string;
}

export interface UnsubscribeDependencies {
  getEnv: (name: string) => string | undefined;
  unsubscribe: (
    supabaseUrl: string,
    serviceRoleKey: string,
    args: UnsubscribeRpcArgs,
  ) => Promise<{ data: unknown; error: unknown }>;
  log?: (value: string) => void;
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export const MAX_FORM_BODY_BYTES = 2048;
const MINIMUM_SECRET_BYTES = 32;

type Interaction = "human" | "one_click";

class FormBodyTooLargeError extends Error {}

export function createEmailUnsubscribeHandler(
  dependencies: UnsubscribeDependencies,
): (request: Request) => Promise<Response> {
  const log = dependencies.log ?? console.log;

  return async (request: Request): Promise<Response> => {
    const finish = (
      response: Response,
      outcome: string,
      interaction?: Interaction,
    ): Response => {
      log(JSON.stringify({ outcome, ...(interaction ? { interaction } : {}) }));
      return response;
    };

    if (request.method !== "POST") {
      return finish(methodNotAllowedResponse(), "method_not_allowed");
    }

    const workerSecret = dependencies.getEnv("UNSUBSCRIBE_WORKER_SECRET") ?? "";
    if (!validWorkerSecret(workerSecret)) {
      return finish(configurationErrorResponse(), "configuration_error");
    }
    const suppliedSecret = request.headers.get(
      "x-unsubscribe-worker-secret",
    );
    if (
      suppliedSecret === null ||
      !(await secretsEqual(suppliedSecret, workerSecret))
    ) {
      return finish(unauthorizedResponse(), "unauthorized");
    }

    const contentType = request.headers.get("content-type") ?? "";
    if (
      contentType.split(";", 1)[0].trim().toLowerCase() !==
        "application/x-www-form-urlencoded"
    ) {
      return finish(badRequestResponse(), "invalid_request");
    }

    const declaredLength = Number(request.headers.get("content-length") ?? "0");
    if (
      !Number.isFinite(declaredLength) ||
      declaredLength < 0 ||
      declaredLength > MAX_FORM_BODY_BYTES
    ) {
      return finish(badRequestResponse(), "invalid_request");
    }

    let form: URLSearchParams;
    try {
      form = new URLSearchParams(
        await readBoundedUtf8Body(request, MAX_FORM_BODY_BYTES),
      );
    } catch {
      return finish(badRequestResponse(), "invalid_request");
    }

    const parsed = parseInternalForm(form);
    if (parsed === null) {
      return finish(badRequestResponse(), "invalid_request");
    }

    const token = normalizedToken(parsed.token);
    if (token === null) {
      return finish(minimalSuccessResponse(), "processed", parsed.interaction);
    }

    const supabaseUrl = dependencies.getEnv("SUPABASE_URL") ?? "";
    const serviceRoleKey = dependencies.getEnv("SUPABASE_SERVICE_ROLE_KEY") ??
      "";
    if (supabaseUrl.length === 0 || serviceRoleKey.length === 0) {
      return finish(errorResponse(), "configuration_error", parsed.interaction);
    }

    let result: { data: unknown; error: unknown };
    try {
      result = await dependencies.unsubscribe(
        supabaseUrl,
        serviceRoleKey,
        { p_token: token },
      );
    } catch {
      return finish(errorResponse(), "database_error", parsed.interaction);
    }
    if (!validUnsubscribeResult(result)) {
      return finish(errorResponse(), "database_error", parsed.interaction);
    }

    return finish(minimalSuccessResponse(), "processed", parsed.interaction);
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
      throw new FormBodyTooLargeError();
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

function parseInternalForm(
  form: URLSearchParams,
): { interaction: Interaction; token: string } | null {
  const keys = Array.from(form.keys());
  if (
    keys.length !== 2 ||
    new Set(keys).size !== 2 ||
    !keys.includes("interaction") ||
    !keys.includes("token") ||
    form.getAll("interaction").length !== 1 ||
    form.getAll("token").length !== 1
  ) {
    return null;
  }

  const interaction = form.get("interaction");
  const token = form.get("token");
  if (
    (interaction !== "human" && interaction !== "one_click") ||
    token === null
  ) {
    return null;
  }
  return { interaction, token };
}

function normalizedToken(value: string): string | null {
  return UUID_PATTERN.test(value) ? value.toLowerCase() : null;
}

function validWorkerSecret(value: string): boolean {
  return !/\s/u.test(value) &&
    new TextEncoder().encode(value).byteLength >= MINIMUM_SECRET_BYTES;
}

async function secretsEqual(left: string, right: string): Promise<boolean> {
  const encoder = new TextEncoder();
  const [leftDigest, rightDigest] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(left)),
    crypto.subtle.digest("SHA-256", encoder.encode(right)),
  ]);
  const leftBytes = new Uint8Array(leftDigest);
  const rightBytes = new Uint8Array(rightDigest);
  let difference = leftBytes.length ^ rightBytes.length;
  for (let index = 0; index < leftBytes.length; index += 1) {
    difference |= leftBytes[index] ^ rightBytes[index];
  }
  return difference === 0;
}

function validUnsubscribeResult(
  result: { data: unknown; error: unknown },
): boolean {
  if (
    result.error !== null || typeof result.data !== "object" ||
    result.data === null
  ) {
    return false;
  }
  return (result.data as Record<string, unknown>).outcome === "processed";
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

function minimalSuccessResponse(): Response {
  return emptyResponse(200);
}

function badRequestResponse(): Response {
  return emptyResponse(400);
}

function errorResponse(): Response {
  return emptyResponse(502);
}

function configurationErrorResponse(): Response {
  return emptyResponse(503);
}

function unauthorizedResponse(): Response {
  return emptyResponse(401);
}

function methodNotAllowedResponse(): Response {
  const response = emptyResponse(405);
  response.headers.set("allow", "POST");
  return response;
}
