import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");

export default defineConfig(({ command, mode }) => {
  // Single repo-root .env for Backend + Frontend; VITE_* variables reach the browser code.
  const env = loadEnv(mode, repoRoot, "VITE_");

  // Mock mode fakes login and the API. It must never ship: refuse any production build that has it on.
  if (command === "build" && env.VITE_MOCK_API === "1") {
    throw new Error("VITE_MOCK_API=1 is for the dev server only. Unset it to build for production.");
  }

  return {
    envDir: repoRoot,
    plugins: [react()],
    server: {
      port: 3000,
      strictPort: true,
      // The console imports packages/mandate-contracts (types, tool names, sample events).
      fs: { allow: [repoRoot] },
      // Same-origin in dev: /api/v1/* is forwarded to FastAPI (scripts/run-gateway.sh).
      proxy: { "/api": "http://127.0.0.1:8080" },
    },
    test: { environment: "node" },
  };
});
