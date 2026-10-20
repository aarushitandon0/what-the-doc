import { defineConfig } from 'vite';
/** /api is proxied to the local FastAPI service, so the browser makes same-origin
 * requests and the backend does not need a CORS allowance for the dev server.
 * Override the target with VITE_API_TARGET if api.py runs on another port. */
const target = process.env.VITE_API_TARGET || 'http://127.0.0.1:8000';
export default defineConfig({
  base: './',
  server: {proxy: {'/api': {target, changeOrigin: false}}},
  preview: {proxy: {'/api': {target, changeOrigin: false}}},
  test: {environment: 'jsdom', setupFiles: ['./src/test-setup.js'], restoreMocks: true},
});
