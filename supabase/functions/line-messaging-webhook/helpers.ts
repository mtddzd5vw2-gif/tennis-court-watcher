export interface NormalizedLineWebhookEvent {
  webhook_event_id: string;
  event_type: "follow" | "unfollow";
  line_user_id: string;
  occurred_at: string;
}

export interface LineWebhookMetrics {
  relevant_event_count: number;
  inserted_event_count: number;
  updated_link_count: number;
}

export interface RecordLineWebhookEventsResult {
  data: unknown;
  error: unknown;
}

export interface LineWebhookDependencies {
  getEnv(name: string): string | undefined;
  recordEvents(
    supabaseUrl: string,
    serviceRoleKey: string,
    events: NormalizedLineWebhookEvent[],
  ): Promise<RecordLineWebhookEventsResult>;
  forwardLegacyWebhook?(
    url: string,
    rawBody: Uint8Array,
    signature: string,
  ): Promise<boolean>;
  now?(): number;
}

const MAX_BODY_BYTES = 128 * 1024;
const MAX_EVENTS = 100;
const MAX_FUTURE_SKEW_MS = 5 * 60 * 1000;
const EVENT_ID_PATTERN = /^[A-Za-z0-9_-]{1,255}$/;
const LINE_USER_ID_PATTERN = /^U[0-9a-f]{32}$/;
const SUPABASE_PROJECT_URL_PATTERN = /^https:\/\/[a-z0-9]{20}\.supabase\.co$/;
const LEGACY_WEBHOOK_PATH_PATTERN = /^\/macros\/s\/[A-Za-z0-9_-]{20,}\/exec$/;

export function normalizeLegacyWebhookUrl(value: string): string | null {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (
    url.protocol !== "https:" ||
    url.hostname !== "script.google.com" ||
    url.port !== "" ||
    url.username !== "" ||
    url.password !== "" ||
    url.search !== "" ||
    url.hash !== "" ||
    !LEGACY_WEBHOOK_PATH_PATTERN.test(url.pathname)
  ) {
    return null;
  }
  return url.toString();
}

