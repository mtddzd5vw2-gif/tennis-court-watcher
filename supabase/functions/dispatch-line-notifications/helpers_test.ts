import assert from "node:assert/strict";
import { test } from "node:test";
import {
  buildLinePushPayload,
  classifyLineError,
  deterministicLineRetryKey,
  extractQuotaConsumption,
  hmacPayloadFingerprint,
  LINE_CANARY_TEST_TEXT,
  normalizeLineRequestId,
  readLineRolloutControls,
  renderLineMessage,
} from "./helpers.ts";

const MESSAGE_ID = "123e4567-e89b-42d3-a456-426614174000";
const LINE_USER_ID = `U${"b".repeat(32)}`;

test("LINE rollout controls permit exactly one live scope", () => {
  assert.deepEqual(
    readLineRolloutControls(MESSAGE_ID, "false", "false"),
    { canaryUserId: MESSAGE_ID, useAllowlist: false, allowAll: false },
  );
  assert.deepEqual(
    readLineRolloutControls(undefined, "true", "false"),
    { canaryUserId: null, useAllowlist: true, allowAll: false },
  );
  assert.deepEqual(
    readLineRolloutControls(undefined, "false", "true"),
    { canaryUserId: null, useAllowlist: false, allowAll: true },
  );

  assert.equal(
    readLineRolloutControls(undefined, "false", "false"),
    null,
  );
  assert.equal(
    readLineRolloutControls(MESSAGE_ID, "true", "false"),
    null,
  );
  assert.equal(
    readLineRolloutControls("not-a-uuid", "true", "false"),
    null,
  );
  assert.equal(
    readLineRolloutControls(undefined, "TRUE", "false"),
    null,
  );
});

test("LINE message rendering is deterministic and omits unsafe links", () => {
  const item = {
    facility_name: "鴨池県営テニスコート",
    available_date: "2026-08-22",
    start_time: "09:00:00",
    end_time: "11:00:00",
    payload: {
      court_name: "Aコート",
      reservation_url: "javascript:alert(1)",
    },
  };
  const first = renderLineMessage([item]);
  const second = renderLineMessage([item]);
  assert.equal(first, second);
  assert.match(first, /2026年8月22日 09:00〜11:00/);
  assert.match(first, /Aコート/);
  assert.doesNotMatch(first, /javascript:/);
  assert.ok(Array.from(first).length <= 4800);
});

test("large batches are aggregated into one bounded user message", () => {
  const items = Array.from({ length: 100 }, (_, index) => ({
    facility_name: `施設${index}`,
    available_date: "2026-08-22",
    start_time: "09:00:00",
    end_time: "11:00:00",
    payload: {
      court_name: `コート${index}`,
      reservation_url: `https://example.jp/reserve/${index}`,
    },
  }));
  const rendered = renderLineMessage(items);
  assert.match(rendered, /ほか\d+件/);
  assert.ok(Array.from(rendered).length <= 4800);
});

test("push payload and retry key are recipient and message bound", async () => {
  const text = "空き通知";
  const payload = buildLinePushPayload(LINE_USER_ID, text);
  assert.deepEqual(payload, {
    to: LINE_USER_ID,
    messages: [{ type: "text", text }],
  });
  assert.equal(deterministicLineRetryKey(MESSAGE_ID.toUpperCase()), MESSAGE_ID);
  assert.throws(() => deterministicLineRetryKey("not-a-uuid"));
  const serialized = JSON.stringify(payload);
  const fingerprint = await hmacPayloadFingerprint(serialized, "a".repeat(32));
  const changed = await hmacPayloadFingerprint(
    JSON.stringify({ ...payload, to: `U${"c".repeat(32)}` }),
    "a".repeat(32),
  );
  assert.match(fingerprint, /^[0-9a-f]{64}$/);
  assert.notEqual(fingerprint, changed);
});

test("canary text is fixed, explicit, and valid for the normal push payload", () => {
  assert.equal(
    LINE_CANARY_TEST_TEXT,
    "【テスト通知】鹿児島テニス空き情報 LINE通知の動作確認です。",
  );
  assert.deepEqual(buildLinePushPayload(LINE_USER_ID, LINE_CANARY_TEST_TEXT), {
    to: LINE_USER_ID,
    messages: [{ type: "text", text: LINE_CANARY_TEST_TEXT }],
  });
});

test("provider status classification follows LINE retry guidance", () => {
  assert.deepEqual(classifyLineError(null), {
    retryable: true,
    errorCode: "line_network_error",
  });
  assert.equal(classifyLineError(503).retryable, true);
  assert.deepEqual(classifyLineError(429), {
    retryable: false,
    errorCode: "line_quota_exceeded",
  });
  assert.equal(classifyLineError(408).retryable, false);
  assert.deepEqual(classifyLineError(401), {
    retryable: false,
    errorCode: "line_invalid_access_token",
  });
  assert.equal(classifyLineError(400).retryable, false);
});

test("quota and request identifiers are strictly normalized", () => {
  assert.equal(extractQuotaConsumption({ totalUsage: 179 }), 179);
  assert.equal(extractQuotaConsumption({ totalUsage: -1 }), null);
  assert.equal(extractQuotaConsumption({ totalUsage: 1, extra: true }), null);
  assert.equal(
    normalizeLineRequestId("123e4567-e89b-42d3-a456-426614174000"),
    "line:request:123e4567-e89b-42d3-a456-426614174000",
  );
  assert.equal(normalizeLineRequestId("bad"), null);
});
