/**
 * Win95Toolbar — Quick access buttons + real-time status display
 * Replaces the old Header component
 */

interface Win95ToolbarProps {
  currentPage: string
  onPageChange: (page: string) => void
}

const QUICK_PAGES = [
  { id: 'comprehensive', icon: '📊', label: '仪表盘' },
  { id: 'klines', icon: '📈', label: 'K线' },
  { id: 'atas-v2', icon: '🤖', label: 'AI决策' },
  { id: 'ai-learning-center', icon: '🎓', label: 'AI学习' },
  { id: 'modern-signals', icon: '📡', label: '信号' },
  { id: 'strategy', icon: '📋', label: '策略' },
  { id: 'unified-factor', icon: '🔬', label: '因子' },
  { id: 'risk', icon: '🛡️', label: '风控' },
]

export default function Win95Toolbar({ currentPage, onPageChange }: Win95ToolbarProps) {
  return (
    <div className="w95-toolbar">
      {QUICK_PAGES.map((p, i) => (
        <button
          key={p.id}
          className={`w95-tb-btn ${currentPage === p.id ? 'active' : ''}`}
          onClick={() => onPageChange(p.id)}
          title={p.label}
        >
          {p.icon} {p.label}
        </button>
      ))}
      <div className="w95-tb-sep" />
      <button className="w95-tb-btn" title="刷新数据" onClick={() => window.location.reload()}>🔃</button>

      <style>{`
        .w95-toolbar {
          background: hsl(var(--card));
          padding: 2px 4px;
          border-bottom: 1px solid hsl(var(--border));
          display: flex;
          align-items: center;
          gap: 2px;
          flex-shrink: 0;
          flex-wrap: nowrap;
          overflow-x: auto;
          transition: background-color 0.3s ease;
        }
        .w95-tb-btn {
          background: hsl(var(--background)) !important;
          border: 1px solid transparent !important;
          padding: 2px 8px !important;
          font-size: 13px !important;
          cursor: pointer;
          display: flex !important;
          align-items: center !important;
          gap: 3px !important;
          height: 26px !important;
          min-height: 26px !important;
          min-width: unset !important;
          white-space: nowrap;
          color: hsl(var(--foreground)) !important;
        }
        .w95-tb-btn:hover {
          border: 1px solid hsl(var(--border)) !important;
          background: hsl(var(--muted)) !important;
        }
        .w95-tb-btn:active {
          border: 1px solid hsl(var(--border)) !important;
          background: hsl(var(--accent)) !important;
        }
        .w95-tb-btn.active {
          border: 1px solid hsl(var(--border)) !important;
          background: hsl(var(--muted)) !important;
        }
        .w95-tb-sep {
          width: 1px;
          height: 18px;
          background: hsl(var(--border));
          margin: 0 3px;
          border-right: none;
          flex-shrink: 0;
        }
      `}</style>
    </div>
  )
}