export async function verifyLineWebhookSignature(
  rawBody: Uint8Array,
  channelSecret: string,
  suppliedSignature: string,
): Promise<boolean> {
  if (
    channelSecret.length < 16 ||
    !/^[A-Za-z0-9+/]{43}=$/.test(suppliedSignature)
  ) {
    return false;
  }
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(channelSecret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const bodyCopy = new Uint8Array(rawBody.byteLength);
  bodyCopy.set(rawBody);
  const digest = new Uint8Array(
    await crypto.subtle.sign("HMAC", key, bodyCopy.buffer),
  );
  const expected = bytesToBase64(digest);
  return constantTimeEqual(expected, suppliedSignature);
}

export function extractRelevantLineEvents(
  value: unknown,
  nowMs = Date.now(),
): NormalizedLineWebhookEvent[] {
  if (!isRecord(value) || !Array.isArray(value.events)) {
    throw new Error("invalid LINE webhook payload");
  }
  if (value.events.length > MAX_EVENTS) {
    throw new Error("too many LINE webhook events");
  }

  const normalized: NormalizedLineWebhookEvent[] = [];
  for (const event of value.events) {
    if (!isRecord(event)) {
      throw new Error("invalid LINE webhook event");
    }
    if (event.type !== "follow" && event.type !== "unfollow") {
      continue;
    }
    if (
      typeof event.webhookEventId !== "string" ||
      !EVENT_ID_PATTERN.test(event.webhookEventId) ||
      typeof event.timestamp !== "number" ||
      !Number.isSafeInteger(event.timestamp) ||
      event.timestamp < 0 ||
      event.timestamp > nowMs + MAX_FUTURE_SKEW_MS ||
      !isRecord(event.source) ||
      event.source.type !== "user" ||
      typeof event.source.userId !== "string" ||
      !LINE_USER_ID_PATTERN.test(event.source.userId)
    ) {
      throw new Error("invalid LINE follow state event");
    }
    normalized.push({
      webhook_event_id: event.webhookEventId,
      event_type: event.type,
      line_user_id: event.source.userId,
      occurred_at: new Date(event.timestamp).toISOString(),
    });
  }
  return normalized;
}

export function createLineWebhookHandler(
  dependencies: LineWebhookDependencies,
): (request: Request) => Promise<Response> {
  return async (request: Request): Promise<Response> => {
    if (request.method !== "POST") {
      return response(405, { error: "method_not_allowed" });
    }
    const declaredLength = Number(request.headers.get("content-length") ?? "0");
    if (Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES) {
      return response(413, { error: "payload_too_large" });
    }

    const channelSecret =
      dependencies.getEnv("LINE_MESSAGING_CHANNEL_SECRET") ?? "";
    const supabaseUrl = dependencies.getEnv("SUPABASE_URL") ?? "";
    const serviceRoleKey = dependencies.getEnv("SUPABASE_SERVICE_ROLE_KEY") ??
      "";
    const bridgeEnabledValue =
      dependencies.getEnv("LINE_WEBHOOK_BRIDGE_ENABLED") ?? "false";
    const bridgeEnabled = bridgeEnabledValue === "true";
    const legacyWebhookUrl = bridgeEnabled
      ? normalizeLegacyWebhookUrl(
        dependencies.getEnv("LINE_LEGACY_WEBHOOK_URL") ?? "",
      )
      : null;
    if (
      channelSecret.length < 16 ||
      !SUPABASE_PROJECT_URL_PATTERN.test(supabaseUrl) ||
      serviceRoleKey.trim().length === 0 ||
      (bridgeEnabledValue !== "true" && bridgeEnabledValue !== "false") ||
      (bridgeEnabled &&
        (legacyWebhookUrl === null ||
          dependencies.forwardLegacyWebhook === undefined))
    ) {
      return response(503, { error: "service_unavailable" });
    }

    let rawBody: Uint8Array;
    try {
      rawBody = new Uint8Array(await request.arrayBuffer());
    } catch {
      return response(400, { error: "invalid_request" });
    }
    if (rawBody.byteLength > MAX_BODY_BYTES) {
      return response(413, { error: "payload_too_large" });
    }

    const signature = request.headers.get("x-line-signature") ?? "";
    if (
      !(await verifyLineWebhookSignature(rawBody, channelSecret, signature))
    ) {
      return response(401, { error: "invalid_signature" });
    }

    let events: NormalizedLineWebhookEvent[];
    try {
      const decoded = new TextDecoder("utf-8", { fatal: true }).decode(rawBody);
      events = extractRelevantLineEvents(
        JSON.parse(decoded),
        dependencies.now?.() ?? Date.now(),
      );
    } catch {
      return response(400, { error: "invalid_payload" });
    }

    let metrics = emptyMetrics();
    if (events.length > 0) {
      let result: RecordLineWebhookEventsResult;
      try {
        result = await dependencies.recordEvents(
          supabaseUrl,
          serviceRoleKey,
          events,
        );
      } catch {
        return response(500, { error: "processing_failed" });
      }
      const normalized = normalizeMetrics(result.data);
      if (result.error !== null || normalized === null) {
        return response(500, { error: "processing_failed" });
      }
      metrics = normalized;
    }

    if (bridgeEnabled && legacyWebhookUrl !== null) {
      let forwarded: boolean;
      try {
        forwarded = await dependencies.forwardLegacyWebhook!(
          legacyWebhookUrl,
          rawBody,
          signature,
        );
      } catch {
        forwarded = false;
      }
      if (!forwarded) {
        return response(502, { error: "legacy_bridge_failed" });
      }
    }
    return response(200, metrics);
  };
}

function normalizeMetrics(value: unknown): LineWebhookMetrics | null {
  if (!Array.isArray(value) || value.length !== 1 || !isRecord(value[0])) {
    return null;
  }
  const record = value[0];
  const keys = [
    "relevant_event_count",
    "inserted_event_count",
    "updated_link_count",
  ] as const;
  if (Object.keys(record).sort().join(",") !== [...keys].sort().join(",")) {
    return null;
  }
  const relevantEventCount = record.relevant_event_count;
  const insertedEventCount = record.inserted_event_count;
  const updatedLinkCount = record.updated_link_count;
  if (
    !isNonnegativeInteger(relevantEventCount) ||
    !isNonnegativeInteger(insertedEventCount) ||
    !isNonnegativeInteger(updatedLinkCount) ||
    insertedEventCount > relevantEventCount ||
    updatedLinkCount > insertedEventCount
  ) {
    return null;
  }
  return {
    relevant_event_count: relevantEventCount,
    inserted_event_count: insertedEventCount,
    updated_link_count: updatedLinkCount,
  };
}

function emptyMetrics(): LineWebhookMetrics {
  return {
    relevant_event_count: 0,
    inserted_event_count: 0,
    updated_link_count: 0,
  };
}

function response(status: number, body: object): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function constantTimeEqual(left: string, right: string): boolean {
  const leftBytes = new TextEncoder().encode(left);
  const rightBytes = new TextEncoder().encode(right);
  let difference = leftBytes.length ^ rightBytes.length;
  const length = Math.max(leftBytes.length, rightBytes.length);
  for (let index = 0; index < length; index += 1) {
    difference |= (leftBytes[index] ?? 0) ^ (rightBytes[index] ?? 0);
  }
  return difference === 0;
}

function bytesToBase64(value: Uint8Array): string {
  let binary = "";
  for (const byte of value) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonnegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}
