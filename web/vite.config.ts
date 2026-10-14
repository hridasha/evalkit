import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the page talks to a EvalKit started with `evalkit ui --no-open`.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5190,
    proxy: {
      "/api": "http://localhost:8800",
      "/ws": { target: "ws://localhost:8800", ws: true },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
