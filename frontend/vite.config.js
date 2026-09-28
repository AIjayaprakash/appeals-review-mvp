import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// MVP1 reviewer UI has no auth (CLAUDE.md) -- local dev only, proxied straight
// to the FastAPI app so the browser never needs to know the API port. 8001, not
// 8000 -- ChromaDB's own container already claims 8000 on the host (see
// docker-compose.yml), so uvicorn must run on a different port.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/cases": "http://localhost:8001",
      "/intake": "http://localhost:8001",
      "/health": "http://localhost:8001",
    },
  },
});
