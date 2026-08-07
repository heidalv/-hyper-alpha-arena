/**
 * entry-client — 应用渲染入口（副作用隔离）
 *
 * 从 main.tsx 拆出 createRoot() 调用，使 main.tsx 成为纯组件模块，
 * 让 @vitejs/plugin-react 的 Fast Refresh 能正常工作。
 *
 * Fast Refresh 硬性要求：入口文件不应有顶层副作用导出。
 * 原先 main.tsx 底部直接 createRoot().render()，导致每次改文件都整页刷新。
 *
 * main.tsx 的其他顶层 import（wsManager/i18n/全局 error handler）仍会执行，
 * 因为本文件 import 了 main.tsx，那些副作用不会丢失。
 */

import React from 'react'
import ReactDOM from 'react-dom/client'
import { Toaster } from 'react-hot-toast'
import App from './app/main'
import { ThemeProvider } from '@/contexts/ThemeContext'
import { AuthProvider } from '@/contexts/AuthContext'
import { ExchangeProvider } from '@/contexts/ExchangeContext'
import { TradingModeProvider } from '@/contexts/TradingModeContext'
import { ArenaDataProvider } from '@/contexts/ArenaDataContext'
import { BacktestProvider } from '@/contexts/BacktestContext'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider defaultTheme="light">
      <AuthProvider>
        <ExchangeProvider>
          <TradingModeProvider>
            <ArenaDataProvider>
              <BacktestProvider>
                <Toaster position="top-right" />
                <App />
              </BacktestProvider>
            </ArenaDataProvider>
          </TradingModeProvider>
        </ExchangeProvider>
      </AuthProvider>
    </ThemeProvider>
  </React.StrictMode>,
)
