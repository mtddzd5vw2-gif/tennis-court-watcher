import { createClient } from "npm:@supabase/supabase-js@2.95.0";
import {
  buildResendPayload,
  buildUnsubscribeUrl,
  classifyResendError,
  deterministicIdempotencyKey,
  EmailNotificationItem,
  extractResendErrorCode,
  extractResendMessageId,
  hmacPayloadFingerprint,
  renderEmail,
} from "./helpers.ts";

const RESEND_EMAIL_ENDPOINT = "https://api.resend.com/emails";
const MAX_BATCH_SIZE = 10;
const RESEND_TIMEOUT_MS = 15_000;
const USER_AGENT = "tennis-court-watcher-email-worker/1.0";

interface ClaimedMessage {
  message_id: string;
  user_id: string;
  channel: "email";
  attempt_count: number;
  locked_until: string;
  items: EmailNotificationItem[];
}

interface Metrics {
  claimed_count: number;
  accepted_count: number;
  retry_count: number;
  permanent_failure_count: number;
  cancelled_count: number;
}

function createWorkerClient(supabaseUrl: string, serviceRoleKey: string) {
  return createClient(supabaseUrl, serviceRoleKey, {
    auth: {
      autoRefreshToken: false,
      persistSession: false,
      detectSessionInUrl: false,
    },
  });
}

type SupabaseWorkerClient = ReturnType<typeof createWorkerClient>;

Deno.serve(async (request: Request): Promise<Response> => {
  const metrics = emptyMetrics();
  const finish = (status = 200): Response => {
    console.log(JSON.stringify(metrics));
    return new Response(JSON.stringify(metrics), {
      status,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
      },
    });
  };

  if (request.method !== "POST") {
    return finish(405);
  }

  // This endpoint is service-to-service only. It intentionally has no CORS
  // handling, and browser-originated requests are rejected.
  if (request.headers.has("origin")) {
    return finish(403);
  }

  const workerSecret = Deno.env.get("EMAIL_DELIVERY_WORKER_SECRET") ?? "";
  if (workerSecret.length < 32) {
    return finish(503);
  }
  const suppliedSecret = readBearerToken(request.headers.get("authorization"));
  if (
    suppliedSecret === null ||
    !(await secretsEqual(suppliedSecret, workerSecret))
  ) {
    return finish(401);
  }

  // Global delivery is fail-closed and disabled unless explicitly set to true.
  if (Deno.env.get("ENABLE_USER_EMAIL_NOTIFICATIONS") !== "true") {
    return finish();
  }

  const batchSize = await readBatchSize(request);
  if (batchSize === null) {
    return finish(400);
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
  const resendApiKey = Deno.env.get("RESEND_API_KEY") ?? "";
  const resendFrom = Deno.env.get("RESEND_FROM_EMAIL") ?? "";
  const payloadHmacKey = Deno.env.get("EMAIL_DELIVERY_PAYLOAD_HMAC_KEY") ?? "";
  const unsubscribePublicBaseUrl = Deno.env.get(
    "EMAIL_UNSUBSCRIBE_PUBLIC_BASE_URL",
  ) ?? "";
  if (
    supabaseUrl.length === 0 ||
    serviceRoleKey.length === 0 ||
    resendApiKey.length === 0 ||
    resendFrom.length === 0 ||
    payloadHmacKey.length < 32 ||
    unsubscribePublicBaseUrl.length === 0
  ) {
    return finish(503);
  }

  const supabase = createWorkerClient(supabaseUrl, serviceRoleKey);

  let claimData: unknown;
  let claimError: unknown;
  try {
    const claimResult = await supabase.rpc(
      "claim_email_messages",
      { batch_size: batchSize },
    );
    claimData = claimResult.data;
    claimError = claimResult.error;
  } catch {
    return finish(502);
  }
  if (claimError !== null || !Array.isArray(claimData)) {
    return finish(502);
  }

  const claimedMessages = claimData.slice(0, batchSize);
  metrics.claimed_count = claimedMessages.length;

  // Sequential processing is deliberate for the first delivery worker.
  for (const candidate of claimedMessages) {
    if (!isClaimedMessage(candidate)) {
      continue;
    }
    await processClaimedMessage(
      supabase,
      candidate,
      resendApiKey,
      resendFrom,
      payloadHmacKey,
      unsubscribePublicBaseUrl,
      metrics,
    );
  }

  return finish();
});

