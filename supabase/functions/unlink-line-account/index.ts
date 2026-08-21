import { createClient } from "npm:@supabase/supabase-js@2.95.0";
import { createUnlinkLineAccountHandler } from "./helpers.ts";
import type { UnlinkLineAccountOutcome } from "./helpers.ts";

async function unlinkAccount(input: {
  supabaseUrl: string;
  serviceRoleKey: string;
  accessToken: string;
}): Promise<UnlinkLineAccountOutcome> {
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

    const unlinkResult = await admin.rpc("unlink_line_account", {
      p_user_id: userResult.data.user.id,
    });
    if (unlinkResult.error) {
      return "internal_error";
    }
    if (
      unlinkResult.data === "unlinked" ||
      unlinkResult.data === "not_linked" ||
      unlinkResult.data === "inactive_member"
    ) {
      return unlinkResult.data;
    }
    return "internal_error";
  } catch {
    return "internal_error";
  }
}

const handler = createUnlinkLineAccountHandler({
  getEnv: (name) => Deno.env.get(name),
  unlinkAccount,
});

Deno.serve(handler);
