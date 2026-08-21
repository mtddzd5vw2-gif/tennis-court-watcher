import { createClient } from "npm:@supabase/supabase-js@2.95.0";
import { createCompleteLineAccountLinkHandler } from "./helpers.ts";
import type { CompleteLineAccountLinkOutcome } from "./helpers.ts";
import { completeLineLogin } from "./line-api.ts";

const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const channelId = Deno.env.get("LINE_LOGIN_CHANNEL_ID") ?? "";
const channelSecret = Deno.env.get("LINE_LOGIN_CHANNEL_SECRET") ?? "";
const callbackUrl = Deno.env.get("LINE_LOGIN_CALLBACK_URL") ?? "";

async function persistLink(input: {
  stateHash: string;
  nonceHash: string;
  lineUserId: string;
  isFriend: boolean;
}): Promise<CompleteLineAccountLinkOutcome> {
  try {
    const admin = createClient(supabaseUrl, serviceRoleKey, {
      auth: {
        autoRefreshToken: false,
        persistSession: false,
        detectSessionInUrl: false,
      },
    });
    const result = await admin.rpc("complete_line_account_link", {
      p_state_hash: input.stateHash,
      p_nonce_hash: input.nonceHash,
      p_line_user_id: input.lineUserId,
      p_is_friend: input.isFriend,
    });
    if (result.error) {
      return "internal_error";
    }
    if (
      result.data === "linked" ||
      result.data === "friend_required" ||
      result.data === "invalid_session" ||
      result.data === "inactive_member" ||
      result.data === "member_conflict" ||
      result.data === "line_conflict" ||
      result.data === "invalid_request"
    ) {
      return result.data;
    }
    return "internal_error";
  } catch {
    return "internal_error";
  }
}

const handler = createCompleteLineAccountLinkHandler({
  getEnv: (name) => Deno.env.get(name),
  completeLink: async ({ code, state }) => {
    if (
      supabaseUrl.length === 0 ||
      serviceRoleKey.length === 0 ||
      !/^[0-9]{5,20}$/.test(channelId) ||
      channelSecret.length < 16 ||
      callbackUrl.length === 0
    ) {
      return "internal_error";
    }
    return await completeLineLogin(
      {
        code,
        state,
        configuration: {
          channelId,
          channelSecret,
          callbackUrl,
        },
      },
      {
        fetch,
        persistLink,
      },
    );
  },
});

Deno.serve(handler);
