import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// FastAPI (uvicorn) dev origin the proxy forwards to.
const API_TARGET = process.env.VITE_API_TARGET ?? "http://localhost:8000";

// In dev the SPA runs on its own port (5173) so it never collides with the live HTMX
// app served at FastAPI's "/" (ADR 0010). Vite proxies the JSON API to FastAPI, so no
// CORS is needed. The SPA's binary assets (page images, overlay PNGs) are re-mounted
// under /api/** for the SPA (spec §A.0, §A.1 #4/#5/#17) as each slice lands, so this one
// /api rule covers them too. The legacy non-/api binary routes belong to the HTMX app
// and are deliberately not proxied — those prefixes (/results, /drawings) collide with
// client-side routes and must fall through to index.html.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: true,
  },
});
