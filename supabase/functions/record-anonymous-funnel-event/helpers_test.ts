import { strict as assert } from "node:assert";
import {
  AnonymousFunnelEvent,
  createAnonymousFunnelHandler,
} from "./helpers.ts";

const ENDPOINT =
  "https://project.example.supabase.co/functions/v1/record-anonymous-funnel-event";
const ORIGIN = "https://tenniscourtwatcher.com";

function dependencies() {
  const calls: AnonymousFunnelEvent[] = [];
  return {
    calls,
    value: {
      getEnv(name: string) {
        return name === "SUPABASE_URL"
          ? "https://abcdefghijklmnopqrst.supabase.co"
          : "service-role-secret";
      },
      async recordEvent(
        _url: string,
        _key: string,
        eventName: AnonymousFunnelEvent,
      ) {
        calls.push(eventName);
        return { data: true, error: null };
      },
    },
  };
}

function request(
  eventName: string,
  options: { origin?: string; method?: string; contentType?: string } = {},
) {
  return new Request(ENDPOINT, {
    method: options.method ?? "POST",
    headers: {
      origin: options.origin ?? ORIGIN,
      "content-type": options.contentType ?? "text/plain;charset=UTF-8",
    },
    body: options.method === "OPTIONS"
      ? undefined
      : JSON.stringify({ event_name: eventName }),
  });
}

Deno.test("records one allowlisted event and returns no body", async () => {
  const fixture = dependencies();
  const handler = createAnonymousFunnelHandler(fixture.value);
  const response = await handler(request("line_start_click"));

  assert.equal(response.status, 204);
  assert.equal(await response.text(), "");
  assert.equal(response.headers.get("access-control-allow-origin"), ORIGIN);
  assert.deepEqual(fixture.calls, ["line_start_click"]);
});

Deno.test("preflight is origin-scoped and does not write", async () => {
  const fixture = dependencies();
  const handler = createAnonymousFunnelHandler(fixture.value);
  const response = await handler(
    request("login_page_view", { method: "OPTIONS" }),
  );

  assert.equal(response.status, 204);
  assert.equal(
    response.headers.get("access-control-allow-methods"),
    "POST, OPTIONS",
  );
  assert.deepEqual(fixture.calls, []);
});

Deno.test("rejects foreign origins and unknown event names", async () => {
  const fixture = dependencies();
  const handler = createAnonymousFunnelHandler(fixture.value);

  assert.equal(
    (await handler(request("login_page_view", {
      origin: "https://example.test",
    }))).status,
    403,
  );
  assert.equal((await handler(request("visitor_id"))).status, 400);
  assert.deepEqual(fixture.calls, []);
});

Deno.test("rejects query data and non-simple content types", async () => {
  const fixture = dependencies();
  const handler = createAnonymousFunnelHandler(fixture.value);
  const queryRequest = request("login_page_view");
  const withQuery = new Request(`${queryRequest.url}?visitor=forbidden`, {
    method: "POST",
    headers: queryRequest.headers,
    body: JSON.stringify({ event_name: "login_page_view" }),
  });

  assert.equal((await handler(withQuery)).status, 400);
  assert.equal(
    (await handler(request("login_page_view", {
      contentType: "application/json",
    }))).status,
    415,
  );
  assert.deepEqual(fixture.calls, []);
});
