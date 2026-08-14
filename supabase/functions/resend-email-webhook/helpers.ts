import { Webhook, WebhookVerificationError } from "npm:svix@1.99.1";

export const MAX_WEBHOOK_BODY_BYTES = 64 * 1024;

export const SUPPORTED_RESEND_EVENT_TYPES = [
  "email.sent",
  "email.delivery_delayed",
  "email.delivered",
  "email.failed",
  "email.bounced",
  "email.complained",
  "email.suppressed",
] as const;

export type SupportedResendEventType =
  typeof SUPPORTED_RESEND_EVENT_TYPES[number];

export interface RecordResendEmailEventArgs {
  p_provider_event_id: string;
  p_provider_message_id: string;
  p_event_type: SupportedResendEventType;
  p_occurred_at: string;
  p_source_tag: string | null;
  p_message_id_tag: string | null;
}

export interface WebhookAggregate {
  outcome: string;
  event_type: string | null;
  stored_event_count: number;
  preference_disabled_count: number;
}

export interface WebhookDependencies {
  getEnv: (name: string) => string | undefined;
  recordEvent: (
    supabaseUrl: string,
    serviceRoleKey: string,
    args: RecordResendEmailEventArgs,
  ) => Promise<{ data: unknown; error: unknown }>;
  verifySignature?: typeof verifyResendSignature;
  log?: (value: string) => void;
}

interface ParsedSupportedEvent {
  kind: "supported";
  eventType: SupportedResendEventType;
  occurredAt: string;
  providerMessageId: string;
  sourceTag: string | null;
  messageIdTag: string | null;
}

interface ParsedUnsupportedEvent {
  kind: "unsupported";
  eventType: string;
}

interface ParsedMalformedEvent {
  kind: "malformed";
  eventType: string | null;
}

type ParsedEvent =
  | ParsedSupportedEvent
  | ParsedUnsupportedEvent
  | ParsedMalformedEvent;

const EVENT_TYPE_PATTERN = /^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,2}$/;
const PROVIDER_MESSAGE_ID_PATTERN = /^[A-Za-z0-9_-]{1,255}$/;
const TAG_VALUE_PATTERN = /^[A-Za-z0-9_-]{1,256}$/;
const ISO_TIMESTAMP_PATTERN =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/;

export class WebhookConfigurationError extends Error {
  constructor() {
    super("Webhook configuration is invalid.");
    this.name = "WebhookConfigurationError";
  }
}

export class WebhookBodyTooLargeError extends Error {
  constructor() {
    super("Webhook body is too large.");
    this.name = "WebhookBodyTooLargeError";
  }
}

export function verifyResendSignature(
  rawBody: string,
  headers: Record<string, string>,
  signingSecret: string,
): unknown {
  let webhook: Webhook;
  try {
    webhook = new Webhook(signingSecret);
  } catch {
    throw new WebhookConfigurationError();
  }
  return webhook.verify(rawBody, headers);
}

export function createResendWebhookHandler(
  dependencies: WebhookDependencies,
): (request: Request) => Promise<Response> {
  const verifySignature = dependencies.verifySignature ?? verifyResendSignature;
  const log = dependencies.log ?? console.log;

  return async (request: Request): Promise<Response> => {
    const finish = (
      status: number,
      outcome: string,
      eventType: string | null = null,
      storedEventCount = 0,
      preferenceDisabledCount = 0,
    ): Response => {
      const aggregate: WebhookAggregate = {
        outcome,
        event_type: eventType,
        stored_event_count: storedEventCount,
        preference_disabled_count: preferenceDisabledCount,
      };
      const serialized = JSON.stringify(aggregate);
      log(serialized);
      return new Response(serialized, {
        status,
        headers: {
          "content-type": "application/json; charset=utf-8",
          "cache-control": "no-store",
        },
      });
    };

    if (request.method !== "POST") {
      return finish(405, "method_not_allowed");
    }
    if (request.headers.has("origin")) {
      return finish(403, "origin_rejected");
    }

    const contentLength = request.headers.get("content-length");
    if (contentLength !== null) {
      const declaredLength = Number(contentLength);
      if (
        !Number.isFinite(declaredLength) ||
        declaredLength < 0 ||
        declaredLength > MAX_WEBHOOK_BODY_BYTES
      ) {
        return finish(413, "payload_too_large");
      }
    }

    const svixId = request.headers.get("svix-id");
    const svixTimestamp = request.headers.get("svix-timestamp");
    const svixSignature = request.headers.get("svix-signature");
    if (
      svixId === null ||
      svixTimestamp === null ||
      svixSignature === null
    ) {
      return finish(401, "unauthorized");
    }

    const signingSecret =
      dependencies.getEnv("RESEND_WEBHOOK_SIGNING_SECRET") ?? "";
    if (signingSecret.length === 0) {
      return finish(503, "configuration_error");
    }

    let rawBody: string;
    try {
      rawBody = await readRawBody(request, MAX_WEBHOOK_BODY_BYTES);
    } catch (error) {
      if (error instanceof WebhookBodyTooLargeError) {
        return finish(413, "payload_too_large");
      }
      return finish(400, "invalid_payload");
    }

    let verifiedPayload: unknown;
    try {
      verifiedPayload = verifySignature(
        rawBody,
        {
          "svix-id": svixId,
          "svix-timestamp": svixTimestamp,
          "svix-signature": svixSignature,
        },
        signingSecret,
      );
    } catch (error) {
      if (error instanceof WebhookConfigurationError) {
        return finish(503, "configuration_error");
      }
      // Svix parses JSON only after a signature has matched. A SyntaxError is
      // therefore a signed but malformed payload rather than an auth failure.
      if (error instanceof SyntaxError) {
        return finish(400, "invalid_payload");
      }
      if (error instanceof WebhookVerificationError) {
        return finish(401, "unauthorized");
      }
      return finish(401, "unauthorized");
    }

    const parsed = parseResendEvent(verifiedPayload);
    if (parsed.kind === "malformed") {
      return finish(422, "invalid_payload", parsed.eventType);
    }
    if (parsed.kind === "unsupported") {
      return finish(200, "ignored_unsupported", parsed.eventType);
    }

    const supabaseUrl = dependencies.getEnv("SUPABASE_URL") ?? "";
    const serviceRoleKey = dependencies.getEnv("SUPABASE_SERVICE_ROLE_KEY") ??
      "";
    if (supabaseUrl.length === 0 || serviceRoleKey.length === 0) {
      return finish(503, "configuration_error", parsed.eventType);
    }

    let rpcResult: { data: unknown; error: unknown };
    try {
      rpcResult = await dependencies.recordEvent(
        supabaseUrl,
        serviceRoleKey,
        {
          p_provider_event_id: svixId,
          p_provider_message_id: parsed.providerMessageId,
          p_event_type: parsed.eventType,
          p_occurred_at: parsed.occurredAt,
          p_source_tag: parsed.sourceTag,
          p_message_id_tag: parsed.messageIdTag,
        },
      );
    } catch {
      return finish(502, "retryable_error", parsed.eventType);
    }

    if (rpcResult.error !== null) {
      return finish(502, "retryable_error", parsed.eventType);
    }
    const normalized = normalizeRpcAggregate(rpcResult.data);
    if (normalized === null) {
      return finish(502, "retryable_error", parsed.eventType);
    }

    return finish(
      200,
      normalized.outcome,
      parsed.eventType,
      normalized.storedEventCount,
      normalized.preferenceDisabledCount,
    );
  };
}

