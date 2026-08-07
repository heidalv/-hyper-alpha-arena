import { Moon, Sun, Monitor } from 'lucide-react'
import { useTheme, type Theme } from '@/contexts/ThemeContext'

const OPTIONS: { value: Theme; label: string; icon: typeof Sun }[] = [
  { value: 'light', label: '日间', icon: Sun },
  { value: 'dark', label: '夜间', icon: Moon },
  { value: 'system', label: '跟随系统', icon: Monitor },
]

interface ThemeToggleProps {
  collapsed?: boolean
  isDark?: boolean
}

export default function ThemeToggle({ collapsed = false, isDark = false }: ThemeToggleProps) {
  const { theme, resolvedTheme, setTheme, toggleTheme } = useTheme()

  const activeIcon = theme === 'system' ? Monitor : resolvedTheme === 'dark' ? Moon : Sun
  const ActiveIcon = activeIcon

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={toggleTheme}
        title={resolvedTheme === 'dark' ? '切换为日间模式' : '切换为夜间模式'}
        className="flex items-center justify-center w-full py-2 text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
      >
        <ActiveIcon className="w-4 h-4" />
      </button>
    )
  }

  return (
    <div
      className="px-2 py-2 border-t"
      style={{
        borderColor: isDark ? '#374151' : '#D0D0D0',
      }}
    >
      <div className="text-[10px] font-semibold tracking-wide mb-1.5 px-1"
        style={{ color: isDark ? '#9CA3AF' : '#808080' }}
      >
        外观
      </div>
      <div className="flex gap-1">
        {OPTIONS.map(({ value, label, icon: Icon }) => {
          const active = theme === value
          return (
            <button
              key={value}
              type="button"
              onClick={() => setTheme(value)}
              title={label}
              className={`flex-1 flex items-center justify-center gap-1 py-1.5 rounded text-[11px] transition-colors ${
                active
                  ? isDark
                    ? 'bg-violet-900/50 text-violet-200 ring-1 ring-violet-700'
                    : 'bg-violet-100 text-violet-800 ring-1 ring-violet-300'
                  : isDark
                    ? 'text-gray-400 hover:bg-gray-800'
                    : 'text-gray-600 hover:bg-gray-100'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              <span>{label}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
