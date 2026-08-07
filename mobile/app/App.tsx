import React, { useState, useCallback } from 'react'
import MonitorPage from './pages/MonitorPage'
import TradingPage from './pages/TradingPage'
import FullAutoPage from './pages/FullAutoPage'
import StrategyPage from './pages/StrategyPage'
import AccountsPage from './pages/AccountsPage'
import SettingsPage from './components/settings/SettingsPage'

type TabKey = 'monitor' | 'trading' | 'fullauto' | 'strategy' | 'accounts'
type ViewMode = 'tabs' | 'settings'

// Status Bar component
const StatusBar: React.FC<{ connected: boolean; accountName?: string; onSettings: () => void }> = ({ connected, accountName, onSettings }) => (
  <div className="flex items-center justify-between px-4 py-2 bg-surface border-b border-border safe-top">
    <div className="flex items-center gap-2">
      <div className={`w-2 h-2 rounded-full ${connected ? 'bg-profit' : 'bg-loss animate-pulse'}`} />
      <span className="text-xs text-muted">{connected ? '已连接' : '连接中...'}</span>
    </div>
    <div className="flex items-center gap-3">
      {accountName && <span className="text-xs text-muted font-medium">{accountName}</span>}
      <button onClick={onSettings} className="p-1 rounded hover:bg-terminal-card/50 active:opacity-70" title="设置">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#6b7280" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      </button>
    </div>
  </div>
)

// Bottom Nav Bar component
const BottomNavBar: React.FC<{ activeTab: TabKey; onTabChange: (tab: TabKey) => void }> = ({ activeTab, onTabChange }) => {
  const tabs: Array<{ key: TabKey; label: string; icon: React.FC<{ active: boolean }> }> = [
    {
      key: 'monitor',
      label: '监控',
      icon: ({ active }) => (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={active ? '#3b82f6' : '#6b7280'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
        </svg>
      )
    },
    {
      key: 'trading',
      label: '交易',
      icon: ({ active }) => (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={active ? '#3b82f6' : '#6b7280'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="2" y="3" width="20" height="18" rx="2" />
          <path d="M8 14l3-3 3 3 3-3" />
        </svg>
      )
    },
    {
      key: 'fullauto',
      label: '自动',
      icon: ({ active }) => (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={active ? '#3b82f6' : '#6b7280'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10" />
          <polygon points="10 8 16 12 10 16 10 8" fill={active ? '#3b82f6' : '#6b7280'} />
        </svg>
      )
    },
    {
      key: 'strategy',
      label: '策略',
      icon: ({ active }) => (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={active ? '#3b82f6' : '#6b7280'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="3" y="3" width="7" height="7" rx="1" />
          <rect x="14" y="3" width="7" height="7" rx="1" />
          <rect x="3" y="14" width="7" height="7" rx="1" />
          <rect x="14" y="14" width="7" height="7" rx="1" />
        </svg>
      )
    },
    {
      key: 'accounts',
      label: '账户',
      icon: ({ active }) => (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke={active ? '#3b82f6' : '#6b7280'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
          <circle cx="9" cy="7" r="4" />
          <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
          <path d="M16 3.13a4 4 0 0 1 0 7.75" />
        </svg>
      )
    }
  ]

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-40 bg-surface/95 backdrop-blur-sm border-t border-border safe-bottom">
      <div className="flex items-stretch">
        {tabs.map(tab => {
          const Icon = tab.icon
          const isActive = activeTab === tab.key
          return (
            <button
              key={tab.key}
              className="flex-1 flex flex-col items-center justify-center py-2 min-h-[44px] touch-feedback"
              onClick={() => onTabChange(tab.key)}
            >
              <Icon active={isActive} />
              <span className={`text-xs mt-0.5 ${isActive ? 'text-primary font-medium' : 'text-muted'}`}>
                {tab.label}
              </span>
            </button>
          )
        })}
      </div>
    </nav>
  )
}

// Page content router
const PageContent: React.FC<{ tab: TabKey }> = ({ tab }) => {
  switch (tab) {
    case 'monitor': return <MonitorPage />
    case 'trading': return <TradingPage />
    case 'fullauto': return <FullAutoPage />
    case 'strategy': return <StrategyPage />
    case 'accounts': return <AccountsPage />
    default: return <MonitorPage />
  }
}

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabKey>('monitor')
  const [viewMode, setViewMode] = useState<ViewMode>('tabs')
  const [connected] = useState(true)
  const accountName = 'AlphaArena'

  const handleTabChange = useCallback((tab: TabKey) => {
    setActiveTab(tab)
  }, [])

  const handleOpenSettings = useCallback(() => {
    setViewMode('settings')
  }, [])

  const handleCloseSettings = useCallback(() => {
    setViewMode('tabs')
  }, [])

  return (
    <div className="flex flex-col h-full">
      <StatusBar connected={connected} accountName={accountName} onSettings={handleOpenSettings} />
      {viewMode === 'settings' ? (
        <SettingsPage onBack={handleCloseSettings} />
      ) : (
        <>
          <main className="flex-1 overflow-y-auto pb-16">
            <PageContent tab={activeTab} />
          </main>
          <BottomNavBar activeTab={activeTab} onTabChange={handleTabChange} />
        </>
      )}
    </div>
  )
}
