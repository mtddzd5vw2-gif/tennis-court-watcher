import { createClient } from "npm:@supabase/supabase-js@2.95.0";
import {
  buildLinePushPayload,
  classifyLineError,
  deterministicLineRetryKey,
  extractQuotaConsumption,
  hmacPayloadFingerprint,
  LINE_CANARY_TEST_TEXT,
  LineNotificationItem,
  normalizeLineRequestId,
  renderLineMessage,
} from "./helpers.ts";

const LINE_PUSH_ENDPOINT = "https://api.line.me/v2/bot/message/push";
const LINE_QUOTA_CONSUMPTION_ENDPOINT =
  "https://api.line.me/v2/bot/message/quota/consumption";
const MAX_BATCH_SIZE = 10;
const LINE_TIMEOUT_MS = 15_000;
const MAX_PROVIDER_RESPONSE_BYTES = 16 * 1024;
const USER_AGENT = "tennis-court-watcher-line-worker/1.0";
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

interface ClaimedMessage {
  message_id: string;
  user_id: string;
  line_user_id: string;
  channel: "line";
  attempt_count: number;
  locked_until: string;
  test_text: string | null;
  items: LineNotificationItem[];
}

interface RolloutControls {
  canaryUserId: string | null;
  allowAll: boolean;
}

interface Metrics {
  claimed_count: number;
  accepted_count: number;
  retry_count: number;
  permanent_failure_count: number;
  cancelled_count: number;
  quota_consumption: number;
  quota_limit: number;
  quota_exhausted: boolean;
}

type SupabaseWorkerClient = ReturnType<typeof createClient<any>>;

