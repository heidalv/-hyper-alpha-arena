import React from 'react'

interface RiskFreezeEntry {
  symbol: string
  frozen: boolean
  dailyPnlPercent: number
  reason?: string
}

interface RiskStatusProps {
  entries: RiskFreezeEntry[]
}

export const RiskStatus: React.FC<RiskStatusProps> = ({ entries }) => {
  const frozen = entries.filter(e => e.frozen)
  const normal = entries.filter(e => !e.frozen)

  if (entries.length === 0) {
    return null
  }

  return (
    <div className="mx-4 mt-3 p-4 bg-surface rounded-card border border-border">
      <h3 className="text-sm font-medium mb-3">风控状态</h3>

      {/* Frozen symbols */}
      {frozen.length > 0 && (
        <div className="mb-3">
          <span className="text-xs text-muted">per-symbol 冻结</span>
          <div className="mt-2 space-y-2">
            {frozen.map(entry => (
              <div key={entry.symbol} className="flex items-center gap-2 p-2 bg-loss/10 rounded border border-loss/30">
                <div className="w-2 h-2 rounded-full bg-loss flex-shrink-0" />
                <span className="text-sm font-medium">{entry.symbol}</span>
                <span className="text-xs text-loss ml-auto">
                  日亏 {entry.dailyPnlPercent.toFixed(1)}% {entry.reason ? `· ${entry.reason}` : ''}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Normal symbols */}
      {normal.length > 0 && (
        <div>
          <span className="text-xs text-muted">正常交易</span>
          <div className="mt-2 space-y-1.5">
            {normal.slice(0, 8).map(entry => (
              <div key={entry.symbol} className="flex items-center gap-2 text-sm">
                <div className="w-2 h-2 rounded-full bg-profit flex-shrink-0" />
                <span className="font-medium flex-1">{entry.symbol}</span>
                <span className={`tabular-nums ${entry.dailyPnlPercent >= 0 ? 'text-profit' : 'text-loss'}`}>
                  {entry.dailyPnlPercent >= 0 ? '+' : ''}{entry.dailyPnlPercent.toFixed(1)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
