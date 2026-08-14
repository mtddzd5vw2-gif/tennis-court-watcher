import { createClient } from "npm:@supabase/supabase-js@2.95.0";
import {
  createEmailUnsubscribeHandler,
  UnsubscribeRpcArgs,
} from "./helpers.ts";

async function callRpc(
  supabaseUrl: string,
  serviceRoleKey: string,
  functionName:
    | "email_unsubscribe_token_is_valid"
    | "unsubscribe_email_notifications_by_token",
  args: UnsubscribeRpcArgs,
) {
  const supabase = createClient(supabaseUrl, serviceRoleKey, {
    auth: {
      autoRefreshToken: false,
      persistSession: false,
      detectSessionInUrl: false,
    },
  });
  const result = await supabase.rpc(functionName, args);
  return { data: result.data, error: result.error };
}

const handler = createEmailUnsubscribeHandler({
  getEnv: (name) => Deno.env.get(name),
  tokenExists: (supabaseUrl, serviceRoleKey, args) =>
    callRpc(
      supabaseUrl,
      serviceRoleKey,
      "email_unsubscribe_token_is_valid",
      args,
    ),
  unsubscribe: (supabaseUrl, serviceRoleKey, args) =>
    callRpc(
      supabaseUrl,
      serviceRoleKey,
      "unsubscribe_email_notifications_by_token",
      args,
    ),
});

Deno.serve(handler);
