import React, { useState, useEffect, useCallback } from 'react'
import TouchButton from '@/components/ui/TouchButton'
import Badge from '@/components/ui/Badge'
import BottomSheet from '@/components/ui/BottomSheet'
import { getSessions, getSessionStatus, startSession, pauseSession, resumeSession, stopSession, deleteSession } from '@/api/fullauto'
import { listAccounts, type AccountListItem } from '@/api/accounts'
import { useTradingPairs } from '@/hooks/useTradingPairs'
import type { FullAutoSession } from '@/api/types'

interface PageProps {
  ws?: any
}

const STATUS_MAP: Record<string, { variant: 'active' | 'defensive' | 'paused' | 'stopped'; label: string; color: string; dotClass: string }> = {
  running: { variant: 'active', label: '运行中', color: 'text-terminal-profit', dotClass: 'bg-terminal-profit' },
  defensive: { variant: 'defensive', label: '防守', color: 'text-terminal-warning', dotClass: 'bg-terminal-warning animate-pulse' },
  paused: { variant: 'paused', label: '已暂停', color: 'text-terminal-warning', dotClass: 'bg-terminal-warning' },
  stopped: { variant: 'stopped', label: '已停止', color: 'text-terminal-muted', dotClass: 'bg-terminal-muted' },
}

const RISK_MODES = [
  { key: 'ai_dynamic', label: 'AI 动态', desc: 'AI 自动调整风险' },
  { key: 'conservative', label: '偏保守', desc: '低杠杆少策略' },
  { key: 'aggressive', label: '偏激进', desc: '高杠杆多策略' },
]

