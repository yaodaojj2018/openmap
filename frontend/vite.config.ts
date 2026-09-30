import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 开发期 /api 代理到本地后端（8000），生产由 nginx 反代
// 注意用 127.0.0.1 而非 localhost：Node 会把 localhost 解析为 ::1，
// 而 WSL 端口转发仅监听 IPv4，用 localhost 会挂起（已踩坑）
export default defineConfig({
  plugins: [react()],
  // env 指向仓库根：VITE_BMAP_AK 统一写在根目录 .env，与后端共享一份配置
  envDir: '..',
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
