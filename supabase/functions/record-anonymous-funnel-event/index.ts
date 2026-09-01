import { createClient } from "npm:@supabase/supabase-js@2.95.0";
import {
  AnonymousFunnelEvent,
  createAnonymousFunnelHandler,
} from "./helpers.ts";

const handler = createAnonymousFunnelHandler({
  getEnv: (name) => Deno.env.get(name),
  recordEvent: async (
    supabaseUrl: string,
    serviceRoleKey: string,
    eventName: AnonymousFunnelEvent,
  ) => {
    const supabase = createClient(supabaseUrl, serviceRoleKey, {
      auth: {
        autoRefreshToken: false,
        persistSession: false,
        detectSessionInUrl: false,
      },
    });
    const result = await supabase.rpc("record_anonymous_funnel_event", {
      p_event_name: eventName,
    });
    return { data: result.data, error: result.error };
  },
});

Deno.serve(handler);
