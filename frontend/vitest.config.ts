import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// 前端回归测试配置（docs/regression-ci.md §6）：vitest + jsdom，覆盖纯逻辑/状态机，
// 不引入端到端浏览器驱动。历史 Bug 的防复发用例放 tests/，文件命名 *.test.tsx。
// 与 vite.config.ts 分离：测试不需要 /api 代理与根目录 envDir，保持最小环境。
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.{ts,tsx}'],
  },
})
