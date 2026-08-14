export interface EmailNotificationItem {
  facility_name: string;
  available_date: string;
  start_time: string;
  end_time: string;
  payload: {
    court_name?: string;
    reservation_url?: string;
  };
}

export interface RenderedEmail {
  subject: string;
  text: string;
  html: string;
}

export interface ResendFailure {
  retryable: boolean;
  errorCode:
    | "resend_server_error"
    | "resend_rate_limited"
    | "resend_concurrent_request"
    | "resend_network_error"
    | "resend_invalid_idempotency_key"
    | "resend_invalid_idempotent_request"
    | "resend_invalid_api_key"
    | "resend_invalid_from"
    | "resend_validation_error"
    | "resend_quota_exceeded"
    | "resend_security_error"
    | "resend_client_error";
}

export interface ResendEmailPayload {
  from: string;
  to: string[];
  subject: string;
  text: string;
  html: string;
  headers: {
    "List-Unsubscribe": string;
    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click";
  };
  tags: ResendEmailTag[];
}

export interface ResendEmailTag {
  name: "tcw_source" | "tcw_message_id";
  value: string;
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const PRODUCTION_UNSUBSCRIBE_PUBLIC_ORIGIN =
  "https://unsubscribe.tenniscourtwatcher.com";

export function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function validHttpUrl(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }

  const candidate = value.trim();
  if (candidate.length === 0) {
    return null;
  }

  try {
    const parsed = new URL(candidate);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return null;
    }
    return candidate;
  } catch {
    return null;
  }
}

export function deterministicIdempotencyKey(messageId: string): string {
  if (!UUID_PATTERN.test(messageId)) {
    throw new Error("Invalid message identifier.");
  }
  return `tennis-court-watcher/email/${messageId.toLowerCase()}`;
}

export function buildUnsubscribeUrl(
  publicBaseUrl: string,
  token: string,
): string {
  if (!UUID_PATTERN.test(token)) {
    throw new Error("Invalid unsubscribe token.");
  }

  let url: URL;
  try {
    url = new URL(publicBaseUrl);
  } catch {
    throw new Error("Invalid public unsubscribe base URL.");
  }
  const productionOrigin = publicBaseUrl ===
      PRODUCTION_UNSUBSCRIBE_PUBLIC_ORIGIN ||
    publicBaseUrl === `${PRODUCTION_UNSUBSCRIBE_PUBLIC_ORIGIN}/`;
  const localOrigin = (url.protocol === "http:" || url.protocol === "https:") &&
    (url.hostname === "localhost" || url.hostname === "127.0.0.1") &&
    url.username.length === 0 &&
    url.password.length === 0;
  if (
    (!productionOrigin && !localOrigin) ||
    url.username.length !== 0 ||
    url.password.length !== 0 ||
    (url.pathname !== "" && url.pathname !== "/") ||
    url.search.length !== 0 ||
    url.hash.length !== 0
  ) {
    throw new Error("Invalid public unsubscribe base URL.");
  }
  url.pathname = `/u/${token.toLowerCase()}`;
  return url.toString();
}

export function classifyResendError(
  status: number | null,
  providerCode: unknown,
): ResendFailure {
  const code = typeof providerCode === "string" ? providerCode : "";

  if (status === null) {
    return { retryable: true, errorCode: "resend_network_error" };
  }
  if (status >= 500 && status <= 599) {
    return { retryable: true, errorCode: "resend_server_error" };
  }
  if (status === 408) {
    return { retryable: true, errorCode: "resend_network_error" };
  }
  if (
    code === "daily_quota_exceeded" ||
    code === "monthly_quota_exceeded"
  ) {
    return { retryable: false, errorCode: "resend_quota_exceeded" };
  }
  if (status === 429 || code === "rate_limit_exceeded") {
    return { retryable: true, errorCode: "resend_rate_limited" };
  }
  if (code === "concurrent_idempotent_requests") {
    return {
      retryable: true,
      errorCode: "resend_concurrent_request",
    };
  }
  if (code === "invalid_idempotent_request") {
    return {
      retryable: false,
      errorCode: "resend_invalid_idempotent_request",
    };
  }
  if (code === "invalid_idempotency_key") {
    return {
      retryable: false,
      errorCode: "resend_invalid_idempotency_key",
    };
  }
  if (
    code === "missing_api_key" ||
    code === "restricted_api_key" ||
    code === "invalid_api_key"
  ) {
    return { retryable: false, errorCode: "resend_invalid_api_key" };
  }
  if (code === "invalid_from_address") {
    return { retryable: false, errorCode: "resend_invalid_from" };
  }
  if (code === "security_error") {
    return { retryable: false, errorCode: "resend_security_error" };
  }
  if (
    code === "validation_error" ||
    code === "invalid_attachment" ||
    code === "invalid_access" ||
    code === "invalid_parameter" ||
    code === "invalid_region" ||
    code === "missing_required_field"
  ) {
    return { retryable: false, errorCode: "resend_validation_error" };
  }
  if ((status === 401 || status === 403) && code.length === 0) {
    return { retryable: false, errorCode: "resend_invalid_api_key" };
  }
  return { retryable: false, errorCode: "resend_client_error" };
}

export function extractResendErrorCode(value: unknown): string | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }

  const candidate = value as Record<string, unknown>;
  for (const key of ["name", "code", "error"]) {
    if (typeof candidate[key] === "string") {
      return candidate[key] as string;
    }
  }
  return null;
}

export function extractResendMessageId(value: unknown): string | null {
  if (typeof value !== "object" || value === null) {
    return null;
  }
  const id = (value as Record<string, unknown>).id;
  if (
    typeof id !== "string" ||
    !/^[A-Za-z0-9_-]{1,255}$/.test(id)
  ) {
    return null;
  }
  return id;
}

