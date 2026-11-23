import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Relative paths: the built site works from any folder or host (Vercel, the Pi, a USB stick).
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    outDir: "dist",
    target: "es2022",
    rollupOptions: {
      output: {
        // Libraries in their own files: they stay cached when only the app changes.
        manualChunks: { react: ["react", "react-dom"], supabase: ["@supabase/supabase-js"] },
      },
    },
  },
});
