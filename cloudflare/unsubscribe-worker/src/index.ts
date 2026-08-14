export interface Env {
  SUPABASE_UNSUBSCRIBE_URL: string;
  UNSUBSCRIBE_WORKER_SECRET: string;
}

export type UpstreamFetch = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const UNSUBSCRIBE_PATH_PATTERN = /^\/u\/([^/]+)$/;
const PRODUCTION_SUPABASE_UNSUBSCRIBE_URL =
  "https://oocqyeariwuppkeaeioh.supabase.co/functions/v1/unsubscribe-email-notifications";
const SUPABASE_FUNCTION_PATH = "/functions/v1/unsubscribe-email-notifications";
const MINIMUM_SECRET_BYTES = 32;
export const MAX_FORM_BODY_BYTES = 2048;

type Interaction = "human" | "one_click";

class FormBodyTooLargeError extends Error {}

export function createUnsubscribeWorkerHandler(
  upstreamFetch: UpstreamFetch = fetch,
): (request: Request, env: Env) => Promise<Response> {
  return async (request: Request, env: Env): Promise<Response> => {
    const requestUrl = new URL(request.url);
    const pathMatch = UNSUBSCRIBE_PATH_PATTERN.exec(requestUrl.pathname);

    if (request.method === "GET") {
      return pathMatch === null ? notFoundResponse() : confirmationResponse();
    }

    if (request.method !== "POST") {
      return pathMatch === null
        ? notFoundResponse()
        : methodNotAllowedResponse();
    }

    if (pathMatch === null) {
      return notFoundResponse();
    }

    const contentType = request.headers.get("content-type") ?? "";
    if (
      contentType.split(";", 1)[0].trim().toLowerCase() !==
        "application/x-www-form-urlencoded"
    ) {
      return badRequestResponse();
    }

    const declaredLength = Number(request.headers.get("content-length") ?? "0");
    if (
      !Number.isFinite(declaredLength) ||
      declaredLength < 0 ||
      declaredLength > MAX_FORM_BODY_BYTES
    ) {
      return badRequestResponse();
    }

    let form: URLSearchParams;
    try {
      form = new URLSearchParams(
        await readBoundedUtf8Body(request, MAX_FORM_BODY_BYTES),
      );
    } catch {
      return badRequestResponse();
    }

    const interaction = parseInteraction(form);
    if (interaction === null) {
      return badRequestResponse();
    }

    const upstreamUrl = normalizedUpstreamUrl(env.SUPABASE_UNSUBSCRIBE_URL);
    const workerSecret = normalizedWorkerSecret(env.UNSUBSCRIBE_WORKER_SECRET);
    if (upstreamUrl === null || workerSecret === null) {
      return upstreamFailureResponse(interaction, 503);
    }

    const token = normalizedToken(pathMatch[1]);
    if (token === null) {
      return successResponse(interaction);
    }

    let upstreamResponse: Response;
    try {
      upstreamResponse = await upstreamFetch(upstreamUrl, {
        method: "POST",
        headers: {
          authorization: `Bearer ${workerSecret}`,
          "content-type": "application/x-www-form-urlencoded",
        },
        body: new URLSearchParams({ interaction, token }).toString(),
        redirect: "manual",
      });
    } catch {
      return upstreamFailureResponse(interaction, 502);
    }

    if (upstreamResponse.status !== 200) {
      const status = upstreamResponse.status >= 500 &&
          upstreamResponse.status <= 599
        ? upstreamResponse.status
        : 502;
      return upstreamFailureResponse(interaction, status);
    }

    return successResponse(interaction);
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

function parseInteraction(form: URLSearchParams): Interaction | null {
  const keys = Array.from(form.keys());
  const oneClick = keys.length === 1 &&
    keys[0] === "List-Unsubscribe" &&
    form.getAll("List-Unsubscribe").length === 1 &&
    form.get("List-Unsubscribe") === "One-Click";
  if (oneClick) {
    return "one_click";
  }

  const human = keys.length === 1 &&
    keys[0] === "interaction" &&
    form.getAll("interaction").length === 1 &&
    form.get("interaction") === "human";
  return human ? "human" : null;
}

function normalizedToken(value: string): string | null {
  return UUID_PATTERN.test(value) ? value.toLowerCase() : null;
}

function normalizedWorkerSecret(value: string | undefined): string | null {
  return typeof value === "string" &&
      !/\s/u.test(value) &&
      new TextEncoder().encode(value).byteLength >= MINIMUM_SECRET_BYTES
    ? value
    : null;
}

function normalizedUpstreamUrl(value: string | undefined): string | null {
  if (typeof value !== "string" || value.length === 0) {
    return null;
  }

  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }

  const productionEndpoint = value === PRODUCTION_SUPABASE_UNSUBSCRIBE_URL;
  const localEndpoint =
    (url.protocol === "http:" || url.protocol === "https:") &&
    (url.hostname === "localhost" || url.hostname === "127.0.0.1") &&
    url.username.length === 0 &&
    url.password.length === 0 &&
    url.search.length === 0 &&
    url.hash.length === 0 &&
    url.pathname === SUPABASE_FUNCTION_PATH;
  if (!productionEndpoint && !localEndpoint) {
    return null;
  }

  return url.toString();
}

function securityHeaders(contentType: string): HeadersInit {
  return {
    "content-type": contentType,
    "cache-control": "no-store",
    "content-security-policy":
      "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    "referrer-policy": "no-referrer",
    "permissions-policy": "browsing-topics=()",
    "x-content-type-options": "nosniff",
  };
}

function htmlResponse(body: string, status = 200): Response {
  return new Response(body, {
    status,
    headers: securityHeaders("text/html; charset=utf-8"),
  });
}

function page(title: string, content: string): string {
  return `<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>${title}</title><style>body{font-family:system-ui,sans-serif;line-height:1.7;margin:0;background:#f5f7f5;color:#17221a}main{max-width:36rem;margin:10vh auto;padding:2rem;background:#fff;border-radius:1rem;box-shadow:0 .5rem 2rem #17221a18}button{font:inherit;font-weight:700;padding:.75rem 1rem;border:0;border-radius:.5rem;background:#176b3a;color:#fff;cursor:pointer}</style></head><body><main><h1>${title}</h1>${content}</main></body></html>`;
}

function confirmationResponse(): Response {
  return htmlResponse(page(
    "メール通知の停止",
    '<p>手続きを続けると、テニスコートの空き通知メールを停止します。</p><form method="post"><input type="hidden" name="interaction" value="human"><button type="submit">メール通知を停止する</button></form>',
  ));
}

function humanSuccessResponse(): Response {
  return htmlResponse(page(
    "メール通知を停止しました",
    "<p>手続きは完了しました。このページを閉じてください。</p>",
  ));
}

function oneClickSuccessResponse(): Response {
  return new Response(null, {
    status: 200,
    headers: {
      "cache-control": "no-store",
      "content-length": "0",
      "referrer-policy": "no-referrer",
    },
  });
}

function successResponse(interaction: Interaction): Response {
  return interaction === "one_click"
    ? oneClickSuccessResponse()
    : humanSuccessResponse();
}

function upstreamFailureResponse(
  interaction: Interaction,
  status: number,
): Response {
  if (interaction === "one_click") {
    return new Response(null, {
      status,
      headers: {
        "cache-control": "no-store",
        "content-length": "0",
        "referrer-policy": "no-referrer",
      },
    });
  }
  return htmlResponse(
    page(
      "現在お手続きできません",
      "<p>時間をおいて、もう一度お試しください。</p>",
    ),
    status,
  );
}

function badRequestResponse(): Response {
  return htmlResponse(
    page(
      "リクエストを処理できませんでした",
      "<p>メール内のリンクから、もう一度お試しください。</p>",
    ),
    400,
  );
}

function notFoundResponse(): Response {
  return htmlResponse(
    page(
      "ページが見つかりません",
      "<p>メール内のリンクをご確認ください。</p>",
    ),
    404,
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

const handler = createUnsubscribeWorkerHandler();

export default {
  fetch(request: Request, env: Env): Promise<Response> {
    return handler(request, env);
  },
};
