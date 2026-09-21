import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Разработка демо: VITE_API_URL= (пусто) + бэкенд на :8000 — запросы /api идут через прокси.
  server: { proxy: { '/api': 'http://localhost:8000' } },
})
