/**
 * 市场扫描器 + 异常检测页面
 *
 * 功能:
 *  - 市场扫描：按量/波动/趋势/资金费率排名的 Top N 币种
 *  - 异常检测：价格异常、量异常、资金费率异常
 *  - 市场状态：各币种的 regime classification (trending/ranging/volatile/crash)
 */
import React, { useEffect, useState, useCallback, useRef } from 'react'
import { cn } from '@/lib/utils'
import { usePageActive } from '@/hooks/usePageActive'
import {
  Search, Zap, AlertTriangle, RefreshCw, Activity,
  TrendingUp, TrendingDown, BarChart3, Shield, Eye, Loader2,
} from 'lucide-react'
import {
  type SymbolScore, type ScanResult, type AnomalyEvent,
  type AnomalyReport, type RegimeClassification, type ScanConfig,
  triggerMarketScan, getLatestScanResult, getAnomalyReport,
  getRegimeClassifications, getScanConfig, updateScanConfig,
  getScannableSymbols,
} from '@/lib/marketScannerApi'

// ── Constants ──

const REGIME_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  trending:  { label: '趋势', color: 'text-blue-400', bg: 'bg-blue-500/10' },
  ranging:   { label: '震荡', color: 'text-yellow-400', bg: 'bg-yellow-500/10' },
  volatile:  { label: '高波动', color: 'text-orange-400', bg: 'bg-orange-500/10' },
  crash:     { label: '崩盘', color: 'text-red-400', bg: 'bg-red-500/10' },
}

const SEVERITY_CONFIG: Record<string, { label: string; color: string; icon: string }> = {
  critical: { label: '极危', color: 'text-red-400', icon: '🔴' },
  high:     { label: '高危', color: 'text-orange-400', icon: '🟠' },
  medium:   { label: '中等', color: 'text-yellow-400', icon: '🟡' },
  low:      { label: '低', color: 'text-green-400', icon: '🟢' },
}

// ── Main Component ──

