import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Port 3030, strict. `--strictPort` matters: Vite's default is to hop to the next free port
// on a clash, which would silently serve the UI from an origin the backend's CORS allowlist
// (WEB_ORIGIN) does not include — and the failure reads as "the API is broken", not "the
// port moved". Better to refuse to start.
export default defineConfig({
  plugins: [react()],
  server: { port: 3030, strictPort: true },
});