async function processClaimedMessage(
  supabase: SupabaseWorkerClient,
  message: ClaimedMessage,
  resendApiKey: string,
  resendFrom: string,
  payloadHmacKey: string,
  unsubscribePublicBaseUrl: string,
  metrics: Metrics,
): Promise<void> {
  try {
    const { data: unsubscribeToken, error: unsubscribeTokenError } =
      await supabase.rpc("get_email_unsubscribe_token_for_message", {
        p_message_id: message.message_id,
      });
    if (
      unsubscribeTokenError !== null ||
      typeof unsubscribeToken !== "string"
    ) {
      await recordFailure(
        supabase,
        message,
        "worker_internal_error",
        metrics,
      );
      return;
    }
    const unsubscribeUrl = buildUnsubscribeUrl(
      unsubscribePublicBaseUrl,
      unsubscribeToken,
    );

    const { data: authData, error: authError } = await supabase.auth.admin
      .getUserById(message.user_id);
    if (authError !== null) {
      await recordFailure(
        supabase,
        message,
        "auth_lookup_error",
        metrics,
      );
      return;
    }

    const recipient = authData.user?.email;
    if (typeof recipient !== "string" || recipient.trim().length === 0) {
      await recordFailure(
        supabase,
        message,
        "recipient_unavailable",
        metrics,
      );
      return;
    }

    const rendered = renderEmail(message.items);
    const providerPayload = buildResendPayload(
      resendFrom,
      recipient,
      rendered,
      message.message_id,
      unsubscribeUrl,
    );
    // Reuse this exact serialized string for both fingerprinting and fetch.
    const serializedPayload = JSON.stringify(providerPayload);
    const payloadFingerprint = await hmacPayloadFingerprint(
      serializedPayload,
      payloadHmacKey,
    );

    const { data: authorization, error: authorizationError } = await supabase
      .rpc("authorize_email_message_send", {
        p_message_id: message.message_id,
        p_locked_until: message.locked_until,
        p_provider_payload_fingerprint: payloadFingerprint,
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
      response = await fetch(RESEND_EMAIL_ENDPOINT, {
        method: "POST",
        headers: {
          authorization: `Bearer ${resendApiKey}`,
          "content-type": "application/json",
          "user-agent": USER_AGENT,
          "idempotency-key": deterministicIdempotencyKey(
            message.message_id,
          ),
        },
        body: serializedPayload,
        signal: AbortSignal.timeout(RESEND_TIMEOUT_MS),
      });
    } catch {
      const classified = classifyResendError(null, null);
      await recordFailure(
        supabase,
        message,
        classified.errorCode,
        metrics,
      );
      return;
    }

    const responseValue = await parseJsonResponse(response);
    if (!response.ok) {
      const classified = classifyResendError(
        response.status,
        extractResendErrorCode(responseValue),
      );
      await recordFailure(
        supabase,
        message,
        classified.errorCode,
        metrics,
      );
      return;
    }

    const providerMessageId = extractResendMessageId(responseValue);
    if (providerMessageId === null) {
      await recordFailure(
        supabase,
        message,
        "resend_unexpected_response",
        metrics,
      );
      return;
    }

    const { data: recorded, error: recordError } = await supabase.rpc(
      "record_email_message_accepted",
      {
        p_message_id: message.message_id,
        p_locked_until: message.locked_until,
        p_provider_message_id: providerMessageId,
      },
    );
    if (recordError === null && recorded === true) {
      metrics.accepted_count += 1;
    }
  } catch {
    await recordFailure(
      supabase,
      message,
      "worker_internal_error",
      metrics,
    );
  }
}

async function recordFailure(
  supabase: SupabaseWorkerClient,
  message: ClaimedMessage,
  errorCode: string,
  metrics: Metrics,
): Promise<void> {
  let outcome: unknown;
  let error: unknown;
  try {
    const result = await supabase.rpc(
      "record_email_message_failure",
      {
        p_message_id: message.message_id,
        p_locked_until: message.locked_until,
        p_error_code: errorCode,
      },
    );
    outcome = result.data;
    error = result.error;
  } catch {
    return;
  }
  if (error !== null) {
    return;
  }
  if (outcome === "retry_wait") {
    metrics.retry_count += 1;
  } else if (outcome === "failed_permanent") {
    metrics.permanent_failure_count += 1;
  }
}

async function parseJsonResponse(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
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

function readBearerToken(value: string | null): string | null {
  if (value === null) {
    return null;
  }
  const match = /^Bearer ([^\s]+)$/.exec(value);
  return match?.[1] ?? null;
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
  return (
    typeof candidate.message_id === "string" &&
    typeof candidate.user_id === "string" &&
    candidate.channel === "email" &&
    typeof candidate.attempt_count === "number" &&
    typeof candidate.locked_until === "string" &&
    Array.isArray(candidate.items) &&
    candidate.items.length > 0
  );
}

function emptyMetrics(): Metrics {
  return {
    claimed_count: 0,
    accepted_count: 0,
    retry_count: 0,
    permanent_failure_count: 0,
    cancelled_count: 0,
  };
}
