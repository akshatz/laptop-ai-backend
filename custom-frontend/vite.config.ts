import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In `npm run dev`, /api/* goes to custom-backend (published on 8011), the same way nginx
// forwards it in the container. BACKEND_URL overrides the target.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: process.env.BACKEND_URL || 'http://localhost:8011',
        rewrite: path => path.replace(/^\/api/, ''),
      },
    },
  },
});