export default function MarketScannerPage() {
  const pageActive = usePageActive()
  const [tab, setTab] = useState<'scan' | 'anomaly' | 'regime'>('scan')

  const [scanResult, setScanResult] = useState<ScanResult | null>(null)
  const [scanConfig, setScanConfig] = useState<ScanConfig | null>(null)
  const [scanning, setScanning] = useState(false)
  const [anomalyReport, setAnomalyReport] = useState<AnomalyReport | null>(null)
  const [regimes, setRegimes] = useState<RegimeClassification[]>([])

  const [totalSymbols, setTotalSymbols] = useState(0)
  const [loading, setLoading] = useState(true)
  const [errors, setErrors] = useState<string[]>([])
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date())

  const fetchAll = useCallback(async () => {
    setLoading(true)
    const errs: string[] = []
    try {
      const [scan, anomaly, regimeList, config, syms] = await Promise.allSettled([
        getLatestScanResult(),
        getAnomalyReport(),
        getRegimeClassifications(),
        getScanConfig(),
        getScannableSymbols(),
      ])
      if (syms.status === 'fulfilled' && syms.value.count > 0) {
        setTotalSymbols(syms.value.count)
      }
      if (scan.status === 'fulfilled' && scan.value) {
        setScanResult(scan.value)
      } else if (scan.status === 'rejected') {
        errs.push('扫描数据加载失败')
      }
      if (anomaly.status === 'fulfilled' && anomaly.value) {
        setAnomalyReport(anomaly.value)
      } else if (anomaly.status === 'rejected') {
        errs.push('异常检测加载失败')
      }
      if (regimeList.status === 'fulfilled') {
        setRegimes(regimeList.value)
      } else {
        errs.push('市场状态加载失败')
      }
      if (config.status === 'fulfilled') {
        setScanConfig(config.value)
      }
      setLastRefresh(new Date())
    } catch (e) {
      errs.push('网络请求失败，请检查后端是否运行')
      console.error('[MarketScanner] fetch error:', e)
    } finally {
      setErrors(errs)
      setLoading(false)
    }
  }, [])

  const handleScan = useCallback(async () => {
    setScanning(true)
    setErrors([])
    try {
      const result = await triggerMarketScan({ top_n: scanConfig?.top_n || 20 })
      if (result.error) {
        setErrors([`扫描超时或失败: ${result.error}`])
      } else {
        setScanResult(result)
      }
      setLastRefresh(new Date())
    } catch (e: any) {
      setErrors([e.message || '扫描执行失败'])
      console.error('[MarketScanner] scan error:', e)
    } finally {
      setScanning(false)
    }
  }, [scanConfig])

  const initDone = useRef(false)
  useEffect(() => {
    if (!initDone.current) {
      initDone.current = true
      fetchAll()
    }
    if (!pageActive) return
    const interval = setInterval(fetchAll, 60_000)
    return () => clearInterval(interval)
  }, [fetchAll, pageActive])

  return (
    <div className="min-h-screen bg-background text-foreground p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Search className="w-7 h-7 text-purple-500" />
            市场扫描器
          </h1>
          <p className="text-muted-foreground text-sm mt-1">
            全市场动态扫描 · 异常检测 · 市场状态分类
            {totalSymbols > 0 && (
              <span className="ml-2 text-purple-400 font-medium">{totalSymbols} 个交易对</span>
            )}
            <span className="ml-3 text-muted-foreground/60">
              上次刷新: {lastRefresh.toLocaleTimeString()}
            </span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleScan}
            disabled={scanning}
            className={cn(
              'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors',
              'bg-purple-600 hover:bg-purple-700 text-white disabled:opacity-50',
            )}
          >
            {scanning
              ? <Loader2 className="w-4 h-4 animate-spin" />
              : <Zap className="w-4 h-4" />}
            {scanning ? '扫描中...' : '手动扫描'}
          </button>
          <button
            onClick={fetchAll}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 bg-secondary hover:bg-secondary/80 text-secondary-foreground rounded-lg text-sm transition-colors"
          >
            <RefreshCw className={cn('w-4 h-4', loading && 'animate-spin')} />
            刷新
          </button>
        </div>
      </div>

      {/* Errors */}
      {errors.length > 0 && (
        <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-400 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <div>{errors.join(' · ')}</div>
        </div>
      )}

      {/* Global loading */}
      {loading && !scanResult && regimes.length === 0 && (
        <div className="py-16 text-center">
          <Loader2 className="w-10 h-10 animate-spin mx-auto text-purple-500 mb-3" />
          <p className="text-sm text-muted-foreground">正在加载市场数据...</p>
        </div>
      )}

      {/* Tabs */}
      {(!loading || scanResult || regimes.length > 0) && (
        <>
          <div className="flex gap-2 mb-6 border-b border-border pb-2">
            {[
              { key: 'scan' as const, label: '扫描排名', icon: <BarChart3 className="w-4 h-4" /> },
              { key: 'anomaly' as const, label: '异常检测', icon: <AlertTriangle className="w-4 h-4" /> },
              { key: 'regime' as const, label: '市场状态', icon: <Activity className="w-4 h-4" /> },
            ].map(t => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={cn(
                  'flex items-center gap-1.5 px-4 py-2 rounded-t-lg text-sm font-medium transition-colors',
                  tab === t.key
                    ? 'bg-card text-foreground border border-border border-b-card -mb-[1px]'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {t.icon}
                {t.label}
                {t.key === 'anomaly' && anomalyReport && anomalyReport.events.length > 0 && (
                  <span className="ml-1 text-xs bg-red-500/20 text-red-400 px-1.5 py-0.5 rounded-full">
                    {anomalyReport.events.filter(e => e.severity === 'critical' || e.severity === 'high').length}
                  </span>
                )}
                {t.key === 'regime' && regimes.length > 0 && (
                  <span className="ml-1 text-xs bg-purple-500/20 text-purple-400 px-1.5 py-0.5 rounded-full">
                    {regimes.length}
                  </span>
                )}
              </button>
            ))}
          </div>

          {tab === 'scan' && <ScanTab result={scanResult} config={scanConfig} scanning={scanning} onScan={handleScan} totalSymbols={totalSymbols} />}
          {tab === 'anomaly' && <AnomalyTab report={anomalyReport} loading={loading} />}
          {tab === 'regime' && <RegimeTab regimes={regimes} loading={loading} />}
        </>
      )}
    </div>
  )
}

// ── Scan Tab ──

