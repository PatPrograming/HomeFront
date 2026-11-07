import { createClient } from "@supabase/supabase-js";

// Only the public URL + anon key belong here. Never the service role key.
const url = import.meta.env.VITE_SUPABASE_URL;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

export const supabaseConfigured = Boolean(url && anonKey);

// createClient throws on empty args, so fall back to placeholders when unconfigured
// (the app still renders; login just shows a "not configured" message).
export const supabase = createClient(
  url || "https://placeholder.supabase.co",
  anonKey || "placeholder-anon-key"
);
