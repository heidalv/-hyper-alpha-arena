import { useState, useEffect, useCallback, useRef } from 'react'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { getHyperliquidBalance } from '@/lib/hyperliquidApi'
import type { HyperliquidBalance } from '@/lib/types/hyperliquid'
import { useTradingMode } from '@/contexts/TradingModeContext'

interface AccountInfo {
  account_id: number
  account_name: string
}

interface AccountRow {
  accountId: number
  name: string
  equity: number
  marginUsage: number
  healthy: boolean
  loading: boolean
}

interface AccountStatusCardProps {
  accounts: AccountInfo[]
  className?: string
}

export default function AccountStatusCard({ accounts, className }: AccountStatusCardProps) {
  const { tradingMode } = useTradingMode()
  const environment = tradingMode === 'testnet' || tradingMode === 'mainnet' ? tradingMode : undefined
  const [rows, setRows] = useState<AccountRow[]>([])
  const loadRef = useRef<() => void>(() => {})

  const loadBalances = useCallback(async () => {
    if (accounts.length === 0) {
      setRows([])
      return
    }

    const results = await Promise.allSettled(
      accounts.map(acc => getHyperliquidBalance(acc.account_id, environment))
    )

    const newRows: AccountRow[] = accounts.map((acc, i) => {
      const result = results[i]
      if (result.status === 'fulfilled' && result.value) {
        const b: HyperliquidBalance = result.value
        const equity = b.totalEquity ?? 0
        const margin = b.usedMargin ?? 0
        const marginPct = equity > 0 ? (margin / equity) * 100 : 0
        return {
          accountId: acc.account_id,
          name: acc.account_name,
          equity,
          marginUsage: marginPct,
          healthy: marginPct < 70,
          loading: false,
        }
      }
      return {
        accountId: acc.account_id,
        name: acc.account_name,
        equity: 0,
        marginUsage: 0,
        healthy: true,
        loading: false,
      }
    })
    setRows(newRows)
  }, [accounts, environment])

  loadRef.current = loadBalances

  useEffect(() => {
    loadBalances()
  }, [loadBalances])

  useEffect(() => {
    if (accounts.length === 0) return
    const timer = setInterval(() => loadRef.current(), 30000)
    return () => clearInterval(timer)
  }, [accounts.length])

  return (
    <div className={cn('bg-card border border-border rounded-lg flex flex-col overflow-hidden', className)}>
      <div className="px-3 py-2.5 border-b border-border">
        <span className="text-xs font-semibold uppercase tracking-wide text-foreground">
          账户状态
        </span>
      </div>

      <div className="flex-1 p-3">
        {accounts.length === 0 ? (
          <div className="flex items-center justify-center h-full">
            <span className="text-xs text-muted-foreground">未配置账户</span>
          </div>
        ) : (
          <div className="space-y-3">
            {rows.map(row => (
              <div key={row.accountId} className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-foreground truncate max-w-[120px]">
                    {row.name}
                  </span>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold tabular-nums text-foreground">
                      ${row.equity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </span>
                    <Badge
                      className={cn(
                        'text-[10px] h-4 px-1.5 border',
                        row.healthy
                          ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                          : 'bg-red-500/10 text-red-400 border-red-500/30'
                      )}
                    >
                      {row.healthy ? '健康' : '风险'}
                    </Badge>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                    <div
                      className={cn(
                        'h-full rounded-full transition-all duration-500',
                        row.marginUsage < 50
                          ? 'bg-emerald-500'
                          : row.marginUsage < 70
                            ? 'bg-amber-500'
                            : 'bg-red-500'
                      )}
                      style={{ width: `${Math.min(row.marginUsage, 100)}%` }}
                    />
                  </div>
                  <span className="text-[10px] text-muted-foreground tabular-nums w-10 text-right">
                    {row.marginUsage.toFixed(1)}%
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