function ScanTab({ result, config, scanning, onScan, totalSymbols }: {
  result: ScanResult | null
  config: ScanConfig | null
  scanning: boolean
  onScan: () => void
  totalSymbols: number
}) {
  if (!result) {
    return (
      <EmptyState
        icon={<Search className="w-12 h-12 text-muted-foreground/30" />}
        message={totalSymbols > 0 ? `已发现 ${totalSymbols} 个交易对，点击开始全市场扫描` : '暂无扫描结果'}
        action={
          <button
            onClick={onScan}
            disabled={scanning}
            className="mt-3 flex items-center gap-2 px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg text-sm disabled:opacity-50 mx-auto"
          >
            {scanning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
            {scanning ? '正在全市场扫描...' : '全市场扫描'}
          </button>
        }
      />
    )
  }

  const scannedTotal = result.total_scanned || totalSymbols || result.top_symbols.length

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <SummaryCard label="全市场交易对" value={scannedTotal} />
        <SummaryCard label="达标交易对" value={result.top_symbols.length} />
        <SummaryCard label="扫描耗时" value={`${(result.scan_duration_ms / 1000).toFixed(1)}s`} />
        <SummaryCard label="Top N" value={config?.top_n || 20} />
        <SummaryCard label="扫描时间" value={result.timestamp ? new Date(result.timestamp).toLocaleTimeString() : '--'} />
      </div>

      {result.top_symbols.length === 0 ? (
        <EmptyState
          icon={<BarChart3 className="w-12 h-12 text-muted-foreground/30" />}
          message="扫描完成但未发现符合条件的交易对，市场可能处于低活跃状态"
        />
      ) : (
        <div className="rounded-xl border border-border bg-card overflow-hidden">
          <div className="px-4 py-3 border-b border-border flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-purple-500" />
            <span className="font-semibold text-sm">币种综合排名</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-muted-foreground border-b border-border text-xs">
                  <th className="text-left py-2 px-3 w-8">#</th>
                  <th className="text-left py-2 px-3">交易对</th>
                  <th className="text-right py-2 px-3">总分</th>
                  <th className="text-right py-2 px-3">成交量</th>
                  <th className="text-right py-2 px-3">波动率</th>
                  <th className="text-right py-2 px-3">趋势</th>
                  <th className="text-right py-2 px-3">资金费率</th>
                </tr>
              </thead>
              <tbody>
                {result.top_symbols.map((s, i) => (
                  <tr key={s.symbol} className="border-b border-border/30 hover:bg-muted/20 transition-colors">
                    <td className="py-2 px-3 text-muted-foreground">{i + 1}</td>
                    <td className="py-2 px-3 font-mono font-semibold text-foreground">{s.symbol}</td>
                    <td className="py-2 px-3 text-right"><ScoreBadge score={s.total_score} /></td>
                    <td className="py-2 px-3 text-right font-mono text-xs">{s.volume_score.toFixed(2)}</td>
                    <td className="py-2 px-3 text-right font-mono text-xs">{s.volatility_score.toFixed(2)}</td>
                    <td className="py-2 px-3 text-right font-mono text-xs">{s.trend_score.toFixed(2)}</td>
                    <td className="py-2 px-3 text-right font-mono text-xs">{s.funding_score.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Anomaly Tab ──

function AnomalyTab({ report, loading }: { report: AnomalyReport | null; loading: boolean }) {
  if (loading && !report) {
    return <LoadingPlaceholder message="正在检测市场异常..." />
  }
  if (!report || report.events.length === 0) {
    return (
      <EmptyState
        icon={<Shield className="w-12 h-12 text-green-500/30" />}
        message="未检测到异常，市场运行正常"
      />
    )
  }

  const criticalEvents = report.events.filter(e => e.severity === 'critical')
  const highEvents = report.events.filter(e => e.severity === 'high')

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <SummaryCard label="扫描币种" value={report.symbols_scanned} />
        <SummaryCard label="异常事件" value={report.events.length} />
        <SummaryCard label="极危" value={criticalEvents.length} highlight={criticalEvents.length > 0} />
        <SummaryCard label="高危" value={highEvents.length} highlight={highEvents.length > 0} />
      </div>

      <div className="space-y-2">
        {report.events.map((event, i) => (
          <AnomalyEventCard key={i} event={event} />
        ))}
      </div>
    </div>
  )
}

function AnomalyEventCard({ event }: { event: AnomalyEvent }) {
  const sev = SEVERITY_CONFIG[event.severity] || SEVERITY_CONFIG.medium
  return (
    <div className={cn(
      'rounded-lg border p-4 flex items-center justify-between',
      sev.color === 'text-red-400' ? 'bg-red-500/5 border-red-500/30' : 'bg-card border-border',
    )}>
      <div className="flex items-center gap-3">
        <span className="text-lg">{sev.icon}</span>
        <div>
          <div className="font-mono font-bold text-foreground">{event.symbol}</div>
          <div className="text-xs text-muted-foreground">
            {event.anomaly_type} · Z-Score: {event.z_score.toFixed(2)}
          </div>
        </div>
      </div>
      <div className="text-right">
        <div className={cn('text-sm font-semibold', sev.color)}>{sev.label}</div>
        <div className="text-xs text-muted-foreground">
          {event.detected_at ? new Date(event.detected_at).toLocaleTimeString() : '--'}
        </div>
      </div>
    </div>
  )
}

// ── Regime Tab ──

function RegimeTab({ regimes, loading }: { regimes: RegimeClassification[]; loading: boolean }) {
  if (loading && regimes.length === 0) {
    return <LoadingPlaceholder message="正在分类市场状态..." />
  }
  if (regimes.length === 0) {
    return (
      <EmptyState
        icon={<Eye className="w-12 h-12 text-muted-foreground/30" />}
        message="暂无市场状态数据，请稍后刷新重试"
      />
    )
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
      {regimes.map(r => {
        const cfg = REGIME_CONFIG[r.regime] || REGIME_CONFIG.ranging
        return (
          <div key={r.symbol} className="rounded-xl border border-border bg-card p-4">
            <div className="flex items-center justify-between mb-3">
              <span className="font-mono font-bold text-foreground">{r.symbol}</span>
              <span className={cn('text-xs font-medium px-2 py-0.5 rounded-full', cfg.color, cfg.bg)}>
                {cfg.label}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div>
                <span className="text-muted-foreground">置信度</span>
                <div className="font-mono font-medium">{(r.confidence * 100).toFixed(0)}%</div>
              </div>
              <div>
                <span className="text-muted-foreground">趋势方向</span>
                <div className={cn(
                  'font-medium flex items-center gap-1',
                  r.trend_direction === 'up' ? 'text-green-400' : r.trend_direction === 'down' ? 'text-red-400' : 'text-muted-foreground',
                )}>
                  {r.trend_direction === 'up' ? <TrendingUp className="w-3 h-3" /> : r.trend_direction === 'down' ? <TrendingDown className="w-3 h-3" /> : <Activity className="w-3 h-3" />}
                  {r.trend_direction === 'up' ? '上涨' : r.trend_direction === 'down' ? '下跌' : '中性'}
                </div>
              </div>
              <div>
                <span className="text-muted-foreground">波动率</span>
                <div className="font-mono">{(r.volatility_percentile * 100).toFixed(0)}%</div>
              </div>
              <div>
                <span className="text-muted-foreground">成交量</span>
                <div className="font-mono">{(r.volume_percentile * 100).toFixed(0)}%</div>
              </div>
            </div>
            <div className="mt-3">
              <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                <div
                  className={cn('h-full rounded-full transition-all', r.confidence > 0.7 ? 'bg-green-500' : r.confidence > 0.4 ? 'bg-yellow-500' : 'bg-red-500')}
                  style={{ width: `${r.confidence * 100}%` }}
                />
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── Shared Sub-components ──

function SummaryCard({ label, value, highlight }: { label: string; value: string | number; highlight?: boolean }) {
  return (
    <div className={cn(
      'rounded-xl border p-4',
      highlight ? 'border-red-500/30 bg-red-500/5 animate-pulse' : 'border-border bg-muted/30',
    )}>
      <div className="text-2xl font-bold">{value}</div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  )
}

function ScoreBadge({ score }: { score: number }) {
  const color = score >= 0.8 ? 'text-green-400' : score >= 0.5 ? 'text-yellow-400' : 'text-muted-foreground'
  return <span className={cn('font-mono font-bold', color)}>{score.toFixed(3)}</span>
}

function EmptyState({ icon, message, action }: { icon: React.ReactNode; message: string; action?: React.ReactNode }) {
  return (
    <div className="py-16 text-center">
      <div className="flex justify-center mb-3">{icon}</div>
      <div className="text-sm text-muted-foreground">{message}</div>
      {action}
    </div>
  )
}

function LoadingPlaceholder({ message }: { message: string }) {
  return (
    <div className="py-16 text-center">
      <Loader2 className="w-8 h-8 animate-spin mx-auto text-purple-500/50 mb-3" />
      <p className="text-sm text-muted-foreground">{message}</p>
    </div>
  )
}