export default function FullAutoPage({ ws }: PageProps) {
  // ── State ──
  const { symbols: configuredSymbols, exchangeSymbols } = useTradingPairs()
  const [sessions, setSessions] = useState<FullAutoSession[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [sessionDetail, setSessionDetail] = useState<FullAutoSession | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const [showDelete, setShowDelete] = useState<string | null>(null)
  const [acting, setActing] = useState(false)
  const [confirm, setConfirm] = useState<{ action: string; fn: () => Promise<any> } | null>(null)
  const [showAdvanced, setShowAdvanced] = useState(false)

  // Create form
  const [accts, setAccts] = useState<AccountListItem[]>([])
  const [selAccountId, setSelAccountId] = useState<number>(0)
  const [selPaperId, setSelPaperId] = useState<number>(0)
  const [selSymbols, setSelSymbols] = useState<string[]>(['BTC', 'ETH'])
  const [selRiskMode, setSelRiskMode] = useState('ai_dynamic')
  const [selTradingMode, setSelTradingMode] = useState('paper')
  const [selAutoCoin, setSelAutoCoin] = useState(false)
  const [advMaxStrats, setAdvMaxStrats] = useState('25')
  const [advDrawdown, setAdvDrawdown] = useState('30')
  const [advDailyLoss, setAdvDailyLoss] = useState('5')
  const [advHealthInterval, setAdvHealthInterval] = useState('300')
  const [advMinLifetime, setAdvMinLifetime] = useState('7')
  const [advConsecutiveLoss, setAdvConsecutiveLoss] = useState('5')

  // ── Load ──
  const loadSessions = useCallback(async () => {
    try {
      const data = await getSessions()
      setSessions(data || [])
      if (!selectedId && data?.length > 0) {
        const active = data.find((s: FullAutoSession) => ['running', 'defensive', 'paused'].includes(s.status))
        if (active) setSelectedId(active.session_id)
        else if (data[0]) setSelectedId(data[0].session_id)
      }
    } catch (e) {
      console.error('[FullAuto] load sessions:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  const loadDetail = useCallback(async (sid: string) => {
    setDetailLoading(true)
    try {
      const detail = await getSessionStatus(sid)
      setSessionDetail(detail)
    } catch (e) {
      console.error('[FullAuto] load detail:', e)
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const loadAccts = useCallback(async () => {
    try {
      const data = await listAccounts()
      setAccts(data || [])
      const aiAccts = (data || []).filter((a: AccountListItem) => a.account_type === 'AI')
      const paperAccts = (data || []).filter((a: AccountListItem) => a.trading_mode === 'paper' || a.account_type === 'PAPER')
      if (aiAccts.length > 0 && !selAccountId) setSelAccountId(aiAccts[0].id)
      if (paperAccts.length > 0 && !selPaperId) setSelPaperId(paperAccts[0].id)
    } catch (e) {
      console.error('[FullAuto] load accounts:', e)
    }
  }, [])

  useEffect(() => { loadSessions() }, [])
  useEffect(() => {
    if (selectedId) loadDetail(selectedId)
    else setSessionDetail(null)
  }, [selectedId, loadDetail])
  useEffect(() => {
    const iv = setInterval(loadSessions, 15000)
    return () => clearInterval(iv)
  }, [loadSessions])

  // ── Handlers ──
  const doAction = async (fn: () => Promise<void>) => {
    setActing(true)
    try {
      await fn()
      await loadSessions()
      if (selectedId) await loadDetail(selectedId)
      try { ws?.getSnapshot() } catch {}
    } catch (e: any) { alert(e.message || '操作失败') }
    finally { setActing(false); setConfirm(null) }
  }

  const handleSelectSession = (sid: string) => {
    setSelectedId(sid)
    setSessionDetail(null)
    // loadDetail called by useEffect
  }

  const handleCreate = async () => {
    if (!selAccountId) { alert('请选择 AI 交易员账户'); return }
    if (selSymbols.length === 0) { alert('请至少选择一个交易对'); return }
    setActing(true)
    try {
      const body: any = {
        account_id: selAccountId,
        symbols: selSymbols,
        risk_mode: selRiskMode,
        trading_mode: selTradingMode,
        auto_coin_enabled: selAutoCoin,
      }
      if (selPaperId && selTradingMode === 'paper') body.paper_account_id = selPaperId
      if (showAdvanced) {
        if (advMaxStrats) body.max_concurrent_strategies = parseInt(advMaxStrats)
        if (advDrawdown) body.max_total_drawdown_pct = parseFloat(advDrawdown)
        if (advDailyLoss) body.daily_loss_limit_pct = parseFloat(advDailyLoss)
        if (advHealthInterval) body.health_check_interval = parseInt(advHealthInterval)
        if (advMinLifetime) body.min_strategy_lifetime_days = parseInt(advMinLifetime)
        if (advConsecutiveLoss) body.consecutive_loss_elimination = parseInt(advConsecutiveLoss)
      }
      const result = await startSession(body)
      setShowCreate(false)
      setSelectedId(result.session_id || null)
      await loadSessions()
    } catch (e: any) { alert(e.message || '启动失败') }
    finally { setActing(false) }
  }

  const handleDelete = async (sid: string) => {
    setActing(true)
    try {
      await deleteSession(sid)
      setShowDelete(null)
      if (selectedId === sid) setSelectedId(null)
      await loadSessions()
    } catch (e: any) { alert(e.message || '删除失败') }
    finally { setActing(false) }
  }

  const toggleSymbol = (sym: string) => {
    setSelSymbols(prev => prev.includes(sym) ? prev.filter(s => s !== sym) : [...prev, sym])
  }

  // ── Derived ──
  const detail = sessionDetail
  const statusInfo = detail ? STATUS_MAP[detail.status] : null
  const aiAccounts = accts.filter(a => a.account_type === 'AI')
  const paperAccounts = accts.filter(a => a.trading_mode === 'paper' || a.account_type === 'PAPER')

  // Merge configured + exchange symbols for selection
  const availableSymbols = (() => {
    const seen = new Set<string>()
    const result: string[] = []
    for (const s of [...configuredSymbols, ...exchangeSymbols]) {
      const upper = s.toUpperCase()
      if (!seen.has(upper)) { seen.add(upper); result.push(upper) }
    }
    return result
  })()

  // ── Runtime ──
  const getRuntime = (s: FullAutoSession) => {
    if (!s.started_at) return '-'
    const start = new Date(s.started_at).getTime()
    const end = s.stopped_at ? new Date(s.stopped_at).getTime() : Date.now()
    const diff = Math.max(0, end - start)
    const h = Math.floor(diff / 3600000)
    const m = Math.floor((diff % 3600000) / 60000)
    return `${h}h ${m}m`
  }

  // ── Render ──
  return (
    <div className="flex flex-col h-full">
      {/* ── Session Bar ── */}
      <div className="flex-shrink-0 px-4 pt-3 pb-2">
        <div className="flex items-center gap-2 overflow-x-auto pb-1 scrollbar-hide">
          {loading ? (
            <span className="text-xs text-terminal-muted">加载中...</span>
          ) : (
            sessions.map(s => {
              const st = STATUS_MAP[s.status] || STATUS_MAP.stopped
              const isActive = s.session_id === selectedId
              return (
                <button
                  key={s.session_id}
                  onClick={() => handleSelectSession(s.session_id)}
                  className={`flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium whitespace-nowrap transition-colors ${
                    isActive ? 'bg-terminal-primary text-white' : 'bg-terminal-card text-terminal-text hover:bg-terminal-border'
                  }`}
                >
                  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${st.dotClass}`} />
                  <span className="max-w-[80px] truncate">{s.account_name || `#${s.account_id}`}</span>
                  <span className="opacity-60">·{s.symbols?.length || 0}</span>
                </button>
              )
            })
          )}
          <button
            onClick={() => { loadAccts(); setShowCreate(true) }}
            className="flex-shrink-0 flex items-center gap-1 px-3 py-1.5 rounded-full text-xs font-medium bg-terminal-card text-terminal-primary border border-dashed border-terminal-primary/50 active:opacity-70"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></svg>
            新建
          </button>
        </div>
      </div>

      {/* ── Content ── */}
      <div className="flex-1 overflow-y-auto px-4 pb-4 space-y-3">
        {loading ? (
          <div className="card text-center text-terminal-muted py-12">加载中...</div>
        ) : sessions.length === 0 ? (
          <div className="card text-center py-10">
            <p className="text-terminal-muted text-lg mb-1">暂无会话</p>
            <p className="text-xs text-terminal-muted mb-4">创建第一个全自动交易会话</p>
            <TouchButton variant="primary" onClick={() => { loadAccts(); setShowCreate(true) }}>
              启动全自动交易
            </TouchButton>
          </div>
        ) : !selectedId ? (
          <div className="card text-center text-terminal-muted py-12">请选择或创建一个会话</div>
        ) : detailLoading ? (
          <div className="card text-center text-terminal-muted py-12">加载会话详情...</div>
        ) : !detail ? (
          <div className="card text-center text-terminal-muted py-12">会话数据加载失败</div>
        ) : (
          <>
            {/* Header */}
            <div className="card">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <div className={`w-3 h-3 rounded-full ${statusInfo?.dotClass}`} />
                  <span className={`font-semibold ${statusInfo?.color}`}>{statusInfo?.label}</span>
                  {statusInfo && <Badge variant={statusInfo.variant}>{detail.status}</Badge>}
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-terminal-muted">{getRuntime(detail)}</span>
                  <button
                    onClick={() => setShowDelete(detail.session_id)}
                    className="p-1 text-terminal-muted hover:text-terminal-loss active:opacity-70"
                    title="删除会话"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
                  </button>
                </div>
              </div>
              <p className="text-[10px] text-terminal-muted mb-3 font-mono">{detail.session_id}</p>

              {/* Stats Grid */}
              <div className="grid grid-cols-3 gap-3 text-sm">
                <div>
                  <p className="text-xs text-terminal-muted">总权益</p>
                  <p className="font-mono font-semibold">
                    ${((detail as any).peak_balance || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-terminal-muted">总盈亏</p>
                  <p className={`font-mono font-semibold ${(detail.total_pnl || 0) >= 0 ? 'text-terminal-profit' : 'text-terminal-loss'}`}>
                    {(detail.total_pnl || 0) >= 0 ? '+' : '-'}${Math.abs(detail.total_pnl || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-terminal-muted">总交易</p>
                  <p className="font-mono">{detail.total_trades || 0}</p>
                </div>
                <div>
                  <p className="text-xs text-terminal-muted">活跃策略</p>
                  <p className="font-mono">{detail.active_count ?? (detail as any)?.active_strategy_ids?.length ?? 0}</p>
                </div>
                <div>
                  <p className="text-xs text-terminal-muted">累计创建</p>
                  <p className="font-mono">{detail.total_strategies_created ?? '-'}</p>
                </div>
                <div>
                  <p className="text-xs text-terminal-muted">胜率</p>
                  <p className="font-mono">{detail.win_rate != null ? `${detail.win_rate.toFixed(1)}%` : '-'}</p>
                </div>
                <div>
                  <p className="text-xs text-terminal-muted">当前回撤</p>
                  <p className={`font-mono ${(detail.current_drawdown || 0) > 0.1 ? 'text-terminal-loss' : ''}`}>
                    {((detail.current_drawdown || 0) * 100).toFixed(2)}%
                  </p>
                </div>
                <div>
                  <p className="text-xs text-terminal-muted">熔断线</p>
                  <p className="font-mono text-terminal-warning">{detail.max_total_drawdown_pct ?? 30}%</p>
                </div>
                <div>
                  <p className="text-xs text-terminal-muted">交易对</p>
                  <p className="font-mono text-[10px] leading-tight">{(detail.symbols || []).join(',') || '-'}</p>
                </div>
              </div>

              {/* Controls */}
              <div className="flex gap-3 mt-4">
                {detail.status === 'running' && (
                  <>
                    <TouchButton variant="ghost" fullWidth loading={acting}
                      onClick={() => setConfirm({ action: '暂停', fn: () => pauseSession(detail.session_id) })}>
                      暂停
                    </TouchButton>
                    <TouchButton variant="danger" fullWidth loading={acting}
                      onClick={() => setConfirm({ action: '停止', fn: () => stopSession(detail.session_id) })}>
                      停止
                    </TouchButton>
                  </>
                )}
                {detail.status === 'paused' && (
                  <>
                    <TouchButton variant="success" fullWidth loading={acting}
                      onClick={() => setConfirm({ action: '恢复', fn: () => resumeSession(detail.session_id) })}>
                      恢复
                    </TouchButton>
                    <TouchButton variant="danger" fullWidth loading={acting}
                      onClick={() => setConfirm({ action: '停止', fn: () => stopSession(detail.session_id) })}>
                      停止
                    </TouchButton>
                  </>
                )}
                {detail.status === 'defensive' && (
                  <TouchButton variant="danger" fullWidth loading={acting}
                    onClick={() => setConfirm({ action: '停止', fn: () => stopSession(detail.session_id) })}>
                    停止
                  </TouchButton>
                )}
                {detail.status === 'stopped' && (
                  <TouchButton variant="ghost" fullWidth
                    onClick={() => setShowDelete(detail.session_id)}>
                    删除记录
                  </TouchButton>
                )}
              </div>
            </div>

            {/* Risk Status */}
            {detail.symbols && detail.symbols.length > 0 && (
              <div className="card">
                <p className="text-xs text-terminal-muted mb-2 font-semibold">▸ 风控状态</p>
                <div className="space-y-1.5 text-xs">
                  {detail.symbols.map(sym => {
                    const drawdown = detail.current_drawdown || 0
                    const isHigh = drawdown > 0.15
                    const isWarn = drawdown > 0.08
                    return (
                      <div key={sym} className="flex items-center gap-2">
                        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
                          isHigh ? 'bg-terminal-loss animate-pulse' : isWarn ? 'bg-terminal-warning' : 'bg-terminal-profit'
                        }`} />
                        <span className="text-terminal-text">{sym}</span>
                        <span className="text-terminal-muted flex-1 text-right">
                          {isHigh ? '⚠ 高风险' : isWarn ? '注意' : '正常'}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {/* Event Log */}
            {((detail as any)?.events?.length > 0 || (detail as any)?.event_log?.length > 0) && (() => {
              const events = (detail as any).events || (detail as any).event_log || []
              return (
                <div>
                  <p className="text-xs text-terminal-muted mb-2 font-semibold">▸ 最近事件</p>
                  <div className="space-y-1.5">
                    {events.slice(0, 20).map((ev: any, i: number) => {
                      const evType = ev.type || ev.event || ''
                      const borderColor =
                        evType === 'circuit_breaker' ? 'border-l-terminal-loss' :
                        evType === 'defensive_exit' || evType === 'strategy_created' ? 'border-l-terminal-profit' :
                        evType?.includes('error') ? 'border-l-terminal-loss' :
                        evType?.includes('warning') || evType?.includes('pause') ? 'border-l-yellow-500' :
                        'border-l-terminal-border'
                      const time = ev.timestamp || ev.time || ''
                      const detail = ev.message || ev.detail || ''
                      return (
                        <div key={i} className={`card py-1.5 px-3 border-l-4 ${borderColor}`}>
                          <div className="flex gap-2">
                            <p className="text-[10px] text-terminal-muted flex-shrink-0 font-mono">
                              {time ? new Date(time).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : ''}
                            </p>
                            <p className="text-xs break-all">{detail}</p>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })()}
          </>
        )}
      </div>

      {/* ── Create Session BottomSheet ── */}
      <BottomSheet open={showCreate} onClose={() => setShowCreate(false)} title="新建全自动会话">
        <div className="space-y-4 max-h-[70vh] overflow-y-auto">
          {/* AI Trader */}
          <div>
            <label className="text-xs text-terminal-muted block mb-1">AI 交易员</label>
            {aiAccounts.length > 0 ? (
              <select
                value={selAccountId || ''}
                onChange={(e) => setSelAccountId(parseInt(e.target.value))}
                className="w-full bg-terminal-bg border border-terminal-border rounded px-3 py-2 text-sm text-terminal-text focus:outline-none focus:border-terminal-primary"
              >
                {aiAccounts.map(a => (
                  <option key={a.id} value={a.id}>{a.name} ({a.model || 'AI'})</option>
                ))}
              </select>
            ) : (
              <p className="text-xs text-terminal-warning">请先在「账户」Tab 创建 AI 交易员</p>
            )}
          </div>

          {/* Paper Account */}
          {selTradingMode === 'paper' && (
            <div>
              <label className="text-xs text-terminal-muted block mb-1">模拟账户</label>
              {paperAccounts.length > 0 ? (
                <select
                  value={selPaperId || ''}
                  onChange={(e) => setSelPaperId(parseInt(e.target.value))}
                  className="w-full bg-terminal-bg border border-terminal-border rounded px-3 py-2 text-sm text-terminal-text focus:outline-none focus:border-terminal-primary"
                >
                  {paperAccounts.map(a => (
                    <option key={a.id} value={a.id}>{a.name}</option>
                  ))}
                </select>
              ) : (
                <p className="text-xs text-terminal-warning">请先在「账户」Tab 创建模拟账户</p>
              )}
            </div>
          )}

          {/* Mode */}
          <div>
            <label className="text-xs text-terminal-muted block mb-1">交易模式</label>
            <div className="flex gap-2">
              {(['paper', 'live'] as const).map(mode => (
                <button
                  key={mode}
                  onClick={() => { setSelTradingMode(mode); if (mode === 'live') setSelPaperId(0) }}
                  className={`flex-1 py-2 rounded text-sm font-medium border ${
                    selTradingMode === mode ? 'bg-terminal-primary/20 border-terminal-primary text-terminal-primary' : 'bg-terminal-bg border-terminal-border text-terminal-muted'
                  }`}
                >
                  {{ paper: '模拟盘', live: '实盘' }[mode]}
                </button>
              ))}
            </div>
          </div>

          {/* Symbols */}
          <div>
            <label className="text-xs text-terminal-muted block mb-1">
              交易对 <span className="text-terminal-muted/50">({selSymbols.length} 个)</span>
            </label>
            <div className="flex flex-wrap gap-1.5">
              {availableSymbols.slice(0, 40).map((sym: string) => {
                const sel = selSymbols.includes(sym)
                return (
                  <button
                    key={sym}
                    onClick={() => toggleSymbol(sym)}
                    className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                      sel ? 'bg-terminal-primary text-white' : 'bg-terminal-bg border border-terminal-border text-terminal-muted'
                    }`}
                  >
                    {sym}
                  </button>
                )
              })}
            </div>
          </div>

          {/* Risk Mode */}
          <div>
            <label className="text-xs text-terminal-muted block mb-1">风险模式</label>
            <div className="space-y-1.5">
              {RISK_MODES.map(rm => (
                <button
                  key={rm.key}
                  onClick={() => setSelRiskMode(rm.key)}
                  className={`w-full text-left px-3 py-2 rounded border text-sm ${
                    selRiskMode === rm.key ? 'bg-terminal-primary/10 border-terminal-primary text-terminal-text' : 'bg-terminal-bg border-terminal-border text-terminal-muted'
                  }`}
                >
                  <span className="font-medium">{rm.label}</span>
                  <span className="text-xs ml-2 opacity-60">{rm.desc}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Auto Coin */}
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm">AI 自动选币</p>
              <p className="text-xs text-terminal-muted">AI 自动发现交易机会</p>
            </div>
            <button
              onClick={() => setSelAutoCoin(!selAutoCoin)}
              className={`w-10 h-6 rounded-full relative transition-colors ${selAutoCoin ? 'bg-terminal-primary' : 'bg-terminal-border'}`}
            >
              <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform ${selAutoCoin ? 'left-[18px]' : 'left-[2px]'}`} />
            </button>
          </div>

          {/* Advanced Config */}
          <div>
            <button
              onClick={() => setShowAdvanced(!showAdvanced)}
              className="flex items-center gap-1 text-xs text-terminal-muted active:opacity-70"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                className={`transition-transform ${showAdvanced ? 'rotate-90' : ''}`}
              ><polyline points="9 18 15 12 9 6" /></svg>
              高级配置
            </button>
            {showAdvanced && (
              <div className="mt-2 space-y-3 p-3 bg-terminal-bg rounded border border-terminal-border">
                {[
                  { label: '最大策略数', value: advMaxStrats, setter: setAdvMaxStrats, hint: 'Max concurrent strategies' },
                  { label: '总回撤熔断 (%)', value: advDrawdown, setter: setAdvDrawdown, hint: 'Max total drawdown' },
                  { label: '日亏损限制 (%)', value: advDailyLoss, setter: setAdvDailyLoss, hint: 'Daily loss limit' },
                  { label: '健康检查间隔 (秒)', value: advHealthInterval, setter: setAdvHealthInterval, hint: 'Health check interval' },
                  { label: '策略最小寿命 (天)', value: advMinLifetime, setter: setAdvMinLifetime, hint: 'Min strategy lifetime' },
                  { label: '连续亏损淘汰 (次)', value: advConsecutiveLoss, setter: setAdvConsecutiveLoss, hint: 'Consecutive loss elimination' },
                ].map(f => (
                  <div key={f.label} className="flex items-center justify-between">
                    <div>
                      <p className="text-xs text-terminal-text">{f.label}</p>
                      <p className="text-[10px] text-terminal-muted">{f.hint}</p>
                    </div>
                    <input
                      type="number"
                      value={f.value}
                      onChange={(e) => f.setter(e.target.value)}
                      className="w-20 bg-terminal-card border border-terminal-border rounded px-2 py-1 text-xs text-right text-terminal-text focus:outline-none focus:border-terminal-primary"
                    />
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Submit */}
          <TouchButton variant="primary" fullWidth loading={acting} onClick={handleCreate}>
            {acting ? '启动中...' : '启动全自动交易'}
          </TouchButton>
        </div>
      </BottomSheet>

      {/* ── Confirm BottomSheet ── */}
      <BottomSheet open={!!confirm} onClose={() => setConfirm(null)} title={`确认${confirm?.action}`}>
        <div className="space-y-4">
          <p className="text-terminal-muted text-center">确定要{confirm?.action}会话吗？</p>
          <div className="flex gap-3">
            <TouchButton variant="ghost" fullWidth onClick={() => setConfirm(null)}>取消</TouchButton>
            <TouchButton variant="danger" fullWidth loading={acting}
              onClick={() => confirm && doAction(confirm.fn)}>
              确认{confirm?.action}
            </TouchButton>
          </div>
        </div>
      </BottomSheet>

      {/* ── Delete Confirm BottomSheet ── */}
      <BottomSheet open={!!showDelete} onClose={() => setShowDelete(null)} title="删除会话">
        <div className="space-y-4">
          <p className="text-terminal-muted text-center">
            确定要删除此会话吗？此操作不可撤销，所有关联数据将丢失。
          </p>
          <div className="flex gap-3">
            <TouchButton variant="ghost" fullWidth onClick={() => setShowDelete(null)}>取消</TouchButton>
            <TouchButton variant="danger" fullWidth loading={acting}
              onClick={() => showDelete && handleDelete(showDelete)}>
              确认删除
            </TouchButton>
          </div>
        </div>
      </BottomSheet>
    </div>
  )
}
