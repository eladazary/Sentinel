import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In local dev (`npm run dev`) the app calls the API at /api/* and Vite proxies
// that to the backend. In Docker the same /api/* paths are proxied by nginx.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
