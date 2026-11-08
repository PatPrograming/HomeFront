import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");

export default defineConfig(({ mode }) => {
  // Single repo-root .env for Backend + Frontend.
  const env = loadEnv(mode, repoRoot, "");
  const userPoolId = env.VITE_COGNITO_USER_POOL_ID || env.COGNITO_USER_POOL_ID || "";
  const clientId = env.VITE_COGNITO_CLIENT_ID || env.COGNITO_APP_CLIENT_ID || "";

  return {
    envDir: repoRoot,
    plugins: [react()],
    define: {
      "import.meta.env.VITE_COGNITO_USER_POOL_ID": JSON.stringify(userPoolId),
      "import.meta.env.VITE_COGNITO_CLIENT_ID": JSON.stringify(clientId),
      "import.meta.env.VITE_API_BASE": JSON.stringify(env.VITE_API_BASE || ""),
    },
    server: {
      port: 3000,
      // Same-origin in dev: /api/v1/* is forwarded to FastAPI (scripts/run-gateway.sh).
      proxy: { "/api": "http://127.0.0.1:8080" },
    },
  };
});
