import { createClient } from "npm:@supabase/supabase-js@2.95.0";
import { createStartLineAccountLinkHandler } from "./helpers.ts";
import type { StartLineAccountLinkOutcome } from "./helpers.ts";

async function createSession(input: {
  supabaseUrl: string;
  serviceRoleKey: string;
  accessToken: string;
  stateHash: string;
  nonceHash: string;
  expiresAt: string;
}): Promise<StartLineAccountLinkOutcome> {
  try {
    const admin = createClient(input.supabaseUrl, input.serviceRoleKey, {
      auth: {
        autoRefreshToken: false,
        persistSession: false,
        detectSessionInUrl: false,
      },
    });

    const userResult = await admin.auth.getUser(input.accessToken);
    if (userResult.error || !userResult.data.user) {
      return "unauthorized";
    }

    const sessionResult = await admin.rpc("create_line_link_session", {
      p_user_id: userResult.data.user.id,
      p_state_hash: input.stateHash,
      p_nonce_hash: input.nonceHash,
      p_expires_at: input.expiresAt,
    });

    if (sessionResult.error) {
      return "internal_error";
    }

    if (
      sessionResult.data === "created" ||
      sessionResult.data === "inactive_member" ||
      sessionResult.data === "retry"
    ) {
      return sessionResult.data;
    }

    return "internal_error";
  } catch {
    return "internal_error";
  }
}

const handler = createStartLineAccountLinkHandler({
  getEnv: (name) => Deno.env.get(name),
  createSession,
});

Deno.serve(handler);
