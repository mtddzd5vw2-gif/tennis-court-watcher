import { createClient } from "npm:@supabase/supabase-js@2.95.0";
import { createDeleteAccountHandler, DeleteAccountOutcome } from "./helpers.ts";

async function deleteAuthenticatedAccount(
  supabaseUrl: string,
  serviceRoleKey: string,
  accessToken: string,
): Promise<DeleteAccountOutcome> {
  try {
    const admin = createClient(
      supabaseUrl,
      serviceRoleKey,
      {
        auth: {
          autoRefreshToken: false,
          persistSession: false,
          detectSessionInUrl: false,
        },
      },
    );

    const userResult = await admin.auth.getUser(accessToken);

    if (
      userResult.error ||
      !userResult.data.user
    ) {
      return "unauthorized";
    }

    const userId = userResult.data.user.id;

    const lockResult = await admin
      .from("profiles")
      .update({
        membership_status: "withdrawal_pending",
      })
      .eq("id", userId)
      .select("id")
      .maybeSingle();

    if (
      lockResult.error ||
      !lockResult.data
    ) {
      return "profile_lock_failed";
    }

    const deleteResult = await admin.auth.admin.deleteUser(userId);

    if (deleteResult.error) {
      return "delete_failed";
    }

    return "deleted";
  } catch {
    return "internal_error";
  }
}

const handler = createDeleteAccountHandler({
  getEnv: (name) => Deno.env.get(name),
  deleteAccount: deleteAuthenticatedAccount,
});

Deno.serve(handler);
