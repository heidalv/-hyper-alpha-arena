/**
 * FeeMonitorPanel — 交易所费率/VIP等级/激励监控面板
 *
 * 展示当前费率档案、手续费效率分析、优化建议
 */
import { useState, useEffect, useCallback, useRef }from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card'
import { Button } from '../ui/button'
import {
  DollarSign, RefreshCw, TrendingDown, PiggyBank,
  BadgePercent, Lightbulb, ArrowRight, Star,
} from 'lucide-react'

interface FeeProfile {
  exchange: string
  tier: string
  maker_rate: number
  taker_rate: number
  volume_30d_usd: number
  next_tier_threshold: number
  savings_at_next_tier: number
  error?: string
}

interface OptimizationTip {
  category: string
  tip: string
  impact: string
}

interface FeeReport {
  period_days: number
  total_fee_usd: number
  total_volume_usd: number
  maker_pct: number
  taker_pct: number
  avg_fee_rate: number
  potential_savings_usd: number
  recommendations: string[]
  optimization_tips: Array<string | OptimizationTip>
  error?: string
}

const IMPACT_LABELS: Record<string, string> = {
  high: '高影响',
  medium: '中影响',
  low: '低影响',
}

const IMPACT_BADGE: Record<string, string> = {
  high: 'bg-orange-100 text-orange-700 border-orange-200 dark:bg-orange-900/30 dark:text-orange-300 dark:border-orange-800',
  medium: 'bg-blue-100 text-blue-700 border-blue-200 dark:bg-blue-900/30 dark:text-blue-300 dark:border-blue-800',
  low: 'bg-gray-100 text-gray-600 border-gray-200 dark:bg-gray-800 dark:text-gray-400 dark:border-gray-700',
}

function renderTipText(tip: string | OptimizationTip): string {
  return typeof tip === 'string' ? tip : tip.tip
}

