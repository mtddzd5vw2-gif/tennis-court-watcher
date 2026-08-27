import { createClient } from "npm:@supabase/supabase-js@2.95.0";
import { createLineWebhookHandler } from "./helpers.ts";

const LEGACY_WEBHOOK_TIMEOUT_MS = 8_000;
const MAX_LEGACY_RESPONSE_BYTES = 16 * 1024;
const LEGACY_WEBHOOK_USER_AGENT =
  "tennis-court-watcher-line-webhook-bridge/1.0";

const handler = createLineWebhookHandler({
  getEnv: (name) => Deno.env.get(name),
  recordEvents: async (supabaseUrl, serviceRoleKey, events) => {
    const supabase = createClient(supabaseUrl, serviceRoleKey, {
      auth: {
        autoRefreshToken: false,
        persistSession: false,
        detectSessionInUrl: false,
      },
    });
    const result = await supabase.rpc("record_line_webhook_events", {
      p_events: events,
    });
    return { data: result.data, error: result.error };
  },
  forwardLegacyWebhook: async (url, rawBody, signature) => {
    const forwardedBody = new Uint8Array(rawBody.byteLength);
    forwardedBody.set(rawBody);
    let response: Response;
    try {
      response = await fetch(url, {
        method: "POST",
        headers: {
          "content-type": "application/json; charset=utf-8",
          "user-agent": LEGACY_WEBHOOK_USER_AGENT,
          "x-line-signature": signature,
        },
        body: forwardedBody.buffer,
        redirect: "follow",
        signal: AbortSignal.timeout(LEGACY_WEBHOOK_TIMEOUT_MS),
      });
    } catch {
      return false;
    }
    await consumeBoundedBody(response);
    if (!response.ok) {
      return false;
    }
    try {
      const responseUrl = new URL(response.url);
      return responseUrl.protocol === "https:" &&
        (responseUrl.hostname === "script.google.com" ||
          responseUrl.hostname.endsWith(".googleusercontent.com"));
    } catch {
      return false;
    }
  },
});

Deno.serve(handler);

async function consumeBoundedBody(response: Response): Promise<void> {
  if (response.body === null) {
    return;
  }
  const reader = response.body.getReader();
  let total = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) {
        return;
      }
      total += chunk.value.byteLength;
      if (total > MAX_LEGACY_RESPONSE_BYTES) {
        await reader.cancel();
        return;
      }
    }
  } catch {
    try {
      await reader.cancel();
    } catch {
      // Ignore response-body cleanup errors.
    }
  }
}
