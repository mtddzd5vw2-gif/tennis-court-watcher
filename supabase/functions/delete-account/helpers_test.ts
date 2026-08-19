import assert from "node:assert/strict";
import { test } from "node:test";
import {
  createDeleteAccountHandler,
  DeleteAccountDependencies,
  MAX_JSON_BODY_BYTES,
} from "./helpers.ts";

const ENDPOINT = "https://project.supabase.co/functions/v1/delete-account";
const ACCESS_TOKEN = "header.payload.signature";
const PROD_ORIGIN = "https://tenniscourtwatcher.com";

function makeHandler(options: {
  deleteAccount?: DeleteAccountDependencies["deleteAccount"];
  serviceRoleKey?: string | null;
  logs?: string[];
} = {}) {
  return createDeleteAccountHandler({
    getEnv: (name) =>
      ({
        SUPABASE_URL: "https://project.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY: options.serviceRoleKey === null
          ? undefined
          : options.serviceRoleKey ?? "service-role-key",
      })[name],
    deleteAccount: options.deleteAccount ??
      (async () => "deleted"),
    log: (value) => options.logs?.push(value),
  });
}

function request(
  body: unknown = {
    confirmation: "delete-my-account",
  },
  options: {
    method?: string;
    origin?: string | null;
    token?: string | null;
  } = {},
) {
  const headers = new Headers({
    "content-type": "application/json",
  });

  if (options.origin !== null) {
    headers.set(
      "origin",
      options.origin ?? PROD_ORIGIN,
    );
  }

  if (options.token !== null) {
    headers.set(
      "authorization",
      `Bearer ${options.token ?? ACCESS_TOKEN}`,
    );
  }

  return new Request(ENDPOINT, {
    method: options.method ?? "POST",
    headers,
    body: (options.method ?? "POST") === "POST"
      ? JSON.stringify(body)
      : undefined,
  });
}

test("valid authenticated confirmation deletes account", async () => {
  let observedToken = "";

  const response = await makeHandler({
    deleteAccount: async (_url, _key, token) => {
      observedToken = token;
      return "deleted";
    },
  })(request());

  assert.equal(response.status, 204);
  assert.equal(observedToken, ACCESS_TOKEN);
  assert.equal(await response.text(), "");
  assert.equal(
    response.headers.get("access-control-allow-origin"),
    PROD_ORIGIN,
  );
});

test("request body never supplies account identity", async () => {
  let called = false;

  const response = await makeHandler({
    deleteAccount: async () => {
      called = true;
      return "deleted";
    },
  })(
    request({
      confirmation: "delete-my-account",
      user_id: "223e4567-e89b-42d3-a456-426614174000",
    }),
  );

  assert.equal(response.status, 400);
  assert.equal(called, false);
});

test("streaming body is bounded even without content-length", async () => {
  let cancelled = false;
  let called = false;
  let chunkNumber = 0;

  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (chunkNumber === 0) {
        controller.enqueue(
          new Uint8Array(MAX_JSON_BODY_BYTES).fill(0x61),
        );
      } else {
        controller.enqueue(new Uint8Array([0x61]));
      }
      chunkNumber += 1;
    },
    cancel() {
      cancelled = true;
    },
  });

  const response = await makeHandler({
    deleteAccount: async () => {
      called = true;
      return "deleted";
    },
  })(
    new Request(
      ENDPOINT,
      {
        method: "POST",
        headers: {
          origin: PROD_ORIGIN,
          authorization: `Bearer ${ACCESS_TOKEN}`,
          "content-type": "application/json",
        },
        body,
        duplex: "half",
      } as RequestInit & { duplex: "half" },
    ),
  );

  assert.equal(response.status, 400);
  assert.equal(called, false);
  assert.equal(cancelled, true);
});

test("wrong confirmation cannot delete account", async () => {
  let called = false;

  const response = await makeHandler({
    deleteAccount: async () => {
      called = true;
      return "deleted";
    },
  })(
    request({
      confirmation: "yes",
    }),
  );

  assert.equal(response.status, 400);
  assert.equal(called, false);
});

test("missing bearer token is unauthorized", async () => {
  let called = false;

  const response = await makeHandler({
    deleteAccount: async () => {
      called = true;
      return "deleted";
    },
  })(
    request(
      undefined,
      { token: null },
    ),
  );

  assert.equal(response.status, 401);
  assert.equal(called, false);
});

test("invalid browser origin is rejected before deletion", async () => {
  let called = false;

  const response = await makeHandler({
    deleteAccount: async () => {
      called = true;
      return "deleted";
    },
  })(
    request(
      undefined,
      { origin: "https://example.invalid" },
    ),
  );

  assert.equal(response.status, 403);
  assert.equal(called, false);
});

test("OPTIONS provides production CORS preflight", async () => {
  const response = await makeHandler()(
    new Request(ENDPOINT, {
      method: "OPTIONS",
      headers: {
        origin: PROD_ORIGIN,
      },
    }),
  );

  assert.equal(response.status, 204);
  assert.equal(
    response.headers.get("access-control-allow-origin"),
    PROD_ORIGIN,
  );
  assert.match(
    response.headers.get("access-control-allow-headers") ?? "",
    /authorization/,
  );
});

test("non-POST methods cannot delete account", async () => {
  const response = await makeHandler()(
    request(
      undefined,
      { method: "GET" },
    ),
  );

  assert.equal(response.status, 405);
});

test("missing server configuration fails closed", async () => {
  let called = false;

  const response = await makeHandler({
    serviceRoleKey: null,
    deleteAccount: async () => {
      called = true;
      return "deleted";
    },
  })(request());

  assert.equal(response.status, 503);
  assert.equal(called, false);
});

test("invalid authenticated user remains unauthorized", async () => {
  const response = await makeHandler({
    deleteAccount: async () => "unauthorized",
  })(request());

  assert.equal(response.status, 401);
});

for (
  const outcome of [
    "profile_lock_failed",
    "delete_failed",
    "internal_error",
  ] as const
) {
  test(`${outcome} remains retryable server failure`, async () => {
    const response = await makeHandler({
      deleteAccount: async () => outcome,
    })(request());

    assert.equal(response.status, 502);
  });
}

test("logs never contain bearer token", async () => {
  const logs: string[] = [];

  const response = await makeHandler({
    logs,
  })(request());

  assert.equal(response.status, 204);
  assert.equal(
    logs.some((entry) => entry.includes(ACCESS_TOKEN)),
    false,
  );
  assert.deepEqual(
    logs.map((entry) => JSON.parse(entry)),
    [{ outcome: "deleted" }],
  );
});
