import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The FastAPI app (step 17) serves /api; in dev, Vite proxies to it. With VITE_MOCK=true the
// app never calls the API (see src/api/mock.ts).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { '/api': 'http://localhost:8000' },
  },
})