export function renderEmail(
  items: EmailNotificationItem[],
  unsubscribeUrl: string,
): RenderedEmail {
  if (items.length === 0) {
    throw new Error("At least one notification item is required.");
  }
  const safeUnsubscribeUrl = validUnsubscribeUrl(unsubscribeUrl);
  if (safeUnsubscribeUrl === null) {
    throw new Error("Invalid unsubscribe URL.");
  }

  const subject =
    `【テニスコート空き通知】予約可能な枠が見つかりました（${items.length}件）`;
  const textParts = [
    "テニスコートの予約可能な枠が見つかりました。",
    "",
  ];
  const htmlParts = [
    "<!doctype html>",
    '<html lang="ja">',
    "<body>",
    "<h1>テニスコートの空き通知</h1>",
    "<p>予約可能な枠が見つかりました。</p>",
    "<ol>",
  ];

  for (const item of items) {
    assertNotificationItem(item);
    const facilityName = normalizeDisplayText(item.facility_name);
    const courtName = item.payload.court_name === undefined
      ? null
      : normalizeDisplayText(item.payload.court_name);
    const date = formatDate(item.available_date);
    const time = `${formatTime(item.start_time)}〜${formatTime(item.end_time)}`;
    const reservationUrl = validHttpUrl(item.payload.reservation_url);

    textParts.push(`・${facilityName}`);
    textParts.push(`  ${date} ${time}`);
    if (courtName !== null) {
      textParts.push(`  コート: ${courtName}`);
    }
    if (reservationUrl !== null) {
      textParts.push(`  予約ページ: ${reservationUrl}`);
    }
    textParts.push("");

    htmlParts.push("<li>");
    htmlParts.push(`<strong>${escapeHtml(facilityName)}</strong><br>`);
    htmlParts.push(`${escapeHtml(date)} ${escapeHtml(time)}`);
    if (courtName !== null) {
      htmlParts.push(`<br>コート: ${escapeHtml(courtName)}`);
    }
    if (reservationUrl !== null) {
      const safeUrl = escapeHtml(reservationUrl);
      htmlParts.push(
        `<br><a href="${safeUrl}" rel="noopener noreferrer">予約ページを開く</a>`,
      );
    }
    htmlParts.push("</li>");
  }

  textParts.push(
    "空き状況は変わることがあります。予約ページで最新状況をご確認ください。",
    "",
    `メール通知を停止する: ${safeUnsubscribeUrl}`,
  );
  const escapedUnsubscribeUrl = escapeHtml(safeUnsubscribeUrl);
  htmlParts.push(
    "</ol>",
    "<p>空き状況は変わることがあります。予約ページで最新状況をご確認ください。</p>",
    "<hr>",
    `<p><a href="${escapedUnsubscribeUrl}" rel="noopener noreferrer">メール通知を停止する</a></p>`,
    "</body>",
    "</html>",
  );

  return {
    subject,
    text: textParts.join("\n"),
    html: htmlParts.join(""),
  };
}

export function buildResendPayload(
  from: string,
  recipient: string,
  rendered: RenderedEmail,
  messageId: string,
  unsubscribeUrl: string,
): ResendEmailPayload {
  if (!UUID_PATTERN.test(messageId)) {
    throw new Error("Invalid message identifier.");
  }
  const safeUnsubscribeUrl = validUnsubscribeUrl(unsubscribeUrl);
  if (safeUnsubscribeUrl === null) {
    throw new Error("Invalid unsubscribe URL.");
  }

  return {
    from,
    to: [recipient],
    subject: rendered.subject,
    text: rendered.text,
    html: rendered.html,
    headers: {
      "List-Unsubscribe": `<${safeUnsubscribeUrl}>`,
      "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    },
    tags: [
      { name: "tcw_source", value: "user_notification" },
      { name: "tcw_message_id", value: messageId.toLowerCase() },
    ],
  };
}

export async function hmacPayloadFingerprint(
  serializedPayload: string,
  secret: string,
): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    encoder.encode(serializedPayload),
  );
  return Array.from(new Uint8Array(signature))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function assertNotificationItem(
  item: EmailNotificationItem,
): asserts item is EmailNotificationItem {
  if (
    typeof item !== "object" ||
    item === null ||
    typeof item.facility_name !== "string" ||
    typeof item.available_date !== "string" ||
    typeof item.start_time !== "string" ||
    typeof item.end_time !== "string" ||
    typeof item.payload !== "object" ||
    item.payload === null ||
    (
      item.payload.court_name !== undefined &&
      typeof item.payload.court_name !== "string"
    ) ||
    (
      item.payload.reservation_url !== undefined &&
      typeof item.payload.reservation_url !== "string"
    )
  ) {
    throw new Error("Invalid notification item.");
  }
}

function validUnsubscribeUrl(value: string): string | null {
  const candidate = validHttpUrl(value);
  if (candidate === null) {
    return null;
  }
  return validUnsubscribeProtocol(new URL(candidate)) ? candidate : null;
}

function validUnsubscribeProtocol(url: URL): boolean {
  return url.protocol === "https:" ||
    (url.protocol === "http:" &&
      (url.hostname === "localhost" || url.hostname === "127.0.0.1"));
}

function normalizeDisplayText(value: string): string {
  return value.replace(/\s+/gu, " ").trim();
}

function formatDate(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (match === null) {
    throw new Error("Invalid notification date.");
  }
  return `${Number(match[1])}年${Number(match[2])}月${Number(match[3])}日`;
}

function formatTime(value: string): string {
  const match = /^([0-2]\d):([0-5]\d)(?::[0-5]\d)?$/.exec(value);
  if (match === null) {
    throw new Error("Invalid notification time.");
  }
  return `${match[1]}:${match[2]}`;
}
