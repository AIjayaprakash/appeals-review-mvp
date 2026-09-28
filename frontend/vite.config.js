import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// MVP1 reviewer UI has no auth (CLAUDE.md) -- local dev only, proxied straight
// to the FastAPI app so the browser never needs to know the API port.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/cases": "http://localhost:8000",
      "/intake": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
});
