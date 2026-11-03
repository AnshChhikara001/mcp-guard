import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In development one origin serves everything, so the browser needs no CORS:
// /gateway/* goes to the Gateway (make gateway) and /agent/* to the Agent API (make agent-api).
// Production routing is decided at deploy time (#34, #35).
const backend = (port: number, prefix: string) => ({
  target: `http://127.0.0.1:${port}`,
  rewrite: (path: string) => path.slice(prefix.length),
});

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/gateway": backend(8000, "/gateway"),
      "/agent": backend(8001, "/agent"),
    },
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
