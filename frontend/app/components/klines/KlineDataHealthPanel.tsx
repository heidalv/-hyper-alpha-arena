/**
 * KlineDataHealthPanel — 数据健康面板
 *
 * 显示各周期数据健康状况（覆盖率、新鲜度、缺口数），
 * 支持一键多周期回填。
 */

import { useState, useEffect, useCallback, Component } from 'react'
import { RefreshCw, Clock, BarChart3, AlertTriangle, CheckCircle2 } from 'lucide-react'

// 简易错误边界：防止健康面板崩溃整个页面
class HealthPanelErrorBoundary extends Component<{ children: React.ReactNode }, { hasError: boolean }> {
  constructor(props: any) {
    super(props)
    this.state = { hasError: false }
  }
  static getDerivedStateFromError() {
    return { hasError: true }
  }
  render() {
    if (this.state.hasError) return null
    return this.props.children
  }
}

interface PeriodHealth {
  period: string
  status: string
  record_count: number
  coverage_pct: number
  freshness_seconds: number | null
  gap_count: number
  latest_timestamp: number | null
  oldest_timestamp: number | null
}

interface HealthData {
  symbol: string
  overall_status: string
  periods: Record<string, PeriodHealth>
}

interface KlineDataHealthPanelProps {
  symbol: string
  period?: string
  onBackfillMultiPeriod?: (periods: string[]) => void
}

const PERIOD_LABELS: Record<string, string> = {
  '1m': '1分',
  '5m': '5分',
  '15m': '15分',
  '30m': '30分',
  '1h': '1时',
  '4h': '4时',
  '1d': '日线',
}

const STATUS_COLORS: Record<string, string> = {
  healthy: '#22c55e',
  degraded: '#f59e0b',
  gaps: '#f59e0b',
  stale: '#ef4444',
  no_data: '#6b7280',
  error: '#ef4444',
}

const STATUS_LABELS: Record<string, string> = {
  healthy: '正常',
  degraded: '偏低',
  gaps: '有缺口',
  stale: '陈旧',
  no_data: '无数据',
  error: '错误',
}

const BADGE_BY_STATUS: Record<string, { color: string; bg: string; text: string }> = {
  healthy: { color: '#22c55e', bg: '#14532d', text: '当前周期正常' },
  degraded: { color: '#f59e0b', bg: '#78350f', text: '当前周期偏低' },
  gaps: { color: '#f59e0b', bg: '#78350f', text: '当前周期有缺口' },
  stale: { color: '#ef4444', bg: '#7f1d1d', text: '当前周期陈旧' },
  no_data: { color: '#ef4444', bg: '#7f1d1d', text: '当前周期无数据' },
  error: { color: '#ef4444', bg: '#7f1d1d', text: '检查失败' },
}

