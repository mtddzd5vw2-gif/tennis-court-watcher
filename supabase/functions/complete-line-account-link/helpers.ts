export type CompleteLineAccountLinkOutcome =
  | "linked"
  | "friend_required"
  | "invalid_session"
  | "inactive_member"
  | "member_conflict"
  | "line_conflict"
  | "invalid_request"
  | "provider_error"
  | "internal_error";

export interface CompleteLineAccountLinkDependencies {
  getEnv: (name: string) => string | undefined;
  completeLink: (input: {
    code: string;
    state: string;
  }) => Promise<CompleteLineAccountLinkOutcome>;
  log?: (value: string) => void;
}

const MAX_CALLBACK_URL_LENGTH = 4096;

export function createCompleteLineAccountLinkHandler(
  dependencies: CompleteLineAccountLinkDependencies,
): (request: Request) => Promise<Response> {
  const log = dependencies.log ?? console.log;

  return async (request: Request): Promise<Response> => {
    const finish = (response: Response, outcome: string): Response => {
      log(JSON.stringify({ outcome }));
      return response;
    };

    if (request.method !== "GET") {
      const response = emptyResponse(405);
      response.headers.set("allow", "GET");
      return finish(response, "method_not_allowed");
    }

    const resultUrl = dependencies.getEnv("LINE_LINK_RESULT_URL") ?? "";
    if (!validResultUrl(resultUrl)) {
      return finish(emptyResponse(503), "configuration_error");
    }

    if (request.url.length > MAX_CALLBACK_URL_LENGTH) {
      return finish(redirectResponse(resultUrl, "failed"), "invalid_request");
    }

    let url: URL;
    try {
      url = new URL(request.url);
    } catch {
      return finish(redirectResponse(resultUrl, "failed"), "invalid_request");
    }

    const errors = url.searchParams.getAll("error");
    if (errors.length > 0) {
      const outcome =
        errors.length === 1 && errors[0] === "access_denied"
          ? "cancelled"
          : "failed";
      return finish(redirectResponse(resultUrl, outcome), `provider_${outcome}`);
    }

    const codes = url.searchParams.getAll("code");
    const states = url.searchParams.getAll("state");
    if (
      codes.length !== 1 ||
      states.length !== 1 ||
      !validAuthorizationCode(codes[0]) ||
      !/^[0-9a-f]{64}$/.test(states[0])
    ) {
      return finish(redirectResponse(resultUrl, "failed"), "invalid_request");
    }

    let outcome: CompleteLineAccountLinkOutcome;
    try {
      outcome = await dependencies.completeLink({
        code: codes[0],
        state: states[0],
      });
    } catch {
      outcome = "internal_error";
    }

    return finish(
      redirectResponse(resultUrl, publicOutcome(outcome)),
      outcome,
    );
  };
}

function validAuthorizationCode(value: string): boolean {
  return (
    value.length >= 8 &&
    value.length <= 2048 &&
    !/[\u0000-\u0020\u007f]/.test(value)
  );
}

function validResultUrl(value: string): boolean {
  try {
    const url = new URL(value);
    const production =
      url.protocol === "https:" &&
      url.hostname === "tenniscourtwatcher.com";
    const local =
      url.protocol === "http:" &&
      (url.hostname === "127.0.0.1" || url.hostname === "localhost");
    return (
      (production || local) &&
      url.pathname === "/account/index.html" &&
      url.username === "" &&
      url.password === "" &&
      url.search === "" &&
      url.hash === ""
    );
  } catch {
    return false;
  }
}

function publicOutcome(outcome: CompleteLineAccountLinkOutcome): string {
  return (
      {
        linked: "success",
        friend_required: "friend_required",
        invalid_session: "expired",
        inactive_member: "inactive",
        member_conflict: "already_linked",
        line_conflict: "line_in_use",
        invalid_request: "failed",
        provider_error: "failed",
        internal_error: "failed",
      } as Record<CompleteLineAccountLinkOutcome, string>
    )[outcome];
}

function redirectResponse(baseUrl: string, outcome: string): Response {
  const location = new URL(baseUrl);
  location.searchParams.set("line_link", outcome);
  return new Response(null, {
    status: 303,
    headers: {
      "cache-control": "no-store",
      "content-length": "0",
      location: location.toString(),
      "referrer-policy": "no-referrer",
      "x-content-type-options": "nosniff",
    },
  });
}

function emptyResponse(status: number): Response {
  return new Response(null, {
    status,
    headers: {
      "cache-control": "no-store",
      "content-length": "0",
      "referrer-policy": "no-referrer",
      "x-content-type-options": "nosniff",
    },
  });
}
