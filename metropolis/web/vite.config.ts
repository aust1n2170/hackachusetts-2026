import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The browser calls /api/...; Vite forwards it to the FastAPI service.
const apiTarget = process.env.API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: apiTarget, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
});