function formatFreshness(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return '-'
  if (seconds < 60) return `${seconds}秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}分钟`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}小时`
  return `${Math.floor(seconds / 86400)}天`
}

const BACKFILL_PERIODS = ['1m', '5m', '15m', '30m', '1h', '4h', '1d']

export default function KlineDataHealthPanel({ symbol, period = '1m', onBackfillMultiPeriod }: KlineDataHealthPanelProps) {
  const [health, setHealth] = useState<HealthData | null>(null)
  const [loading, setLoading] = useState(false)
  const [expanded, setExpanded] = useState(false)

  const fetchHealth = useCallback(async () => {
    if (!symbol) return
    setLoading(true)
    try {
      const res = await fetch(`/api/klines/health/${symbol}`)
      if (res.ok) {
        const data = await res.json()
        setHealth(data)
      }
    } catch {} finally {
      setLoading(false)
    }
  }, [symbol])

  useEffect(() => {
    fetchHealth()
  }, [fetchHealth])

  // 自动刷新（每30秒）
  useEffect(() => {
    const interval = setInterval(fetchHealth, 30000)
    return () => clearInterval(interval)
  }, [fetchHealth])

  const currentHealth = health?.periods?.[period]
  const overallBadge = health
    ? currentHealth
      ? BADGE_BY_STATUS[currentHealth.status] || { color: '#6b7280', bg: '#374151', text: '当前周期未知' }
      : { color: '#6b7280', bg: '#374151', text: '当前周期未统计' }
    : null

  const handleMultiBackfill = () => {
    // 找出需要回填的周期（coverage < 80% 或 no_data）
    if (!health) {
      onBackfillMultiPeriod?.(BACKFILL_PERIODS)
      return
    }
    const needsBackfill = BACKFILL_PERIODS.filter(p => {
      const h = health.periods[p]
      if (!h) return true
      return h.status === 'no_data' || h.coverage_pct < 80
    })
    onBackfillMultiPeriod?.(needsBackfill.length > 0 ? needsBackfill : BACKFILL_PERIODS)
  }

  return (
    <HealthPanelErrorBoundary>
    <div style={{ fontSize: 12, borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
      {/* Header */}
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '6px 12px', cursor: 'pointer', userSelect: 'none',
          background: 'rgba(255,255,255,0.02)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <BarChart3 size={14} style={{ color: '#94a3b8' }} />
          <span style={{ color: '#cbd5e1', fontWeight: 500 }}>数据健康</span>
          {overallBadge && (
            <span style={{
              padding: '1px 8px', borderRadius: 10, fontSize: 11,
              background: overallBadge.bg, color: overallBadge.color,
            }}>
              {overallBadge.text}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <button
            onClick={(e) => { e.stopPropagation(); fetchHealth() }}
            disabled={loading}
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              color: '#64748b', padding: 2,
              opacity: loading ? 0.5 : 1,
            }}
          >
            <RefreshCw size={13} style={{ animation: loading ? 'spin 1s linear infinite' : 'none' }} />
          </button>
          <span style={{ color: '#475569', transform: expanded ? 'rotate(180deg)' : '', transition: 'transform 0.2s' }}>▼</span>
        </div>
      </div>

      {/* Body */}
      {expanded && (
        <div style={{ padding: '8px 12px' }}>
          {/* Period grid */}
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)',
            gap: 6, marginBottom: 8,
          }}>
            {BACKFILL_PERIODS.map(period => {
              const h = health?.periods[period]
              const statusColor = h ? STATUS_COLORS[h.status] || '#6b7280' : '#374151'
              const statusLabel = h ? (STATUS_LABELS[h.status] || h.status) : '-'
              const coverage = h?.coverage_pct ?? 0

              return (
                <div key={period} style={{
                  background: 'rgba(255,255,255,0.03)',
                  borderRadius: 6, padding: '6px 8px',
                  border: '1px solid rgba(255,255,255,0.06)',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                    <span style={{ color: '#94a3b8', fontWeight: 500 }}>{PERIOD_LABELS[period]}</span>
                    <span style={{
                      width: 8, height: 8, borderRadius: '50%',
                      background: statusColor,
                      display: 'inline-block',
                    }} />
                  </div>
                  {h && h.status !== 'no_data' ? (
                    <>
                      <div style={{ color: coverage >= 80 ? '#22c55e' : coverage >= 50 ? '#f59e0b' : '#ef4444', fontWeight: 600, fontSize: 14 }}>
                        {coverage.toFixed(0)}%
                      </div>
                      <div style={{ color: '#64748b', fontSize: 10, marginTop: 2, display: 'flex', alignItems: 'center', gap: 3 }}>
                        <Clock size={10} />
                        {formatFreshness(h.freshness_seconds)}
                      </div>
                      {h.gap_count > 0 && (
                        <div style={{ color: '#f59e0b', fontSize: 10, display: 'flex', alignItems: 'center', gap: 3 }}>
                          <AlertTriangle size={10} />
                          {h.gap_count}个缺口
                        </div>
                      )}
                    </>
                  ) : (
                    <div style={{ color: '#64748b', fontSize: 11 }}>{statusLabel}</div>
                  )}
                </div>
              )
            })}
          </div>

          {/* Multi-period backfill button */}
          <button
            onClick={handleMultiBackfill}
            style={{
              width: '100%', padding: '6px 12px',
              background: 'rgba(59, 130, 246, 0.15)',
              border: '1px solid rgba(59, 130, 246, 0.3)',
              borderRadius: 6, color: '#60a5fa',
              cursor: 'pointer', fontSize: 12,
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            }}
          >
            <CheckCircle2 size={13} />
            多周期一键回填（近30天）
          </button>
        </div>
      )}
    </div>
    </HealthPanelErrorBoundary>
  )
}
