/**
 * HypothesisPanel — LLM 策略假设引擎状态面板
 *
 * 展示假设生成/验证/晋升统计，支持手动触发
 */
import { useState, useEffect, useCallback, useRef }from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card'
import { Button } from '../ui/button'
import {
  Lightbulb, RefreshCw, Play, CheckCircle2, XCircle,
  ArrowUp, Brain, FlaskConical, TrendingUp, Zap,
} from 'lucide-react'
import { useTradingPairs, FALLBACK_TRADING_PAIRS } from '@/hooks/useTradingPairs'

interface HypothesisItem {
  id: string
  description: string
  symbol: string
  exchange?: string
  period?: string
  direction: string
  confidence: number
  regime: string
  source: string
  status?: string
  snapshot_id?: string
  data_source?: string
  qaa_correlation_id?: string
  created_at: number | string | null
}

interface ValidationItem {
  id: string
  passed: boolean
  sharpe: number
  win_rate: number
  max_dd: number
  trades: number
  pnl: number
  error: string
  status?: string
  exchange?: string
  symbol?: string
  period?: string
  snapshot_id?: string
  data_source?: string
  promoted_template_id?: string
}

interface HypothesisData {
  total_generated: number
  total_validated: number
  total_promoted: number
  thresholds: {
    min_sharpe: number
    min_win_rate: number
    max_drawdown: number
  }
  recent_generated: HypothesisItem[]
  recent_validated: ValidationItem[]
  error?: string
}

const DIR_ICONS: Record<string, React.ReactNode> = {
  long: <TrendingUp className="w-3 h-3 text-green-400" />,
  short: <TrendingUp className="w-3 h-3 text-red-400 rotate-180" />,
  neutral: <ArrowUp className="w-3 h-3 text-slate-400 rotate-90" />,
}

function formatCreatedAt(value: number | string | null | undefined) {
  if (!value) return ''
  if (typeof value === 'number') return new Date(value * 1000).toLocaleString()
  return new Date(value).toLocaleString()
}

