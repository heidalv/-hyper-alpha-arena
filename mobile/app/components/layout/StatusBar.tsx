import React from 'react'
import { Wifi, WifiOff } from 'lucide-react'
import { useTheme } from '@/hooks/useTheme'

interface StatusBarProps {
  connected: boolean
  accountName?: string
}

export default function StatusBar({ connected, accountName }: StatusBarProps) {
  const { resolved, toggle } = useTheme()

  return (
    <div className="flex-shrink-0 h-10 bg-terminal-card border-b border-terminal-border flex items-center justify-between px-4">
      <div className="flex items-center gap-2">
        {connected ? (
          <Wifi size={14} className="text-terminal-profit" />
        ) : (
          <WifiOff size={14} className="text-terminal-loss" />
        )}
        <span className="text-xs text-terminal-muted">
          {connected ? '已连接' : '未连接'}
        </span>
      </div>
      <div className="flex items-center gap-3">
        {accountName && (
          <span className="text-xs text-terminal-muted">{accountName}</span>
        )}
        {/* Theme toggle */}
        <button
          onClick={toggle}
          className="flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium
                     bg-terminal-border/50 hover:bg-terminal-border transition-colors
                     active:opacity-70"
          title={resolved === 'dark' ? '切换日间模式' : '切换夜间模式'}
        >
          {resolved === 'dark' ? (
            <>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-yellow-400">
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2v2" />
                <path d="M12 20v2" />
                <path d="m4.93 4.93 1.41 1.41" />
                <path d="m17.66 17.66 1.41 1.41" />
                <path d="M2 12h2" />
                <path d="M20 12h2" />
                <path d="m6.34 17.66-1.41 1.41" />
                <path d="m19.07 4.93-1.41 1.41" />
              </svg>
              <span className="text-terminal-text">日间</span>
            </>
          ) : (
            <>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-blue-400">
                <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" />
              </svg>
              <span className="text-terminal-text">夜间</span>
            </>
          )}
        </button>
      </div>
    </div>
  )
}
