export interface UnsubscribeRpcArgs {
  p_token: string;
}

export interface UnsubscribeDependencies {
  getEnv: (name: string) => string | undefined;
  tokenExists: (
    supabaseUrl: string,
    serviceRoleKey: string,
    args: UnsubscribeRpcArgs,
  ) => Promise<{ data: unknown; error: unknown }>;
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

class FormBodyTooLargeError extends Error {}

export function createEmailUnsubscribeHandler(
  dependencies: UnsubscribeDependencies,
): (request: Request) => Promise<Response> {
  const log = dependencies.log ?? console.log;

  return async (request: Request): Promise<Response> => {
    const finish = (
      response: Response,
      outcome: string,
      interaction?: "confirmation" | "human" | "one_click",
    ): Response => {
      log(JSON.stringify({ outcome, ...(interaction ? { interaction } : {}) }));
      return response;
    };

    if (request.method !== "GET" && request.method !== "POST") {
      return finish(methodNotAllowedResponse(), "method_not_allowed");
    }

    const supabaseUrl = dependencies.getEnv("SUPABASE_URL") ?? "";
    const serviceRoleKey = dependencies.getEnv("SUPABASE_SERVICE_ROLE_KEY") ??
      "";
    if (supabaseUrl.length === 0 || serviceRoleKey.length === 0) {
      return finish(errorResponse(), "configuration_error");
    }

    const requestUrl = new URL(request.url);
    if (request.method === "GET") {
      const token = normalizedToken(requestUrl.searchParams.get("token"));
      if (token !== null) {
        let result: { data: unknown; error: unknown };
        try {
          result = await dependencies.tokenExists(
            supabaseUrl,
            serviceRoleKey,
            { p_token: token },
          );
        } catch {
          return finish(errorResponse(), "database_error");
        }
        if (result.error !== null || typeof result.data !== "boolean") {
          return finish(errorResponse(), "database_error");
        }
      }

      return finish(
        confirmationResponse(token ?? ""),
        "confirmation_rendered",
        "confirmation",
      );
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
      const rawBody = await readBoundedUtf8Body(
        request,
        MAX_FORM_BODY_BYTES,
      );
      form = new URLSearchParams(rawBody);
    } catch {
      return finish(badRequestResponse(), "invalid_request");
    }

    const formKeys = Array.from(form.keys());
    const oneClick = formKeys.length === 1 &&
      formKeys[0] === "List-Unsubscribe" &&
      form.getAll("List-Unsubscribe").length === 1 &&
      form.get("List-Unsubscribe") === "One-Click";
    const human = !oneClick && form.get("interaction") === "human";
    if (!oneClick && !human) {
      return finish(badRequestResponse(), "invalid_request");
    }

    const token = normalizedToken(
      oneClick ? requestUrl.searchParams.get("token") : form.get("token"),
    );
    if (token !== null) {
      let result: { data: unknown; error: unknown };
      try {
        result = await dependencies.unsubscribe(
          supabaseUrl,
          serviceRoleKey,
          { p_token: token },
        );
      } catch {
        return finish(errorResponse(), "database_error");
      }
      if (!validUnsubscribeResult(result)) {
        return finish(errorResponse(), "database_error");
      }
    }

    if (oneClick) {
      return finish(minimalSuccessResponse(), "processed", "one_click");
    }
    return finish(humanSuccessResponse(), "processed", "human");
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

function normalizedToken(value: string | null): string | null {
  return value !== null && UUID_PATTERN.test(value)
    ? value.toLowerCase()
    : null;
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

function htmlResponse(body: string, status = 200): Response {
  return new Response(body, {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      "content-security-policy":
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
      "referrer-policy": "no-referrer",
      "x-content-type-options": "nosniff",
    },
  });
}

function page(title: string, content: string): string {
  return `<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><title>${title}</title><style>body{font-family:system-ui,sans-serif;line-height:1.7;margin:0;background:#f5f7f5;color:#17221a}main{max-width:36rem;margin:10vh auto;padding:2rem;background:#fff;border-radius:1rem;box-shadow:0 0.5rem 2rem #17221a18}button{font:inherit;font-weight:700;padding:.75rem 1rem;border:0;border-radius:.5rem;background:#176b3a;color:#fff;cursor:pointer}</style></head><body><main><h1>${title}</h1>${content}</main></body></html>`;
}

function confirmationResponse(token: string): Response {
  const content =
    `<p>この操作を続けると、テニスコートの空き通知メールを停止します。</p><form method="post" action="/functions/v1/unsubscribe-email-notifications"><input type="hidden" name="interaction" value="human"><input type="hidden" name="token" value="${token}"><button type="submit">メール通知を停止する</button></form>`;
  return htmlResponse(page("メール通知の停止", content));
}

function humanSuccessResponse(): Response {
  return htmlResponse(page(
    "メール通知を停止しました",
    "<p>お手続きは完了しました。このページを閉じてください。</p>",
  ));
}

function minimalSuccessResponse(): Response {
  return new Response(null, {
    status: 200,
    headers: {
      "cache-control": "no-store",
      "content-length": "0",
    },
  });
}

function badRequestResponse(): Response {
  return htmlResponse(
    page(
      "リクエストを処理できませんでした",
      "<p>メール内のリンクからもう一度お試しください。</p>",
    ),
    400,
  );
}

function errorResponse(): Response {
  return htmlResponse(
    page(
      "現在お手続きできません",
      "<p>時間をおいて、もう一度お試しください。</p>",
    ),
    502,
  );
}

function methodNotAllowedResponse(): Response {
  const response = htmlResponse(
    page(
      "リクエストを処理できませんでした",
      "<p>メール内のリンクからお手続きください。</p>",
    ),
    405,
  );
  response.headers.set("allow", "GET, POST");
  return response;
}