export default function FeeMonitorPanel() {
  const [profile, setProfile] = useState<FeeProfile | null>(null)
  const [report, setReport] = useState<FeeReport | null>(null)
  const [loading, setLoading] = useState(false)
  const timer = useRef<ReturnType<typeof setInterval>>()

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const [pRes, rRes] = await Promise.allSettled([
        fetch('/api/monitor/fee-profile'),
        fetch('/api/monitor/fee-report'),
      ])
      if (pRes.status === 'fulfilled' && pRes.value.ok) setProfile(await pRes.value.json())
      if (rRes.status === 'fulfilled' && rRes.value.ok) setReport(await rRes.value.json())
    } catch (e) {
      console.error('[FeeMonitor] fetch error:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAll()
    timer.current = setInterval(fetchAll, 120_000)
    return () => clearInterval(timer.current)
  }, [fetchAll])

  const makerRateBps = (profile?.maker_rate ?? 0) * 10000
  const takerRateBps = (profile?.taker_rate ?? 0) * 10000
  const makerPct = Math.round((report?.maker_pct ?? 0) * 100)
  const takerPct = Math.round((report?.taker_pct ?? 0) * 100)

  return (
    <div className="h-full w-full flex flex-col bg-background">
      <div className="flex-shrink-0 flex items-center justify-between border-b px-6 py-3">
        <div className="flex items-center gap-3">
          <DollarSign className="w-5 h-5 text-emerald-600" />
          <div>
            <span className="font-semibold text-sm">费率与激励监控</span>
            <span className="text-xs text-muted-foreground ml-2">VIP 等级 · 手续费效率 · 优化建议</span>
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={fetchAll} disabled={loading} className="text-xs h-8">
          <RefreshCw className={`w-3 h-3 mr-1 ${loading ? 'animate-spin' : ''}`} />
          刷新
        </Button>
      </div>

      <div className="flex-1 overflow-auto p-4 space-y-4 max-w-5xl mx-auto w-full">

      {/* Fee Profile */}
      <Card>
        <CardHeader className="py-3 px-4">
          <CardTitle className="text-sm flex items-center gap-2">
            <BadgePercent className="w-4 h-4 text-muted-foreground" />
            当前费率档案
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-4 pt-0">
          {!profile ? (
            <p className="text-xs text-muted-foreground text-center py-4">加载中...</p>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <InfoBlock label="交易所" value={profile.exchange} />
              <InfoBlock label="VIP 等级" value={
                <span className="flex items-center gap-1">
                  <Star className="w-3 h-3 text-amber-500" />
                  {profile.tier}
                </span>
              } />
              <InfoBlock label="Maker 费率" value={`${makerRateBps.toFixed(1)} bps`} highlight="green" />
              <InfoBlock label="Taker 费率" value={`${takerRateBps.toFixed(1)} bps`} highlight="red" />
              <InfoBlock label="30天交易量" value={`$${fmtNum(profile.volume_30d_usd)}`} />
              <InfoBlock label="下一等级门槛" value={`$${fmtNum(profile.next_tier_threshold)}`} />
              <InfoBlock
                label="升级后可节省"
                value={`$${(profile.savings_at_next_tier ?? 0).toFixed(2)}/月`}
                highlight="green"
              />
            </div>
          )}
        </CardContent>
      </Card>

      {/* Fee Report */}
      <Card>
        <CardHeader className="py-3 px-4">
          <CardTitle className="text-sm flex items-center gap-2">
            <PiggyBank className="w-4 h-4 text-muted-foreground" />
            手续费效率报告 ({report?.period_days ?? 30}天)
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-4 pt-0">
          {!report ? (
            <p className="text-xs text-muted-foreground text-center py-4">加载中...</p>
          ) : (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
                <InfoBlock label="总手续费" value={`$${report.total_fee_usd.toFixed(4)}`} highlight="red" />
                <InfoBlock label="总交易量" value={`$${fmtNum(report.total_volume_usd)}`} />
                <InfoBlock label="平均费率" value={`${(report.avg_fee_rate * 10000).toFixed(2)} bps`} />
                <InfoBlock label="可节省" value={`$${report.potential_savings_usd.toFixed(4)}`} highlight="green" />
              </div>

              {/* Maker/Taker Bar */}
              <div className="mb-4">
                <div className="flex items-center justify-between text-xs text-muted-foreground mb-1.5">
                  <span>Maker / Taker 占比</span>
                  <span>
                    <span className="text-green-600 font-medium">{makerPct}% Maker</span>
                    {' · '}
                    <span className="text-red-600 font-medium">{takerPct}% Taker</span>
                  </span>
                </div>
                <div className="h-4 rounded-full overflow-hidden flex bg-muted">
                  {makerPct > 0 && (
                    <div
                      className="bg-green-500 flex items-center justify-center text-[10px] text-white font-medium min-w-0"
                      style={{ width: `${makerPct}%` }}
                    />
                  )}
                  {takerPct > 0 && (
                    <div
                      className="bg-red-400 flex items-center justify-center text-[10px] text-white font-medium min-w-0 flex-1"
                      style={{ width: takerPct > 0 && makerPct > 0 ? `${takerPct}%` : '100%' }}
                    />
                  )}
                  {makerPct === 0 && takerPct === 0 && (
                    <div className="w-full flex items-center justify-center text-[10px] text-muted-foreground">暂无数据</div>
                  )}
                </div>
              </div>

              {/* Recommendations */}
              {report.recommendations.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-xs text-muted-foreground flex items-center gap-1 font-medium">
                    <TrendingDown className="w-3 h-3" /> 优化建议
                  </p>
                  {report.recommendations.map((r, i) => (
                    <div key={i} className="flex items-start gap-2 text-xs px-3 py-2 rounded-md bg-muted/50 border">
                      <ArrowRight className="w-3 h-3 text-emerald-600 mt-0.5 flex-shrink-0" />
                      <span className="text-foreground leading-relaxed">{r}</span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* Tips */}
      {report?.optimization_tips && report.optimization_tips.length > 0 && (
        <Card>
          <CardHeader className="py-3 px-4">
            <CardTitle className="text-sm flex items-center gap-2">
              <Lightbulb className="w-4 h-4 text-amber-500" />
              费率优化技巧
            </CardTitle>
          </CardHeader>
          <CardContent className="px-4 pb-4 pt-0">
            <div className="space-y-2">
              {report.optimization_tips.map((item, i) => {
                const tipText = renderTipText(item)
                const impact = typeof item === 'object' && item !== null ? item.impact : null
                return (
                  <div
                    key={i}
                    className="flex items-start gap-2 text-xs px-3 py-2.5 rounded-md bg-sky-50 border border-sky-100 dark:bg-sky-950/20 dark:border-sky-900/40"
                  >
                    <Lightbulb className="w-3.5 h-3.5 text-sky-600 dark:text-sky-400 mt-0.5 flex-shrink-0" />
                    <div className="min-w-0 flex-1">
                      {impact && (
                        <span className={`inline-block mb-1 px-1.5 py-0.5 rounded text-[10px] border ${IMPACT_BADGE[impact] ?? IMPACT_BADGE.low}`}>
                          {IMPACT_LABELS[impact] ?? impact}
                        </span>
                      )}
                      <p className="leading-relaxed text-foreground">{tipText}</p>
                    </div>
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>
      )}
      </div>
    </div>
  )
}

function InfoBlock({ label, value, highlight }: {
  label: string; value: React.ReactNode; highlight?: 'green' | 'red'
}) {
  const valColor =
    highlight === 'green'
      ? 'text-green-600 dark:text-green-400'
      : highlight === 'red'
      ? 'text-red-600 dark:text-red-400'
      : 'text-foreground'
  return (
    <div>
      <p className="text-[10px] text-muted-foreground uppercase mb-0.5 tracking-wide">{label}</p>
      <p className={`text-sm font-semibold ${valColor}`}>{value}</p>
    </div>
  )
}

function fmtNum(n: number): string {
  if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B'
  if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M'
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K'
  return n.toFixed(2)
}
