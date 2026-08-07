import React, { useState } from 'react'
import BottomNavBar, { TabId } from './BottomNavBar'
import StatusBar from './StatusBar'
import MonitorPage from '@/pages/MonitorPage'
import TradingPage from '@/pages/TradingPage'
import FullAutoPage from '@/pages/FullAutoPage'
import StrategyPage from '@/pages/StrategyPage'
import { useWebSocket } from '@/hooks/useWebSocket'
import { ThemeProvider } from '@/hooks/useTheme'

export default function MobileShell() {
  const [activeTab, setActiveTab] = useState<TabId>('monitor')
  const ws = useWebSocket()

  return (
    <ThemeProvider>
      <div className="flex flex-col h-screen overflow-hidden">
        <StatusBar connected={ws.connected} accountName={ws.overview?.account?.name} />
        <main className="flex-1 overflow-y-auto">
          {/* All pages stay mounted; inactive ones hidden via display:none to preserve data & intervals */}
          <div className={activeTab === 'monitor' ? '' : 'hidden'}><MonitorPage ws={ws} /></div>
          <div className={activeTab === 'trading' ? '' : 'hidden'}><TradingPage ws={ws} /></div>
          <div className={activeTab === 'fullauto' ? '' : 'hidden'}><FullAutoPage ws={ws} /></div>
          <div className={activeTab === 'strategy' ? '' : 'hidden'}><StrategyPage ws={ws} /></div>
        </main>
        <BottomNavBar active={activeTab} onChange={setActiveTab} />
      </div>
    </ThemeProvider>
  )
}