Deno.serve(async (request: Request): Promise<Response> => {
  if (request.method !== "POST" || request.headers.has("origin")) {
    return jsonResponse(405, { error: "method_not_allowed" });
  }

  const workerSecret = Deno.env.get("LINE_DELIVERY_WORKER_SECRET") ?? "";
  const suppliedSecret = readBearerToken(request.headers.get("authorization"));
  if (
    workerSecret.length < 32 ||
    suppliedSecret === null ||
    !(await secretsEqual(suppliedSecret, workerSecret))
  ) {
    return jsonResponse(401, { error: "unauthorized" });
  }
  if (Deno.env.get("ENABLE_USER_LINE_NOTIFICATIONS") !== "true") {
    return jsonResponse(503, { error: "delivery_disabled" });
  }

  const rolloutControls = readRolloutControls(
    Deno.env.get("LINE_NOTIFICATION_CANARY_USER_ID"),
    Deno.env.get("LINE_NOTIFICATION_ALLOW_ALL"),
  );
  if (rolloutControls === null) {
    return jsonResponse(503, { error: "service_unavailable" });
  }

  const batchSize = await readBatchSize(request);
  if (batchSize === null) {
    return jsonResponse(400, { error: "invalid_request" });
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
  const channelAccessToken = Deno.env.get("LINE_CHANNEL_ACCESS_TOKEN") ?? "";
  const payloadHmacKey = Deno.env.get("LINE_DELIVERY_PAYLOAD_HMAC_KEY") ?? "";
  const quotaLimit = readQuotaLimit(Deno.env.get("LINE_MONTHLY_PUSH_LIMIT"));
  if (
    !/^https:\/\/[a-z0-9]{20}\.supabase\.co$/.test(supabaseUrl) ||
    serviceRoleKey.trim().length === 0 ||
    channelAccessToken.trim().length < 32 ||
    payloadHmacKey.length < 32 ||
    quotaLimit === null
  ) {
    return jsonResponse(503, { error: "service_unavailable" });
  }

  const quotaConsumption = await fetchQuotaConsumption(channelAccessToken);
  if (quotaConsumption === null) {
    return jsonResponse(502, { error: "quota_check_failed" });
  }
  const metrics = emptyMetrics(quotaConsumption, quotaLimit);
  const remainingQuota = Math.max(0, quotaLimit - quotaConsumption);
  if (remainingQuota === 0) {
    metrics.quota_exhausted = true;
    return jsonResponse(200, metrics);
  }

  const supabase = createClient(supabaseUrl, serviceRoleKey, {
    auth: {
      autoRefreshToken: false,
      persistSession: false,
      detectSessionInUrl: false,
    },
  });
  const claimSize = Math.min(batchSize, remainingQuota);
  const { data: claimed, error: claimError } = await supabase.rpc(
    "claim_line_messages",
    {
      batch_size: claimSize,
      p_canary_user_id: rolloutControls.canaryUserId,
      p_allow_all: rolloutControls.allowAll,
    },
  );
  if (claimError !== null || !Array.isArray(claimed)) {
    return jsonResponse(500, { error: "claim_failed" });
  }

  const claimedMessages = claimed.filter(isClaimedMessage);
  if (claimedMessages.length !== claimed.length) {
    return jsonResponse(500, { error: "claim_failed" });
  }
  metrics.claimed_count = claimedMessages.length;

  for (const message of claimedMessages) {
    await processMessage(
      supabase,
      message,
      channelAccessToken,
      payloadHmacKey,
      rolloutControls,
      metrics,
    );
  }
  return jsonResponse(200, metrics);
});

async function processMessage(
  supabase: SupabaseWorkerClient,
  message: ClaimedMessage,
  channelAccessToken: string,
  payloadHmacKey: string,
  rolloutControls: RolloutControls,
  metrics: Metrics,
): Promise<void> {
  try {
    const text = message.test_text ?? renderLineMessage(message.items);
    const providerPayload = buildLinePushPayload(message.line_user_id, text);
    const serializedPayload = JSON.stringify(providerPayload);
    const payloadFingerprint = await hmacPayloadFingerprint(
      serializedPayload,
      payloadHmacKey,
    );

    const { data: authorization, error: authorizationError } = await supabase
      .rpc("authorize_line_message_send", {
        p_message_id: message.message_id,
        p_locked_until: message.locked_until,
        p_line_user_id: message.line_user_id,
        p_provider_payload_fingerprint: payloadFingerprint,
        p_canary_user_id: rolloutControls.canaryUserId,
        p_allow_all: rolloutControls.allowAll,
      });
    if (authorizationError !== null) {
      return;
    }
    if (authorization === "cancelled") {
      metrics.cancelled_count += 1;
      return;
    }
    if (authorization === "failed_permanent") {
      metrics.permanent_failure_count += 1;
      return;
    }
    if (authorization !== "authorized") {
      return;
    }

    let response: Response;
    try {
      response = await fetch(LINE_PUSH_ENDPOINT, {
        method: "POST",
        headers: {
          authorization: `Bearer ${channelAccessToken}`,
          "content-type": "application/json",
          "user-agent": USER_AGENT,
          "x-line-retry-key": deterministicLineRetryKey(message.message_id),
        },
        body: serializedPayload,
        signal: AbortSignal.timeout(LINE_TIMEOUT_MS),
      });
    } catch {
      await recordFailure(supabase, message, "line_network_error", metrics);
      return;
    }

    await consumeBoundedBody(response);
    if (response.status === 200) {
      const providerId = normalizeLineRequestId(
        response.headers.get("x-line-request-id"),
      );
      if (providerId === null) {
        await recordFailure(
          supabase,
          message,
          "line_unexpected_response",
          metrics,
        );
        return;
      }
      await recordAccepted(supabase, message, providerId, "accepted", metrics);
      return;
    }
    if (response.status === 409) {
      const providerId = normalizeLineRequestId(
        response.headers.get("x-line-accepted-request-id"),
      );
      if (providerId === null) {
        await recordFailure(
          supabase,
          message,
          "line_unexpected_response",
          metrics,
        );
        return;
      }
      await recordAccepted(
        supabase,
        message,
        providerId,
        "accepted_retry",
        metrics,
      );
      return;
    }

    const classified = classifyLineError(response.status);
    await recordFailure(supabase, message, classified.errorCode, metrics);
  } catch {
    await recordFailure(supabase, message, "worker_internal_error", metrics);
  }
}

async function recordAccepted(
  supabase: SupabaseWorkerClient,
  message: ClaimedMessage,
  providerMessageId: string,
  providerStatus: "accepted" | "accepted_retry",
  metrics: Metrics,
): Promise<void> {
  const { data, error } = await supabase.rpc("record_line_message_accepted", {
    p_message_id: message.message_id,
    p_locked_until: message.locked_until,
    p_provider_message_id: providerMessageId,
    p_provider_status: providerStatus,
  });
  if (error === null && data === true) {
    metrics.accepted_count += 1;
  }
}

async function recordFailure(
  supabase: SupabaseWorkerClient,
  message: ClaimedMessage,
  errorCode: string,
  metrics: Metrics,
): Promise<void> {
  try {
    const { data, error } = await supabase.rpc("record_line_message_failure", {
      p_message_id: message.message_id,
      p_locked_until: message.locked_until,
      p_error_code: errorCode,
    });
    if (error !== null) {
      return;
    }
    if (data === "retry_wait") {
      metrics.retry_count += 1;
    } else if (data === "failed_permanent") {
      metrics.permanent_failure_count += 1;
    }
  } catch {
    return;
  }
}

async function fetchQuotaConsumption(token: string): Promise<number | null> {
  let response: Response;
  try {
    response = await fetch(LINE_QUOTA_CONSUMPTION_ENDPOINT, {
      method: "GET",
      headers: {
        authorization: `Bearer ${token}`,
        "user-agent": USER_AGENT,
      },
      signal: AbortSignal.timeout(LINE_TIMEOUT_MS),
    });
  } catch {
    return null;
  }
  if (response.status !== 200) {
    await consumeBoundedBody(response);
    return null;
  }
  let value: unknown;
  try {
    const body = await readBoundedText(response);
    value = JSON.parse(body);
  } catch {
    return null;
  }
  return extractQuotaConsumption(value);
}

async function consumeBoundedBody(response: Response): Promise<void> {
  try {
    await readBoundedText(response);
  } catch {
    // Provider response bodies are never logged or persisted.
  }
}

async function readBoundedText(response: Response): Promise<string> {
  const body = await response.text();
  if (new TextEncoder().encode(body).byteLength > MAX_PROVIDER_RESPONSE_BYTES) {
    throw new Error("provider response too large");
  }
  return body;
}

async function readBatchSize(request: Request): Promise<number | null> {
  const contentLength = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(contentLength) && contentLength > 1024) {
    return null;
  }
  let value: unknown;
  try {
    const body = await request.text();
    value = body.trim().length === 0 ? {} : JSON.parse(body);
  } catch {
    return null;
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const input = value as Record<string, unknown>;
  if (Object.keys(input).some((key) => key !== "batch_size")) {
    return null;
  }
  const batchSize = input.batch_size ?? MAX_BATCH_SIZE;
  if (
    typeof batchSize !== "number" ||
    !Number.isInteger(batchSize) ||
    batchSize < 1 ||
    batchSize > MAX_BATCH_SIZE
  ) {
    return null;
  }
  return batchSize;
}

function readQuotaLimit(value: string | undefined): number | null {
  if (value === undefined || !/^\d{1,3}$/.test(value)) {
    return null;
  }
  const limit = Number(value);
  return Number.isInteger(limit) && limit >= 1 && limit <= 180 ? limit : null;
}

function readBearerToken(value: string | null): string | null {
  if (value === null) {
    return null;
  }
  return /^Bearer ([^\s]+)$/.exec(value)?.[1] ?? null;
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

function isClaimedMessage(value: unknown): value is ClaimedMessage {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  const hasRegularItems =
    candidate.test_text === null &&
    Array.isArray(candidate.items) &&
    candidate.items.length > 0;
  const hasCanaryText =
    candidate.test_text === LINE_CANARY_TEST_TEXT &&
    Array.isArray(candidate.items) &&
    candidate.items.length === 0;
  return (
    typeof candidate.message_id === "string" &&
    typeof candidate.user_id === "string" &&
    typeof candidate.line_user_id === "string" &&
    /^U[0-9a-f]{32}$/.test(candidate.line_user_id) &&
    candidate.channel === "line" &&
    typeof candidate.attempt_count === "number" &&
    typeof candidate.locked_until === "string" &&
    (hasRegularItems || hasCanaryText)
  );
}

function readRolloutControls(
  canaryValue: string | undefined,
  allowAllValue: string | undefined,
): RolloutControls | null {
  if (allowAllValue !== "true" && allowAllValue !== "false") {
    return null;
  }
  const allowAll = allowAllValue === "true";
  const canary = canaryValue?.trim() ?? "";
  const normalizedCanary = UUID_PATTERN.test(canary)
    ? canary.toLowerCase()
    : null;
  if (
    (allowAll && canary.length > 0) ||
    (!allowAll && normalizedCanary === null)
  ) {
    return null;
  }
  return {
    canaryUserId: normalizedCanary,
    allowAll,
  };
}

function emptyMetrics(quotaConsumption: number, quotaLimit: number): Metrics {
  return {
    claimed_count: 0,
    accepted_count: 0,
    retry_count: 0,
    permanent_failure_count: 0,
    cancelled_count: 0,
    quota_consumption: quotaConsumption,
    quota_limit: quotaLimit,
    quota_exhausted: false,
  };
}

function jsonResponse(status: number, value: object): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}
