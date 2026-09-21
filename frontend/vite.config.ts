import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 开发期 /api 代理到本地后端（8000），生产由 nginx 反代
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