export function parseResendEvent(value: unknown): ParsedEvent {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return { kind: "malformed", eventType: null };
  }
  const envelope = value as Record<string, unknown>;
  if (
    typeof envelope.type !== "string" ||
    !EVENT_TYPE_PATTERN.test(envelope.type) ||
    envelope.type.length > 80
  ) {
    return { kind: "malformed", eventType: null };
  }
  const eventType = envelope.type;
  if (!isSupportedEventType(eventType)) {
    return { kind: "unsupported", eventType };
  }

  if (
    typeof envelope.created_at !== "string" ||
    !ISO_TIMESTAMP_PATTERN.test(envelope.created_at) ||
    !Number.isFinite(Date.parse(envelope.created_at)) ||
    typeof envelope.data !== "object" ||
    envelope.data === null ||
    Array.isArray(envelope.data)
  ) {
    return { kind: "malformed", eventType };
  }

  const data = envelope.data as Record<string, unknown>;
  if (
    typeof data.email_id !== "string" ||
    !PROVIDER_MESSAGE_ID_PATTERN.test(data.email_id)
  ) {
    return { kind: "malformed", eventType };
  }

  let sourceTag: string | null = null;
  let messageIdTag: string | null = null;
  if (data.tags !== undefined && data.tags !== null) {
    if (
      typeof data.tags !== "object" ||
      Array.isArray(data.tags)
    ) {
      return { kind: "malformed", eventType };
    }
    const tags = data.tags as Record<string, unknown>;
    const parsedSourceTag = readTag(tags, "tcw_source");
    const parsedMessageIdTag = readTag(tags, "tcw_message_id");
    if (parsedSourceTag === undefined || parsedMessageIdTag === undefined) {
      return { kind: "malformed", eventType };
    }
    sourceTag = parsedSourceTag;
    messageIdTag = parsedMessageIdTag;
  }

  return {
    kind: "supported",
    eventType,
    occurredAt: envelope.created_at,
    providerMessageId: data.email_id,
    sourceTag,
    messageIdTag,
  };
}

async function readRawBody(
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
      throw new WebhookBodyTooLargeError();
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

function readTag(
  tags: Record<string, unknown>,
  name: string,
): string | null | undefined {
  const value = tags[name];
  if (value === undefined || value === null) {
    return null;
  }
  if (typeof value !== "string" || !TAG_VALUE_PATTERN.test(value)) {
    return undefined;
  }
  return value;
}

function isSupportedEventType(
  value: string,
): value is SupportedResendEventType {
  return (SUPPORTED_RESEND_EVENT_TYPES as readonly string[]).includes(value);
}

function normalizeRpcAggregate(value: unknown): {
  outcome: string;
  storedEventCount: number;
  preferenceDisabledCount: number;
} | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const aggregate = value as Record<string, unknown>;
  if (
    typeof aggregate.outcome !== "string" ||
    ![
      "recorded",
      "duplicate",
      "ignored_unmatched",
      "correlation_conflict",
    ].includes(aggregate.outcome) ||
    !isAggregateCount(aggregate.stored_event_count) ||
    !isAggregateCount(aggregate.preference_disabled_count)
  ) {
    return null;
  }
  return {
    outcome: aggregate.outcome,
    storedEventCount: aggregate.stored_event_count,
    preferenceDisabledCount: aggregate.preference_disabled_count,
  };
}

function isAggregateCount(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}
