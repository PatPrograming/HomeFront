import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");

export default defineConfig(({ mode }) => {
  // Single repo-root .env for Backend + Frontend.
  const env = loadEnv(mode, repoRoot, "");
  const supabaseUrl = (env.VITE_SUPABASE_URL || env.SUPABASE_URL || "").replace(/\/$/, "");
  const supabaseAnon = env.VITE_SUPABASE_ANON_KEY || env.SUPABASE_ANON_KEY || "";

  return {
    envDir: repoRoot,
    plugins: [react()],
    define: {
      "import.meta.env.VITE_SUPABASE_URL": JSON.stringify(supabaseUrl),
      "import.meta.env.VITE_SUPABASE_ANON_KEY": JSON.stringify(supabaseAnon),
      "import.meta.env.VITE_API_BASE": JSON.stringify(env.VITE_API_BASE || ""),
    },
    server: {
      port: 3000,
      // Same-origin in dev: /api/v1/* is forwarded to FastAPI (scripts/run-gateway.sh).
      proxy: { "/api": "http://127.0.0.1:8080" },
    },
  };
});
