// Supabase Edge Function "door-accounts": admins create, delete and reset the
// passwords of dashboard accounts (logic and rules: handler.ts).
//
// Deploy with the platform's JWT check OFF: the handler checks the caller
// itself, by asking the database (door_me) with the caller's own token, and
// the platform check does not understand the new API keys.
//
//   supabase functions deploy door-accounts --no-verify-jwt
//
// Keys come from the environment Supabase gives every function: the new
// SUPABASE_SECRET_KEYS / SUPABASE_PUBLISHABLE_KEYS ("default" entry), else the
// legacy SUPABASE_SERVICE_ROLE_KEY / SUPABASE_ANON_KEY.
import { createClient } from "npm:@supabase/supabase-js@2";
import { type Backend, handle } from "./handler.ts";

function key(dictionary: string, legacy: string): string {
  try {
    const keys = JSON.parse(Deno.env.get(dictionary) || "{}");
    if (typeof keys.default === "string" && keys.default) return keys.default;
  } catch {
    // not set, or not JSON: fall back to the legacy key
  }
  return Deno.env.get(legacy) || "";
}

const URL = Deno.env.get("SUPABASE_URL") || "";
const SECRET = key("SUPABASE_SECRET_KEYS", "SUPABASE_SERVICE_ROLE_KEY");
const PUBLISHABLE = key("SUPABASE_PUBLISHABLE_KEYS", "SUPABASE_ANON_KEY");
const STATELESS = { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } };

function connect(authorization: string, apikey: string): Backend {
  const caller = createClient(URL, PUBLISHABLE || apikey, {
    ...STATELESS,
    global: { headers: { Authorization: authorization } },
  });
  const admin = createClient(URL, SECRET, STATELESS);
  return {
    rpc: async (fn, args) => {
      const { data, error } = await caller.rpc(fn, args);
      return { data, error };
    },
    createUser: async (email, password) => {
      const { data, error } = await admin.auth.admin.createUser({ email, password, email_confirm: true });
      return { data: data && data.user ? { id: data.user.id } : null, error };
    },
    deleteUser: async (id) => {
      const { error } = await admin.auth.admin.deleteUser(id);
      return { data: null, error };
    },
    setPassword: async (id, password) => {
      const { data, error } = await admin.auth.admin.updateUserById(id, { password });
      return { data: data && data.user ? { email: data.user.email || "" } : null, error };
    },
    audit: async (row) => {
      const { error } = await admin.from("door_audit").insert(row);
      return { data: null, error };
    },
  };
}

Deno.serve((req) => handle(req, connect));
