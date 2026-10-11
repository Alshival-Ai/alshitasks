import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  root: "desktop/renderer",
  base: "./",
  plugins: [react()],
  build: { outDir: "../../dist", emptyOutDir: true },
});
