// Built into the folder the kernel's HTTP server serves; assets under /static/.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "/static/",
  build: { outDir: "../cadcore/service/web_ui", emptyOutDir: true, sourcemap: false },
  server: { proxy: { "/op": "http://127.0.0.1:8080", "/examples": "http://127.0.0.1:8080",
                     "/state": "http://127.0.0.1:8080", "/ask": "http://127.0.0.1:8080" } },
});
