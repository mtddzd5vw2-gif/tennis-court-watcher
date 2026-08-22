import { createClient } from "npm:@supabase/supabase-js@2.95.0";
import { createLineWebhookHandler } from "./helpers.ts";

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
});

Deno.serve(handler);
