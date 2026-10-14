import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 构建产物直接输出到后端托管目录 web/（FastAPI StaticFiles 与 PyInstaller 均指向它）
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../web',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/healthz': 'http://127.0.0.1:8000',
    },
  },
})
