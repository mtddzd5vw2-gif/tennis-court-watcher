import { createClient } from "npm:@supabase/supabase-js@2.95.0";
import {
  createResendWebhookHandler,
  RecordResendEmailEventArgs,
} from "./helpers.ts";

const handler = createResendWebhookHandler({
  getEnv: (name) => Deno.env.get(name),
  recordEvent: async (
    supabaseUrl: string,
    serviceRoleKey: string,
    args: RecordResendEmailEventArgs,
  ) => {
    const supabase = createClient(supabaseUrl, serviceRoleKey, {
      auth: {
        autoRefreshToken: false,
        persistSession: false,
        detectSessionInUrl: false,
      },
    });
    const result = await supabase.rpc("record_resend_email_event", args);
    return { data: result.data, error: result.error };
  },
});

Deno.serve(handler);
