import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // 3000 is in the backend's default ALLOWED_ORIGINS; the demo console keeps 5173.
    port: 3000,
    // Same-origin in dev: /api/v1/* is forwarded to FastAPI (scripts/run-gateway.sh).
    proxy: { "/api": "http://127.0.0.1:8080" },
  },
});
