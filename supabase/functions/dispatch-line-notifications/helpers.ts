export interface LineNotificationItem {
  facility_name: string;
  available_date: string;
  start_time: string;
  end_time: string;
  payload: {
    court_name?: string;
    reservation_url?: string;
  };
}

export interface LinePushPayload {
  to: string;
  messages: [{ type: "text"; text: string }];
}

export interface LineFailure {
  retryable: boolean;
  errorCode:
    | "line_network_error"
    | "line_server_error"
    | "line_quota_exceeded"
    | "line_invalid_access_token"
    | "line_invalid_recipient_or_payload"
    | "line_client_error";
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const LINE_USER_ID_PATTERN = /^U[0-9a-f]{32}$/;
const LINE_REQUEST_ID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_LINE_TEXT_CHARACTERS = 4800;
const SETTINGS_URL =
  "https://tenniscourtwatcher.com/account/index.html#line-link-title";
export const LINE_CANARY_TEST_TEXT =
  "【テスト通知】鹿児島テニス空き情報 LINE通知の動作確認です。";

export function validHttpUrl(value: unknown): string | null {
  if (typeof value !== "string" || value.trim().length === 0) {
    return null;
  }
  try {
    const parsed = new URL(value.trim());
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return null;
    }
    return parsed.toString();
  } catch {
    return null;
  }
}

export function renderLineMessage(items: LineNotificationItem[]): string {
  if (items.length === 0) {
    throw new Error("At least one LINE notification item is required.");
  }
  const header = [
    "【テニスコート空き通知】",
    "予約できる可能性がある枠を見つけました。",
    "",
  ];
  const footer = [
    "",
    "空き状況は変わることがあります。予約ページで最新状況をご確認ください。",
    `LINE連携設定: ${SETTINGS_URL}`,
  ];
  const lines = [...header];
  let included = 0;

  for (const item of items) {
    assertNotificationItem(item);
    const itemLines = renderItem(item, included + 1);
    const omittedAfterInsert = items.length - (included + 1);
    const candidate = [
      ...lines,
      ...itemLines,
      ...(omittedAfterInsert > 0 ? [`ほか${omittedAfterInsert}件`] : []),
      ...footer,
    ].join("\n");
    if (characterCount(candidate) > MAX_LINE_TEXT_CHARACTERS) {
      break;
    }
    lines.push(...itemLines);
    included += 1;
  }

  if (included === 0) {
    throw new Error("LINE notification item is too large.");
  }
  const omitted = items.length - included;
  if (omitted > 0) {
    lines.push(`ほか${omitted}件`);
  }
  lines.push(...footer);
  const message = lines.join("\n");
  if (characterCount(message) > MAX_LINE_TEXT_CHARACTERS) {
    throw new Error("LINE notification text exceeds the safe limit.");
  }
  return message;
}

export function buildLinePushPayload(
  lineUserId: string,
  text: string,
): LinePushPayload {
  if (!LINE_USER_ID_PATTERN.test(lineUserId)) {
    throw new Error("Invalid LINE user identifier.");
  }
  if (
    text.trim().length === 0 ||
    characterCount(text) > MAX_LINE_TEXT_CHARACTERS
  ) {
    throw new Error("Invalid LINE message text.");
  }
  return {
    to: lineUserId,
    messages: [{ type: "text", text }],
  };
}

export function deterministicLineRetryKey(messageId: string): string {
  if (!UUID_PATTERN.test(messageId)) {
    throw new Error("Invalid message identifier.");
  }
  return messageId.toLowerCase();
}

export function normalizeLineRequestId(
  value: string | null,
): string | null {
  if (value === null || !LINE_REQUEST_ID_PATTERN.test(value)) {
    return null;
  }
  return `line:request:${value.toLowerCase()}`;
}

export function classifyLineError(status: number | null): LineFailure {
  if (status === null) {
    return { retryable: true, errorCode: "line_network_error" };
  }
  if (status >= 500 && status <= 599) {
    return { retryable: true, errorCode: "line_server_error" };
  }
  if (status === 429) {
    return { retryable: false, errorCode: "line_quota_exceeded" };
  }
  if (status === 401 || status === 403) {
    return { retryable: false, errorCode: "line_invalid_access_token" };
  }
  if (status === 400) {
    return {
      retryable: false,
      errorCode: "line_invalid_recipient_or_payload",
    };
  }
  return { retryable: false, errorCode: "line_client_error" };
}

export function extractQuotaConsumption(value: unknown): number | null {
  if (
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value) ||
    Object.keys(value).length !== 1
  ) {
    return null;
  }
  const totalUsage = (value as Record<string, unknown>).totalUsage;
  if (
    typeof totalUsage !== "number" ||
    !Number.isSafeInteger(totalUsage) ||
    totalUsage < 0
  ) {
    return null;
  }
  return totalUsage;
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

function renderItem(item: LineNotificationItem, number: number): string[] {
  const facility = normalizeDisplayText(item.facility_name);
  const court = item.payload.court_name === undefined
    ? null
    : normalizeDisplayText(item.payload.court_name);
  const reservationUrl = validHttpUrl(item.payload.reservation_url);
  const lines = [
    `${number}. ${facility}`,
    `${formatDate(item.available_date)} ${formatTime(item.start_time)}〜${formatTime(item.end_time)}`,
  ];
  if (court !== null) {
    lines.push(`コート: ${court}`);
  }
  if (reservationUrl !== null) {
    lines.push(`予約ページ: ${reservationUrl}`);
  }
  lines.push("");
  return lines;
}

function assertNotificationItem(
  item: LineNotificationItem,
): asserts item is LineNotificationItem {
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
    throw new Error("Invalid LINE notification item.");
  }
}

function normalizeDisplayText(value: string): string {
  const normalized = value.replace(/[\p{Cc}\p{Cf}]+/gu, " ")
    .replace(/\s+/gu, " ")
    .trim();
  if (normalized.length === 0) {
    throw new Error("Invalid LINE notification display text.");
  }
  return normalized;
}

function formatDate(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (match === null) {
    throw new Error("Invalid LINE notification date.");
  }
  return `${Number(match[1])}年${Number(match[2])}月${Number(match[3])}日`;
}

function formatTime(value: string): string {
  const match = /^([0-2]\d):([0-5]\d)(?::[0-5]\d)?$/.exec(value);
  if (match === null) {
    throw new Error("Invalid LINE notification time.");
  }
  return `${match[1]}:${match[2]}`;
}

function characterCount(value: string): number {
  return Array.from(value).length;
}
