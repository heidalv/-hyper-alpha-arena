/**
 * DataQualityPanel — 数据质量监控看板
 *
 * 展示数据源健康度、K线新鲜度告警、因子异常告警
 */
import { useState, useEffect, useCallback, useRef }from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card'
import { Button } from '../ui/button'
import {
  Database, RefreshCw, AlertTriangle, CheckCircle2,
  XCircle, Clock, Activity, Wifi, WifiOff,
} from 'lucide-react'

interface SourceHealth {
  total_calls: number
  success_rate: number
  avg_latency_ms: number
  last_success: number
  last_failure: number
  last_error: string
  healthy: boolean
}

interface Alert {
  level: string
  source: string
  symbol: string
  message: string
  timestamp: number
  details: Record<string, any>
}

interface DQData {
  source_health: Record<string, SourceHealth>
  recent_alerts: Alert[]
  stale_threshold_sec: number
  error?: string
}

export default function DataQualityPanel() {
  const [data, setData] = useState<DQData | null>(null)
  const [loading, setLoading] = useState(false)
  const timer = useRef<ReturnType<typeof setInterval>>()

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/monitor/data-quality')
      if (res.ok) setData(await res.json())
    } catch (e) {
      console.error('[DataQuality] fetch error:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
    timer.current = setInterval(fetchData, 30_000)
    return () => clearInterval(timer.current)
  }, [fetchData])

  const sources = data ? Object.entries(data.source_health) : []
  const alerts = data?.recent_alerts ?? []
  const criticals = alerts.filter(a => a.level === 'critical')
  const warnings = alerts.filter(a => a.level === 'warning')

  return (
    <div className="p-4 space-y-4 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Database className="w-5 h-5 text-blue-400" />
          <h2 className="text-lg font-bold text-white">数据质量监控</h2>
        </div>
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
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-4 gap-3">
        <SummaryCard
          label="数据源"
          value={sources.length}
          icon={<Database className="w-4 h-4" />}
          color="blue"
        />
        <SummaryCard
          label="健康"
          value={sources.filter(([, s]) => s.healthy).length}
          icon={<CheckCircle2 className="w-4 h-4" />}
          color="green"
        />
        <SummaryCard
          label="严重告警"
          value={criticals.length}
          icon={<XCircle className="w-4 h-4" />}
          color="red"
        />
        <SummaryCard
          label="警告"
          value={warnings.length}
          icon={<AlertTriangle className="w-4 h-4" />}
          color="yellow"
        />
      </div>

      {/* Source Health Table */}
      <Card className="bg-[#1a1a2e] border-slate-700/50">
        <CardHeader className="py-3 px-4">
          <CardTitle className="text-sm text-slate-300 flex items-center gap-2">
            <Activity className="w-4 h-4" />
            数据源健康状态
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-4">
          {sources.length === 0 ? (
            <p className="text-xs text-slate-500 text-center py-4">暂无数据源记录</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-slate-400 border-b border-slate-700/50">
                    <th className="text-left py-2 px-2">数据源</th>
                    <th className="text-right py-2 px-2">调用次数</th>
                    <th className="text-right py-2 px-2">成功率</th>
                    <th className="text-right py-2 px-2">延迟(ms)</th>
                    <th className="text-center py-2 px-2">状态</th>
                    <th className="text-left py-2 px-2">最近错误</th>
                  </tr>
                </thead>
                <tbody>
                  {sources.map(([name, s]) => (
                    <tr key={name} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                      <td className="py-2 px-2 font-medium text-slate-200">{name}</td>
                      <td className="py-2 px-2 text-right text-slate-300">{s.total_calls}</td>
                      <td className="py-2 px-2 text-right">
                        <span className={s.success_rate >= 0.9 ? 'text-green-400' : s.success_rate >= 0.7 ? 'text-yellow-400' : 'text-red-400'}>
                          {(s.success_rate * 100).toFixed(1)}%
                        </span>
                      </td>
                      <td className="py-2 px-2 text-right text-slate-300">
                        {s.avg_latency_ms.toFixed(0)}
                      </td>
                      <td className="py-2 px-2 text-center">
                        {s.healthy
                          ? <Wifi className="w-3.5 h-3.5 text-green-400 inline" />
                          : <WifiOff className="w-3.5 h-3.5 text-red-400 inline" />
                        }
                      </td>
                      <td className="py-2 px-2 text-slate-500 truncate max-w-[200px]">
                        {s.last_error || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Recent Alerts */}
      <Card className="bg-[#1a1a2e] border-slate-700/50">
        <CardHeader className="py-3 px-4">
          <CardTitle className="text-sm text-slate-300 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" />
            近期告警 ({alerts.length})
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-4 max-h-[400px] overflow-y-auto">
          {alerts.length === 0 ? (
            <p className="text-xs text-slate-500 text-center py-4">无告警记录，系统运行正常</p>
          ) : (
            <div className="space-y-1.5">
              {alerts.slice().reverse().map((a, i) => (
                <div
                  key={i}
                  className={`flex items-start gap-2 px-3 py-2 rounded text-xs ${
                    a.level === 'critical'
                      ? 'bg-red-900/20 border border-red-800/30'
                      : 'bg-yellow-900/15 border border-yellow-800/20'
                  }`}
                >
                  {a.level === 'critical'
                    ? <XCircle className="w-3.5 h-3.5 text-red-400 mt-0.5 flex-shrink-0" />
                    : <AlertTriangle className="w-3.5 h-3.5 text-yellow-400 mt-0.5 flex-shrink-0" />
                  }
                  <div className="flex-1 min-w-0">
                    <p className="text-slate-200">{a.message}</p>
                    <p className="text-slate-500 mt-0.5">
                      {a.source} · {a.symbol} · {new Date(a.timestamp * 1000).toLocaleTimeString()}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function SummaryCard({ label, value, icon, color }: {
  label: string; value: number; icon: React.ReactNode; color: string
}) {
  const colors: Record<string, string> = {
    blue: 'text-blue-400 bg-blue-400/10',
    green: 'text-green-400 bg-green-400/10',
    red: 'text-red-400 bg-red-400/10',
    yellow: 'text-yellow-400 bg-yellow-400/10',
  }
  return (
    <Card className="bg-[#1a1a2e] border-slate-700/50">
      <CardContent className="p-3 flex items-center gap-3">
        <div className={`p-2 rounded ${colors[color]}`}>{icon}</div>
        <div>
          <p className="text-[10px] text-slate-500 uppercase">{label}</p>
          <p className="text-lg font-bold text-white">{value}</p>
        </div>
      </CardContent>
    </Card>
  )
}
