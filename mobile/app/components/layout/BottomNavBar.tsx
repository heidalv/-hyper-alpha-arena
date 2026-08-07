import React from 'react'
import { Activity, CandlestickChart, PlayCircle, Layers } from 'lucide-react'

type TabId = 'monitor' | 'trading' | 'fullauto' | 'strategy'

interface BottomNavBarProps {
  active: TabId
  onChange: (tab: TabId) => void
}

const tabs: { id: TabId; label: string; Icon: any }[] = [
  { id: 'monitor', label: '监控', Icon: Activity },
  { id: 'trading', label: '交易', Icon: CandlestickChart },
  { id: 'fullauto', label: '自动', Icon: PlayCircle },
  { id: 'strategy', label: '策略', Icon: Layers },
]

export default function BottomNavBar({ active, onChange }: BottomNavBarProps) {
  return (
    <nav className="flex-shrink-0 bg-terminal-card border-t border-terminal-border flex items-center justify-around h-14 safe-bottom">
      {tabs.map(({ id, label, Icon }) => {
        const isActive = active === id
        return (
          <button
            key={id}
            onClick={() => onChange(id)}
            className={`flex flex-col items-center justify-center flex-1 h-full gap-0.5
              ${isActive ? 'text-terminal-primary' : 'text-terminal-muted'}
              active:opacity-70 transition-colors`}
          >
            <Icon size={22} strokeWidth={isActive ? 2.5 : 1.5} />
            <span className="text-[10px] font-medium">{label}</span>
          </button>
        )
      })}
    </nav>
  )
}

export type { TabId }
