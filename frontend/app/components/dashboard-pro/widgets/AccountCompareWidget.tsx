/**
 * AccountCompareWidget — 多账户 x 交易所 x 模式并排对比
 *
 * 核心新增组件：一眼看清「实盘 vs 模拟」「不同交易所」「不同账户」的表现差异。
 */
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import AnimatedNumber from '@/components/ui/animated-number'
import { Wifi, WifiOff, Loader2 } from 'lucide-react'
import type { WidgetProps, WsConnStatus } from '../types'

const MODE_LABEL: Record<string, string> = {
  paper: '模拟',
  testnet: '测试网',
  mainnet: '实盘',
}

const MODE_CLASS: Record<string, string> = {
  paper: 'bg-sky-500/15 text-sky-400 border-sky-500/30',
  testnet: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  mainnet: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
}

function WsIndicator({ status }: { status: WsConnStatus | undefined }) {
  if (status === 'open') return <Wifi className="h-3 w-3 text-emerald-400" />
  if (status === 'connecting') return <Loader2 className="h-3 w-3 text-amber-400 animate-spin" />
  return <WifiOff className="h-3 w-3 text-muted-foreground/50" />
}

export default function AccountCompareWidget({ overviews, wsStatusByAccount }: WidgetProps) {
  if (overviews.length === 0) {
    return (
      <div className="h-full w-full rounded-lg border border-border/70 bg-card/80 backdrop-blur-sm flex items-center justify-center">
        <span className="text-xs text-muted-foreground">请在顶部选择至少一个账户</span>
      </div>
    )
  }

  return (
    <div className="h-full w-full rounded-lg border border-border/70 bg-card/80 backdrop-blur-sm overflow-hidden flex flex-col">
      <div className="px-3 py-2 border-b border-border/60 flex items-center justify-between shrink-0">
        <span className="text-xs font-semibold uppercase tracking-wide text-foreground">账户对比</span>
        <Badge variant="outline" className="text-[10px] h-5">{overviews.length}</Badge>
      </div>
      <div className="flex-1 overflow-x-auto overflow-y-hidden">
        <div className="flex h-full divide-x divide-border/50">
          {overviews.map((o) => {
            const positive = o.total_pnl >= 0
            const key = `${o.account_id}-${o.exchange}-${o.trading_mode}`
            return (
              <div key={key} className="flex-1 min-w-[168px] p-3 flex flex-col gap-2">
                <div className="flex items-center justify-between gap-1">
                  <span className="text-xs font-medium text-foreground truncate">
                    {o.account_name || `账户 #${o.account_id}`}
                  </span>
                  <WsIndicator status={wsStatusByAccount[o.account_id]} />
                </div>
                <div className="flex items-center gap-1">
                  <Badge className={cn('text-[9px] h-4 px-1 border', MODE_CLASS[o.trading_mode])}>
                    {MODE_LABEL[o.trading_mode] || o.trading_mode}
                  </Badge>
                  <span className="text-[10px] text-muted-foreground truncate">{o.exchange}</span>
                </div>

                {o.error ? (
                  <div className="flex-1 flex items-center">
                    <span className="text-[11px] text-red-400/80">{o.error}</span>
                  </div>
                ) : (
                  <>
                    <div>
                      <div className="text-[10px] text-muted-foreground">权益</div>
                      <div className="text-base font-bold tabular-nums text-foreground">
                        <AnimatedNumber value={o.equity} decimals={2} prefix="$" />
                      </div>
                    </div>
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="text-muted-foreground">盈亏</span>
                      <span className={cn('font-medium tabular-nums', positive ? 'text-emerald-400' : 'text-red-400')}>
                        {positive ? '+' : ''}${o.total_pnl.toFixed(2)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="text-muted-foreground">胜率</span>
                      <span className="font-medium tabular-nums text-foreground">{o.win_rate.toFixed(1)}%</span>
                    </div>
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="text-muted-foreground">持仓</span>
                      <span className="font-medium tabular-nums text-foreground">{o.active_positions}</span>
                    </div>
                  </>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
