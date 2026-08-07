/// <reference types="vitest" />
import path from "path"
import os from "os"
import react from "@vitejs/plugin-react"
import { defineConfig } from 'vitest/config'

const isWin = os.platform() === 'win32'

export default defineConfig({
  plugins: [
    react({
      // main.tsx 已拆出 createRoot 到 entry-client.tsx，Fast Refresh 可正常工作
      exclude: /node_modules/,
    }),
  ],
  appType: 'spa',
  // 明确指定index.html位置
  build: {
    rollupOptions: {
      input: path.resolve(__dirname, 'index.html'),
    }
  },
  define: {
    'process.env': '{}',
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    // strictPort=true：5173 被占就直接报错退出，避免静默换 5174/5175
    // 导致浏览器书签（停在 5173）还连着上一个旧 vite 实例，看到的永远是旧代码。
    // 统一用 scripts/stop-dev.ps1 + start-dev.ps1 管理生命周期。
    strictPort: true,
    allowedHosts: true,
    fs: {
      allow: ['..'],
    },
    hmr: {
      // 不固定 host：避免用 127.0.0.1 打开页面时 WS 仍连 localhost 导致 HMR 静默失败
      overlay: true,
    },
    watch: {
      // Windows + 含中文路径时 native watcher 偶发漏事件，开发环境启用 polling
      usePolling: isWin || process.env.VITE_USE_POLLING === '1',
      // 300ms 轮询：兼顾响应速度与 CPU 占用（原 1000ms 太慢）
      interval: Number(process.env.VITE_POLL_INTERVAL || 300),
      // 显式排除监控无关目录，避免后端 db 写入、日志滚动、python 缓存文件
      // 触发无意义的重新构建。
      ignored: [
        '**/node_modules/**',
        '**/.git/**',
        '**/dist/**',
        '**/__pycache__/**',
        '**/backend/**',
        '**/data/**',
        '**/logs/**',
        '**/_archive/**',
        '**/*.db',
        '**/*.sqlite',
        '**/*.log',
      ],
    },
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        secure: false,
      },
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./app"),
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./app/test/setup.ts'],
    include: ['app/**/*.test.{ts,tsx}'],
  },
})
