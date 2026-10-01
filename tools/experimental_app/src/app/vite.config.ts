import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// FastAPI (uvicorn) dev origin the proxy forwards to.
const API_TARGET = process.env.VITE_API_TARGET ?? "http://localhost:8000";

// In dev the SPA runs on the Vite dev server (5173) and Vite proxies the JSON API to
// FastAPI, so no CORS is needed. All server data — including binary assets (page images,
// overlay PNGs) — lives under /api/** (spec §A.0, §A.1 #4/#5/#17), so this one /api rule
// covers everything the SPA fetches. Every other path is a client-side route and is served
// index.html by the dev server. In prod FastAPI serves the built SPA and the same /api
// surface directly (ADR-0010 contract step, ticket 08); there is no HTMX layer left.
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
