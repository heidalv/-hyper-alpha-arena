import React, { useState, useEffect, useCallback } from 'react'
import BottomSheet from '@/components/ui/BottomSheet'
import TouchButton from '@/components/ui/TouchButton'
import { getSessions } from '@/api/fullauto'
import { getPaperPositions, type PaperPosition } from '@/api/trading'
import type { FullAutoSession } from '@/api/types'
import TradingKlineChart from '@/components/trading/TradingKlineChart'
import { useKlines } from '@/hooks/useKlines'
import { useTradingPairs } from '@/hooks/useTradingPairs'

type SortField = 'symbol' | 'pnl' | 'pnl_pct' | 'size'

function formatPrice(v: number): string {
  if (v >= 1000) return v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (v >= 1) return v.toFixed(4)
  return v.toPrecision(4)
}

interface PageProps {
  ws?: any
}

export default function TradingPage({ ws }: PageProps) {
  const [positions, setPositions] = useState<PaperPosition[]>([])
  const [loading, setLoading] = useState(true)
  const [sortBy, setSortBy] = useState<SortField>('pnl')
  const [sortAsc, setSortAsc] = useState(false)
  const [detailTarget, setDetailTarget] = useState<PaperPosition | null>(null)
  const [paperAccountId, setPaperAccountId] = useState<number>(5)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)
  const [selectedSymbol, setSelectedSymbol] = useState('BTC')
  const [klinePeriod, setKlinePeriod] = useState('1h')
  const [customSymbol, setCustomSymbol] = useState('')
  const { data: klineData, loading: klineLoading, lastUpdated, refetch: refetchKlines } = useKlines(selectedSymbol, klinePeriod)
  const { symbols: configuredSymbols, exchangeSymbols } = useTradingPairs()

  // Merge: configured + exchange available, deduplicated
  const availableSymbols = (() => {
    const seen = new Set<string>()
    const result: string[] = []
    for (const s of [...configuredSymbols, ...exchangeSymbols]) {
      const upper = s.toUpperCase()
      if (!seen.has(upper)) { seen.add(upper); result.push(upper) }
    }
    return result
  })()

  const loadPositions = useCallback(async (aid: number) => {
    try {
      const data = await getPaperPositions(aid)
      const open = (data || []).filter(p => p.status === 'open')
      setPositions(open)
      setLastRefresh(new Date())
    } catch (e) {
      console.error('[Trading] load positions failed:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let mounted = true
    async function loadPaperAccountId() {
      try {
        const sessions = await getSessions()
        const active = (sessions as FullAutoSession[]).find(s =>
          ['running', 'defensive', 'paused'].includes(s.status)
        )
        if (active && mounted) {
          const aid = (active as any).paper_account_id || (active as any).trading_account_id || 5
          setPaperAccountId(aid)
        }
      } catch {}
    }
    loadPaperAccountId()
  }, [])

  useEffect(() => {
    if (!paperAccountId) return
    loadPositions(paperAccountId)
    const iv = setInterval(() => loadPositions(paperAccountId), 8000)
    return () => clearInterval(iv)
  }, [paperAccountId, loadPositions])

  // Calculate totals
  const totalPnl = positions.reduce((sum, p) => sum + p.unrealized_pnl, 0)
  const totalMargin = positions.reduce((sum, p) => sum + (p.margin || 0), 0)

  // Sort
  const sorted = [...positions].sort((a, b) => {
    const aPnlPct = a.entry_price > 0
      ? (a.side === 'long' ? (a.mark_price - a.entry_price) / a.entry_price : (a.entry_price - a.mark_price) / a.entry_price) * 100
      : 0
    const bPnlPct = b.entry_price > 0
      ? (b.side === 'long' ? (b.mark_price - b.entry_price) / b.entry_price : (b.entry_price - b.mark_price) / b.entry_price) * 100
      : 0
    const mod = sortAsc ? 1 : -1
    switch (sortBy) {
      case 'pnl': return (a.unrealized_pnl - b.unrealized_pnl) * mod
      case 'pnl_pct': return (aPnlPct - bPnlPct) * mod
      case 'size': return (a.size - b.size) * mod
      default: return a.symbol.localeCompare(b.symbol) * mod
    }
  })

  const toggleSort = (field: SortField) => {
    if (sortBy === field) setSortAsc(!sortAsc)
    else { setSortBy(field); setSortAsc(false) }
  }

  return (
    <div className="p-4 space-y-3">
      {/* ── Symbol Selector ── */}
      <div className="flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-hide">
        {availableSymbols.slice(0, 30).map((sym: string) => (
          <button
            key={sym}
            onClick={() => setSelectedSymbol(sym)}
            className={`flex-shrink-0 px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
              selectedSymbol === sym ? 'bg-terminal-primary text-white' : 'bg-terminal-card text-terminal-muted'
            }`}
          >
            {sym}
          </button>
        ))}
        {/* Custom symbol input */}
        <form
          onSubmit={(e) => {
            e.preventDefault()
            const s = customSymbol.trim().toUpperCase()
            if (s && s.length >= 2) setSelectedSymbol(s)
            setCustomSymbol('')
          }}
          className="flex-shrink-0 flex items-center"
        >
          <input
            type="text"
            value={customSymbol}
            onChange={(e) => setCustomSymbol(e.target.value.toUpperCase())}
            placeholder="自定义..."
            className="w-20 bg-terminal-bg border border-dashed border-terminal-border rounded-full px-3 py-1.5 text-xs text-terminal-text placeholder-terminal-muted/40 focus:outline-none focus:border-terminal-primary"
          />
        </form>
      </div>

      {/* ── Kline Chart ── */}
      <TradingKlineChart
        data={klineData}
        loading={klineLoading}
        symbol={selectedSymbol}
        period={klinePeriod}
        onPeriodChange={setKlinePeriod}
        lastUpdated={lastUpdated}
        onRefresh={refetchKlines}
      />

      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">模拟持仓</h2>
        <div className="flex items-center gap-2">
          {lastRefresh && (
            <span className="text-[10px] text-terminal-muted">
              更新: {lastRefresh.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          )}
          <span className="text-xs text-terminal-muted">{positions.length} 个</span>
        </div>
      </div>

      {/* Summary bar */}
      {positions.length > 0 && (
        <div className="card grid grid-cols-2 gap-3">
          <div>
            <p className="text-xs text-terminal-muted">持仓总盈亏</p>
            <p className={`font-mono font-bold ${totalPnl >= 0 ? 'text-terminal-profit' : 'text-terminal-loss'}`}>
              {totalPnl >= 0 ? '+' : ''}${totalPnl.toLocaleString('en-US', { maximumFractionDigits: 0 })}
            </p>
          </div>
          <div>
            <p className="text-xs text-terminal-muted">持仓保证金</p>
            <p className="font-mono">${totalMargin.toLocaleString('en-US', { maximumFractionDigits: 0 })}</p>
          </div>
        </div>
      )}

      {/* Sort controls */}
      {positions.length > 0 && (
        <div className="flex gap-2 text-xs">
          {([
            { key: 'pnl' as SortField, label: 'PnL' },
            { key: 'size' as SortField, label: '数量' },
            { key: 'symbol' as SortField, label: '品种' },
          ]).map(f => (
            <button key={f.key} onClick={() => toggleSort(f.key)}
              className={`px-2 py-1 rounded ${sortBy === f.key ? 'bg-terminal-primary text-white' : 'text-terminal-muted bg-terminal-card'}`}>
              {f.label}{sortBy === f.key ? (sortAsc ? '↑' : '↓') : ''}
            </button>
          ))}
        </div>
      )}

      {/* Positions List */}
      {loading ? (
        <div className="card text-center text-terminal-muted py-12"><p>加载中...</p></div>
      ) : positions.length === 0 ? (
        <div className="card text-center text-terminal-muted py-12">
          <p>暂无活跃持仓</p>
          <p className="text-xs mt-1">在自动 Tab 启动 FullAuto 会话</p>
        </div>
      ) : (
        <div className="space-y-2">
          {sorted.map(p => {
            const pnlPct = p.entry_price > 0
              ? (p.side === 'long' ? (p.mark_price - p.entry_price) / p.entry_price : (p.entry_price - p.mark_price) / p.entry_price) * 100
              : 0
            const isProfit = p.unrealized_pnl >= 0
            return (
              <div
                key={p.id}
                onClick={() => setDetailTarget(p)}
                className={`card border-l-4 cursor-pointer active:opacity-80 ${isProfit ? 'border-l-terminal-profit' : 'border-l-terminal-loss'}`}
              >
                {/* Row 1: Symbol + Side/Tier + PnL */}
                <div className="flex justify-between items-start">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-base">{p.symbol}</span>
                    <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${
                      p.side === 'long' ? 'bg-green-900/40 text-green-400' : 'bg-red-900/40 text-red-400'
                    }`}>
                      {p.side === 'long' ? 'Long' : 'Short'} {p.leverage}x
                    </span>
                    <span className="text-xs px-1.5 py-0.5 rounded bg-blue-900/30 text-blue-400">
                      {p.timeframe_tier || '-'}
                    </span>
                  </div>
                  <div className="text-right">
                    <p className={`font-mono font-bold text-sm ${isProfit ? 'text-terminal-profit' : 'text-terminal-loss'}`}>
                      {isProfit ? '+' : ''}${p.unrealized_pnl.toLocaleString('en-US', { maximumFractionDigits: 0 })}
                    </p>
                    <p className={`text-xs font-mono ${isProfit ? 'text-terminal-profit/70' : 'text-terminal-loss/70'}`}>
                      {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                    </p>
                  </div>
                </div>
                {/* Row 2: Entry / Mark / Size */}
                <div className="flex gap-4 mt-2 text-xs text-terminal-muted">
                  <span>入场 ${formatPrice(p.entry_price)}</span>
                  <span className={p.mark_price >= p.entry_price ? 'text-terminal-profit' : 'text-terminal-loss'}>
                    标记 ${formatPrice(p.mark_price)}
                  </span>
                  <span>{p.trade_nature || ''}</span>
                </div>
                {/* Row 3: TP / SL */}
                {(p.tp_price || p.sl_price) && (
                  <div className="flex gap-4 mt-1 text-xs">
                    {p.tp_price > 0 && <span className="text-terminal-profit">TP ${formatPrice(p.tp_price)}</span>}
                    {p.sl_price > 0 && <span className="text-terminal-loss">SL ${formatPrice(p.sl_price)}</span>}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Detail BottomSheet */}
      <BottomSheet open={!!detailTarget} onClose={() => setDetailTarget(null)} title="持仓详情">
        {detailTarget && (() => {
          const p = detailTarget
          const pnlPct = p.entry_price > 0
            ? (p.side === 'long' ? (p.mark_price - p.entry_price) / p.entry_price : (p.entry_price - p.mark_price) / p.entry_price) * 100
            : 0
          const isProfit = p.unrealized_pnl >= 0
          return (
            <div className="space-y-3">
              <div className="text-center">
                <p className="text-xl font-bold">{p.symbol}</p>
                <div className="flex justify-center gap-2 mt-1">
                  <span className={`text-xs px-2 py-0.5 rounded ${p.side === 'long' ? 'bg-green-900/40 text-green-400' : 'bg-red-900/40 text-red-400'}`}>
                    {p.side === 'long' ? 'Long' : 'Short'} {p.leverage}x
                  </span>
                  <span className="text-xs px-2 py-0.5 rounded bg-blue-900/30 text-blue-400">
                    {p.timeframe_tier}
                  </span>
                  {p.trade_nature && (
                    <span className="text-xs px-2 py-0.5 rounded bg-gray-800 text-gray-400">
                      {p.trade_nature}
                    </span>
                  )}
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <p className="text-xs text-terminal-muted">入场价</p>
                  <p className="font-mono">${formatPrice(p.entry_price)}</p>
                </div>
                <div>
                  <p className="text-xs text-terminal-muted">标记价</p>
                  <p className={`font-mono ${p.mark_price >= p.entry_price ? 'text-terminal-profit' : 'text-terminal-loss'}`}>
                    ${formatPrice(p.mark_price)}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-terminal-muted">持有数量</p>
                  <p className="font-mono">{p.size.toLocaleString('en-US', { maximumFractionDigits: 2 })}</p>
                </div>
                <div>
                  <p className="text-xs text-terminal-muted">保证金</p>
                  <p className="font-mono">${(p.margin || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}</p>
                </div>
                <div>
                  <p className="text-xs text-terminal-muted">未实现盈亏</p>
                  <p className={`font-mono font-bold ${isProfit ? 'text-terminal-profit' : 'text-terminal-loss'}`}>
                    {isProfit ? '+' : ''}${p.unrealized_pnl.toLocaleString('en-US', { maximumFractionDigits: 0 })}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-terminal-muted">盈亏%</p>
                  <p className={`font-mono font-bold ${isProfit ? 'text-terminal-profit' : 'text-terminal-loss'}`}>
                    {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                  </p>
                </div>
              </div>
              {(p.tp_price > 0 || p.sl_price > 0 || p.liquidation_price) && (
                <div className="p-2 rounded bg-terminal-card/50 space-y-1 text-xs">
                  {p.tp_price > 0 && (
                    <div className="flex justify-between">
                      <span className="text-terminal-muted">止盈 TP</span>
                      <span className="text-terminal-profit font-mono">${formatPrice(p.tp_price)}</span>
                    </div>
                  )}
                  {p.sl_price > 0 && (
                    <div className="flex justify-between">
                      <span className="text-terminal-muted">止损 SL</span>
                      <span className="text-terminal-loss font-mono">${formatPrice(p.sl_price)}</span>
                    </div>
                  )}
                  {p.liquidation_price && (
                    <div className="flex justify-between">
                      <span className="text-terminal-muted">清算价</span>
                      <span className="text-red-600 font-mono">${formatPrice(p.liquidation_price)}</span>
                    </div>
                  )}
                </div>
              )}
              <TouchButton variant="ghost" fullWidth onClick={() => setDetailTarget(null)}>关闭</TouchButton>
            </div>
          )
        })()}
      </BottomSheet>
    </div>
  )
}
