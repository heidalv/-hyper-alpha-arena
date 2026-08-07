/**
 * useArbitrageHubData — 套利中心主数据 hook（从 ArbitrageHubPage 拆出）
 *
 * 职责：
 *   - 并行拉取 V3 / Rebate / 积分 / 规则闸门 / Paper 会话等全部数据（30s 轮询）
 *   - 事件流增量合并（append + 去重 + 最近 50 条）
 *   - 通知队列（最近 10 条，10s 自动消失）
 *   - 全局 fetch 失败 toast（半数以上接口失败时提示，避免每 30s 重复刷屏）
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'react-hot-toast'
import {
  type ArbitrageStatus, type ArbitragePosition, type ArbitrageOpportunity,
  type RebateStatus, type RebateOpportunity, type RebatePosition,
  type RebateAnalytics, type CapitalAllocation, type WashTradeStatus,
  type ExchangeIncentiveSummary, type RuleSyncGateState,
  type StrategyConfigDetail, type RebateEvent, type PointsSummary,
  type ArbitragePaperSessionStatus,
  getArbitrageStatus, getArbitragePositions, getArbitrageOpportunities,
  getRebateStatus, getRebateOpportunities, getRebatePositions,
  getRebateCapital, getWashTradeStatus, getRebateAnalytics, getExchangeIncentives,
  getRuleSyncGate, getStrategyConfigs, getRebateEvents, getPointsSummary,
  formatRebateEventMessage, getArbitragePaperSession,
} from '@/lib/arbitrageApi'

export interface HubNotification {
  id: string
  type: string
  message: string
  ts: number
}

export function useArbitrageHubData(pageActive: boolean) {
  // ── Arbitrage (V3) state ──
  const [arbStatus, setArbStatus] = useState<ArbitrageStatus>({ engine_enabled: false, scanner_scan_count: 0, cached_opportunities: 0, circuit_breaker_active: false })
  const [arbPositions, setArbPositions] = useState<ArbitragePosition[]>([])
  const [arbOpps, setArbOpps] = useState<ArbitrageOpportunity[]>([])

  // ── Rebate state ──
  const [rebStatus, setRebStatus] = useState<RebateStatus>({ engine_enabled: false, mode: 'paper', scan_count: 0, execution_count: 0, active_positions: 0, total_rebate_pnl: 0, wash_trade_safe: true, next_safe_interval_sec: 0 })
  const [rebOpps, setRebOpps] = useState<RebateOpportunity[]>([])
  const [rebPositions, setRebPositions] = useState<RebatePosition[]>([])
  const [rebCapital, setRebCapital] = useState<CapitalAllocation>({ total_equity: 0, allocations: {}, used: {}, utilization: {}, rebate_available: 0, total_utilization_pct: 0 })
  const [washStatus, setWashStatus] = useState<WashTradeStatus>({ is_safe: true, next_safe_interval_sec: 0, daily_volume_usd: 0, last_trade_ts: 0, trade_count_today: 0, risk_level: 'low' })
  const [rebAnalytics, setRebAnalytics] = useState<RebateAnalytics>({ total_trades: 0, win_rate: 0, total_pnl: 0, total_rebate: 0, total_points: 0, net_pnl: 0, by_strategy: {} })
  const [incentives, setIncentives] = useState<ExchangeIncentiveSummary[]>([])

  // ── Extra tab state ──
  const [strategyConfigs, setStrategyConfigs] = useState<Record<string, StrategyConfigDetail>>({})
  const [events, setEvents] = useState<RebateEvent[]>([])
  const lastEventTs = useRef<number>(Date.now() / 1000)
  const [notifications, setNotifications] = useState<HubNotification[]>([])
  const [pointsSummary, setPointsSummary] = useState<PointsSummary | null>(null)
  const [ruleGate, setRuleGate] = useState<RuleSyncGateState | null>(null)
  const [paperSession, setPaperSession] = useState<ArbitragePaperSessionStatus | null>(null)

  const [loading, setLoading] = useState(false)
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date())
  // fetch 失败 toast 去抖：恢复正常前只提示一次
  const fetchFailToastShown = useRef(false)

  // ── Notification auto-dismiss ──
  useEffect(() => {
    if (notifications.length === 0) return
    const timers: ReturnType<typeof setTimeout>[] = []
    for (const n of notifications) {
      const elapsed = (Date.now() - n.ts) / 1000
      const remaining = Math.max(0, 10000 - elapsed * 1000)
      timers.push(setTimeout(() => {
        setNotifications(prev => prev.filter(x => x.id !== n.id))
      }, remaining))
    }
    return () => timers.forEach(clearTimeout)
  }, [notifications])

  // ── Data fetching ──
  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const results = await Promise.allSettled([
        getArbitrageStatus(),
        getArbitragePositions('active'),
        getArbitrageOpportunities(),
        getRebateStatus(),
        getRebateOpportunities(),
        getRebatePositions('active'),
        getRebateCapital(),
        getWashTradeStatus(),
        getRebateAnalytics(),
        getExchangeIncentives(),
        getStrategyConfigs(),
        getRebateEvents(lastEventTs.current),
        getPointsSummary(),
        getRuleSyncGate(),
        getArbitragePaperSession(),
      ] as const)
      const [
        stArb, posArb, oppsArb,
        stReb, oppsReb, posReb, capReb, wash, analytics, inc,
        cfgStrat, evData, ptsSum, gate, paperSess,
      ] = results

      // 全局 fetch 失败提示（半数以上失败视为后端不可用）
      const failed = results.filter(r => r.status === 'rejected').length
      if (failed > results.length / 2) {
        if (!fetchFailToastShown.current) {
          fetchFailToastShown.current = true
          toast.error('套利中心数据获取失败，请检查后端服务是否在运行')
        }
      } else {
        fetchFailToastShown.current = false
      }

      if (stArb.status === 'fulfilled') setArbStatus(stArb.value)
      if (posArb.status === 'fulfilled') setArbPositions(posArb.value?.positions ?? [])
      if (oppsArb.status === 'fulfilled') setArbOpps(oppsArb.value?.opportunities ?? [])
      if (stReb.status === 'fulfilled') setRebStatus(stReb.value)
      if (oppsReb.status === 'fulfilled') setRebOpps(oppsReb.value?.opportunities ?? [])
      if (posReb.status === 'fulfilled') setRebPositions(posReb.value?.positions ?? [])
      if (capReb.status === 'fulfilled') setRebCapital(capReb.value)
      if (wash.status === 'fulfilled') setWashStatus(wash.value)
      if (analytics.status === 'fulfilled') {
        const analyticsValue = analytics.value ?? ({} as RebateAnalytics)
        const analyticsDefault = {
          total_trades: 0, win_rate: 0, total_pnl: 0, total_rebate: 0,
          total_points: 0, net_pnl: 0, by_strategy: {},
        }
        setRebAnalytics({
          ...analyticsDefault,
          ...analyticsValue,
          by_strategy: analyticsValue.by_strategy ?? {},
        })
      }
      if (inc.status === 'fulfilled') setIncentives(inc.value?.exchanges ?? [])
      if (cfgStrat.status === 'fulfilled') setStrategyConfigs(cfgStrat.value)

      if (evData.status === 'fulfilled') {
        const { events: newEvents, latest_ts } = evData.value
        // P0 修复：增量 API 不能做全量替换（会把列表清空闪烁）。
        // 改为 append + 按 ts+type+message 去重 + 保留最近 50 条。
        if (newEvents.length > 0) {
          setEvents(prev => {
            const seen = new Set(prev.map(e => `${e.ts}-${e.type}-${(e as any).message ?? ''}`))
            const fresh = newEvents.filter(e => !seen.has(`${e.ts}-${e.type}-${(e as any).message ?? ''}`))
            return [...fresh, ...prev]
              .sort((a, b) => b.ts - a.ts)
              .slice(0, 50)
          })
        }
        if (latest_ts > 0) lastEventTs.current = latest_ts

        if (newEvents.length > 0) {
          const formatted: HubNotification[] = newEvents.map((ev, idx) => ({
            id: `${ev.ts}-${ev.type}-${idx}`,
            type: ev.type,
            message: formatRebateEventMessage(ev),
            ts: ev.ts * 1000,
          }))
          setNotifications(prev => {
            const merged = [...formatted, ...prev]
            return merged.slice(0, 10)
          })
        }
      }

      if (ptsSum.status === 'fulfilled') setPointsSummary(ptsSum.value)
      if (gate.status === 'fulfilled') setRuleGate(gate.value)
      if (paperSess.status === 'fulfilled') setPaperSession(paperSess.value)

      setLastRefresh(new Date())
    } catch (e) {
      console.error('[ArbHub] fetch error:', e)
      if (!fetchFailToastShown.current) {
        fetchFailToastShown.current = true
        toast.error('套利中心数据刷新失败')
      }
    } finally {
      setLoading(false)
    }
  }, [])

  const initDone = useRef(false)
  useEffect(() => {
    if (!initDone.current) {
      initDone.current = true
      fetchAll()
    }
    if (!pageActive) return
    const interval = setInterval(fetchAll, 30_000)
    return () => clearInterval(interval)
  }, [fetchAll, pageActive])

  return {
    arbStatus, arbPositions, arbOpps,
    rebStatus, rebOpps, rebPositions, rebCapital, washStatus, rebAnalytics, incentives,
    strategyConfigs, events, notifications, setNotifications,
    pointsSummary, ruleGate, paperSession,
    loading, lastRefresh, fetchAll,
  }
}