export default function HypothesisPanel() {
  const { symbols: configuredPairs } = useTradingPairs()
  const [data, setData] = useState<HypothesisData | null>(null)
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [runResult, setRunResult] = useState<any>(null)
  const timer = useRef<ReturnType<typeof setInterval>>()

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/monitor/hypothesis')
      if (res.ok) setData(await res.json())
    } catch (e) {
      console.error('[Hypothesis] fetch error:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
    timer.current = setInterval(fetchData, 60_000)
    return () => clearInterval(timer.current)
  }, [fetchData])

  const handleRun = async () => {
    setRunning(true)
    setRunResult(null)
    try {
      const res = await fetch('/api/monitor/hypothesis/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbols: (configuredPairs.length > 0 ? configuredPairs : FALLBACK_TRADING_PAIRS).slice(0, 5) }),
      })
      if (res.ok) {
        const result = await res.json()
        setRunResult(result)
        fetchData()
      }
    } catch (e) {
      console.error('[Hypothesis] run error:', e)
    } finally {
      setRunning(false)
    }
  }

  const thresholds = data?.thresholds

  return (
    <div className="p-4 space-y-4 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <Lightbulb className="w-5 h-5 text-amber-400" />
          <h2 className="text-lg font-bold text-white">策略假设引擎</h2>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={fetchData}
            disabled={loading}
            className="text-xs"
          >
            <RefreshCw className={`w-3 h-3 mr-1 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </Button>
          <Button
            size="sm"
            onClick={handleRun}
            disabled={running}
            className="text-xs bg-amber-600 hover:bg-amber-700"
          >
            <Play className={`w-3 h-3 mr-1 ${running ? 'animate-pulse' : ''}`} />
            {running ? '生成中...' : '手动生成'}
          </Button>
        </div>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-3 gap-3">
        <StatCard
          label="已生成假设"
          value={data?.total_generated ?? 0}
          icon={<Brain className="w-4 h-4" />}
          color="text-blue-400 bg-blue-400/10"
        />
        <StatCard
          label="已回测验证"
          value={data?.total_validated ?? 0}
          icon={<FlaskConical className="w-4 h-4" />}
          color="text-purple-400 bg-purple-400/10"
        />
        <StatCard
          label="已晋升策略"
          value={data?.total_promoted ?? 0}
          icon={<Zap className="w-4 h-4" />}
          color="text-amber-400 bg-amber-400/10"
        />
      </div>

      {/* Thresholds */}
      {thresholds && (
        <Card className="bg-[#1a1a2e] border-slate-700/50">
          <CardContent className="p-3">
            <div className="flex items-center gap-6 text-xs text-slate-400">
              <span>晋升标准:</span>
              <span>Sharpe ≥ <strong className="text-white">{thresholds.min_sharpe}</strong></span>
              <span>胜率 ≥ <strong className="text-white">{(thresholds.min_win_rate * 100).toFixed(0)}%</strong></span>
              <span>最大回撤 ≤ <strong className="text-white">{thresholds.max_drawdown}%</strong></span>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Run Result */}
      {runResult && (
        <Card className="bg-amber-900/10 border-amber-800/30">
          <CardContent className="p-3">
            <p className="text-xs text-amber-300 mb-2">
              本次生成 {runResult.triggered} 个假设
            </p>
            {runResult.results?.map((r: any, i: number) => (
              <div key={i} className="flex items-center gap-2 text-xs py-1">
                {r.passed
                  ? <CheckCircle2 className="w-3 h-3 text-green-400" />
                  : <XCircle className="w-3 h-3 text-red-400" />
                }
                <span className="text-slate-300 flex-1">{r.hypothesis}</span>
                <span className="text-slate-400">Sharpe {r.sharpe}</span>
                <span className="text-slate-400">WR {(r.win_rate * 100).toFixed(0)}%</span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* Recent Validated */}
      <Card className="bg-[#1a1a2e] border-slate-700/50">
        <CardHeader className="py-3 px-4">
          <CardTitle className="text-sm text-slate-300 flex items-center gap-2">
            <FlaskConical className="w-4 h-4" />
            验证记录
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-4 max-h-[300px] overflow-y-auto">
          {(data?.recent_validated ?? []).length === 0 ? (
            <p className="text-xs text-slate-500 text-center py-4">暂无验证记录</p>
          ) : (
            <div className="space-y-1.5">
              {(data?.recent_validated ?? []).slice().reverse().map((v, i) => (
                <div
                  key={i}
                  className={`flex items-center gap-3 px-3 py-2 rounded text-xs ${
                    v.passed
                      ? 'bg-green-900/15 border border-green-800/20'
                      : 'bg-slate-800/30 border border-slate-700/20'
                  }`}
                >
                  {v.passed
                    ? <CheckCircle2 className="w-3.5 h-3.5 text-green-400 flex-shrink-0" />
                    : <XCircle className="w-3.5 h-3.5 text-slate-500 flex-shrink-0" />
                  }
                  <span className="text-slate-400 font-mono truncate">{v.id.slice(0, 8)}</span>
                  <span className="text-slate-500">{v.exchange || '-'}:{v.symbol || '-'}/{v.period || '-'}</span>
                  <span className="text-slate-300">Sharpe {v.sharpe}</span>
                  <span className="text-slate-300">WR {(v.win_rate * 100).toFixed(0)}%</span>
                  <span className="text-slate-300">DD {v.max_dd.toFixed(1)}%</span>
                  <span className={v.pnl >= 0 ? 'text-green-400' : 'text-red-400'}>
                    PnL ${v.pnl.toFixed(2)}
                  </span>
                  {v.error && <span className="text-red-400 truncate ml-auto">{v.error}</span>}
                  {v.promoted_template_id && <span className="text-amber-400 truncate ml-auto">{v.promoted_template_id}</span>}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Recent Generated */}
      <Card className="bg-[#1a1a2e] border-slate-700/50">
        <CardHeader className="py-3 px-4">
          <CardTitle className="text-sm text-slate-300 flex items-center gap-2">
            <Brain className="w-4 h-4" />
            近期假设
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-4 max-h-[300px] overflow-y-auto">
          {(data?.recent_generated ?? []).length === 0 ? (
            <p className="text-xs text-slate-500 text-center py-4">暂无假设记录</p>
          ) : (
            <div className="space-y-1.5">
              {(data?.recent_generated ?? []).slice().reverse().map((h, i) => (
                <div
                  key={i}
                  className="flex items-start gap-2 px-3 py-2 rounded bg-slate-800/30 border border-slate-700/20 text-xs"
                >
                  <div className="mt-0.5">{DIR_ICONS[h.direction] || DIR_ICONS.neutral}</div>
                  <div className="flex-1 min-w-0">
                    <p className="text-slate-200">{h.description}</p>
                    <p className="text-slate-500 mt-0.5">
                      {h.exchange || '-'}:{h.symbol}/{h.period || '-'} · {h.direction} · {h.status || 'generated'} · conf: {(h.confidence * 100).toFixed(0)}%
                      {h.regime && ` · ${h.regime}`}
                    </p>
                    <p className="text-slate-600 mt-0.5">
                      source: {h.data_source || h.source || '-'}
                      {h.snapshot_id && ` · snapshot: ${h.snapshot_id.slice(0, 8)}`}
                      {h.qaa_correlation_id && ` · qaa: ${h.qaa_correlation_id.slice(0, 8)}`}
                    </p>
                  </div>
                  <span className="text-[10px] text-slate-600">
                    {formatCreatedAt(h.created_at)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function StatCard({ label, value, icon, color }: {
  label: string; value: number; icon: React.ReactNode; color: string
}) {
  return (
    <Card className="bg-[#1a1a2e] border-slate-700/50">
      <CardContent className="p-3 flex items-center gap-3">
        <div className={`p-2 rounded ${color}`}>{icon}</div>
        <div>
          <p className="text-[10px] text-slate-500 uppercase">{label}</p>
          <p className="text-lg font-bold text-white">{value}</p>
        </div>
      </CardContent>
    </Card>
  )
}
