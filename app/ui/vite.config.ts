import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Built into app/ui/dist, which the companion API serves at / (python -m app.api).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "dist", emptyOutDir: true },
  server: { proxy: { "^/(health|home|applications|shortlist|tracker|assessments|board|notifications|push)": "http://127.0.0.1:8765" } },
});
