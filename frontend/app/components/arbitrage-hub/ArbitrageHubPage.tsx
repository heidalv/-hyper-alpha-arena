/**
 * ArbitrageHubPage — 套利中心主页面
 *
 * Tab 布局:
 *   总览 | 模拟账户 | 交易员套利 | 启动配置 | QAA管道 | 套利积分 | 交易积分 | 刷交易 | 合约交易 | 规则同步
 *
 * 数据获取逻辑在 useArbitrageHubData hook（30s 轮询 + 事件增量合并 + 失败 toast）；
 * 规则同步面板拆分至 RuleSyncPanel.tsx。
 */
import React, { useState } from 'react'
import { cn } from '@/lib/utils'
import { usePageActive } from '@/hooks/usePageActive'
import {
  RefreshCw, ArrowRightLeft, Activity, Shield,
  Coins, TrendingUp, LayoutDashboard, Settings2,
  AlertTriangle, Flame, Wallet, Play, Sparkles, Layers,
} from 'lucide-react'
import {
  resumeRuleSyncGate,
  POINTS_ARB_STRATEGIES, TRADE_POINTS_STRATEGIES,
  getRebateConfig, patchEngineConfig, patchRiskGateConfig, patchStrategyConfig,
  type AiGeneratedConfig,
} from '@/lib/arbitrageApi'

// Tab content components
import OverviewTab from './OverviewTab'
import PointsTab from './PointsTab'
import MonitorTab from './MonitorTab'
import ContractsTab from './ContractsTab'
import ArbitrageStartWizard from './ArbitrageStartWizard'
import ArbitragePaperAccountPanel from './ArbitragePaperAccountPanel'
import UnifiedAccountOverviewPanel from './UnifiedAccountOverviewPanel'
import ArbitrageTraderConfigTab from './ArbitrageTraderConfigTab'
import ArbitrageQAAStatusPanel from './ArbitrageQAAStatusPanel'
import ArbitrageProgramsPanel from './ArbitrageProgramsPanel'
import FundingMatrixPanel from './FundingMatrixPanel'
import RuleSyncPanel from './RuleSyncPanel'
import KpiTile from './KpiTile'
import AiConfigDialog from './AiConfigDialog'
import { useArbitrageHubData } from './useArbitrageHubData'

type TabKey = 'overview' | 'unified_account' | 'start_config' | 'paper_account' | 'trader_config' | 'qaa_pipeline' | 'points_arb' | 'trade_points' | 'wash_trade' | 'contracts' | 'rule_sync'

const DEFAULT_ENGINE_FORM = {
  min_monthly_value: 50,
  max_position_usd: 2000,
  max_total_volume_7d: 100000,
  max_holding_days: 7,
}
const DEFAULT_RISK_FORM = {
  max_daily_volume_per_exchange: 20000,
  max_weekly_volume_per_exchange: 100000,
  max_daily_loss_pct: 0.02,
}

export default function ArbitrageHubPage() {
  const pageActive = usePageActive()
  const [tab, setTab] = useState<TabKey>('overview')

  const {
    arbStatus, arbPositions, arbOpps,
    rebStatus, rebOpps, rebPositions, rebCapital, washStatus, rebAnalytics, incentives,
    strategyConfigs, events, notifications, setNotifications,
    pointsSummary, ruleGate, paperSession,
    loading, lastRefresh, fetchAll,
  } = useArbitrageHubData(pageActive)

  // ── AI 一键配置对话框 ──
  const [aiConfigOpen, setAiConfigOpen] = useState(false)
  const [aiEngine, setAiEngine] = useState(DEFAULT_ENGINE_FORM)
  const [aiRisk, setAiRisk] = useState(DEFAULT_RISK_FORM)

  const openAiConfig = async () => {
    // 打开前拉取当前配置作为对比基线，失败时用默认值
    try {
      const cfg = await getRebateConfig()
      if (cfg.loaded) {
        if (cfg.engine) setAiEngine({ ...DEFAULT_ENGINE_FORM, ...cfg.engine })
        if (cfg.risk_gate) setAiRisk({ ...DEFAULT_RISK_FORM, ...cfg.risk_gate })
      }
    } catch { /* 用默认值 */ }
    setAiConfigOpen(true)
  }

  const applyAiConfig = async (config: AiGeneratedConfig) => {
    if (config.engine && Object.keys(config.engine).length) {
      await patchEngineConfig(config.engine)
    }
    if (config.risk_gate && Object.keys(config.risk_gate).length) {
      await patchRiskGateConfig(config.risk_gate)
    }
    for (const [sid, sc] of Object.entries(config.strategies || {})) {
      await patchStrategyConfig(sid, {
        params: sc.params,
        risk_overrides: sc.risk_overrides,
        enabled: sc.enabled,
      })
    }
    fetchAll()
  }

  // AiConfigDialog 需要的当前策略面板状态（从 hook 的 strategyConfigs 转换）
  const aiPanels = Object.fromEntries(
    Object.entries(strategyConfigs).map(([sid, sc]) => [sid, {
      params: sc.params || {},
      overrides: sc.risk_overrides || {},
      enabled: !!sc.enabled,
      expanded: false,
    }]),
  )

  // ── Tab config ──
  const tabs: { key: TabKey; label: string; icon: React.ReactNode }[] = [
    { key: 'overview', label: '总览', icon: <LayoutDashboard className="w-4 h-4" /> },
    { key: 'unified_account', label: '统一账户', icon: <Layers className="w-4 h-4" /> },
    { key: 'paper_account', label: '模拟账户', icon: <Wallet className="w-4 h-4" /> },
    { key: 'trader_config', label: '交易员套利', icon: <Settings2 className="w-4 h-4" /> },
    { key: 'start_config', label: '启动配置', icon: <Play className="w-4 h-4" /> },
    { key: 'qaa_pipeline', label: 'QAA管道', icon: <Activity className="w-4 h-4" /> },
    { key: 'points_arb', label: '套利积分', icon: <Coins className="w-4 h-4" /> },
    { key: 'trade_points', label: '交易积分', icon: <Activity className="w-4 h-4" /> },
    { key: 'wash_trade', label: '刷交易', icon: <Flame className="w-4 h-4" /> },
    { key: 'contracts', label: '合约交易', icon: <TrendingUp className="w-4 h-4" /> },
    { key: 'rule_sync', label: '规则同步', icon: <Shield className="w-4 h-4" /> },
  ]

  // ── Shared props for all tabs ──
  const sharedProps = {
    arbStatus, arbPositions, arbOpps,
    rebStatus, rebOpps, rebPositions, rebCapital, washStatus, rebAnalytics, incentives,
    strategyConfigs, events, notifications, pointsSummary,
    ruleGate, paperSession,
    onRefresh: fetchAll,
  }

  return (
    <div className="min-h-screen bg-background text-foreground p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <ArrowRightLeft className="w-7 h-7 text-blue-500" />
            套利中心
          </h1>
          <p className="text-muted-foreground text-sm mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>套利引擎 · 积分返利 · 风险监控 · 绩效分析</span>
            {/* P0 修复：状态灯拆为 V3 | Rebate | Paper 三态，不再单灯误导 */}
            <span className="flex items-center gap-1" title="V3 统计套利引擎">
              <span className={cn('w-2 h-2 rounded-full inline-block', arbStatus.engine_enabled ? 'bg-green-500' : 'bg-gray-400')} />
              <span className="text-[11px]">V3</span>
            </span>
            <span className="flex items-center gap-1" title="Rebate 返利引擎">
              <span className={cn('w-2 h-2 rounded-full inline-block', rebStatus.engine_enabled ? 'bg-green-500' : 'bg-gray-400')} />
              <span className="text-[11px]">Rebate</span>
            </span>
            <span className="flex items-center gap-1" title="Paper 验证会话">
              <span className={cn('w-2 h-2 rounded-full inline-block', paperSession?.running ? 'bg-green-500 animate-pulse' : 'bg-gray-400')} />
              <span className="text-[11px]">Paper</span>
            </span>
            <span className="text-muted-foreground/60 text-[11px]">
              {lastRefresh.toLocaleTimeString()}
            </span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={openAiConfig}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm transition-colors"
          >
            <Sparkles className="w-4 h-4" />
            AI 一键配置
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

      {/* Sticky KPI Strip */}
      <div className="sticky top-0 z-20 mb-4 grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 bg-background/95 backdrop-blur py-2">
        <KpiTile label="套利积分策略" value={`${rebOpps.filter(o => (POINTS_ARB_STRATEGIES as readonly string[]).includes(o.strategy_type)).length}`} sub={POINTS_ARB_STRATEGIES.join('/')} tone="blue" />
        <KpiTile label="交易积分策略" value={`${rebOpps.filter(o => (TRADE_POINTS_STRATEGIES as readonly string[]).includes(o.strategy_type)).length}`} sub={`${TRADE_POINTS_STRATEGIES.join('/')}（已关闭）`} tone="purple" />
        <KpiTile label="活跃仓位" value={`${rebStatus.active_positions + arbPositions.length}`} sub={`Rebate ${rebStatus.active_positions} · V3 ${arbPositions.length}`} tone="green" />
        <KpiTile label="净收益" value={`$${(rebAnalytics.net_pnl || 0).toFixed(2)}`} sub={`返利 $${(rebAnalytics.total_rebate || 0).toFixed(2)}`} tone="amber" />
        <KpiTile label="刷量安全" value={washStatus.is_safe ? '安全' : '等待'} sub={washStatus.is_safe ? '可继续' : `${Math.round(washStatus.next_safe_interval_sec)}s`} tone={washStatus.is_safe ? 'green' : 'red'} />
        <KpiTile label="规则闸门" value={ruleGate?.is_rebate_paused || ruleGate?.is_v3_paused ? '暂停' : '正常'} sub={ruleGate?.pause_reason || '无阻塞'} tone={ruleGate?.is_rebate_paused || ruleGate?.is_v3_paused ? 'red' : 'green'} />
      </div>

      {(ruleGate?.is_rebate_paused || ruleGate?.is_v3_paused) && (
        <div className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 p-4 flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-red-500 mt-0.5" />
            <div>
              <div className="font-semibold text-red-700 dark:text-red-300">规则同步已触发暂停</div>
              <div className="text-sm text-muted-foreground mt-1">
                {ruleGate.pause_reason || '规则变化需要人工确认'}；影响策略：{ruleGate.paused_strategies?.length ? ruleGate.paused_strategies.join('/') : 'Rebate/S1-S8'}
              </div>
            </div>
          </div>
          <button
            onClick={async () => {
              await resumeRuleSyncGate('resume_from_arbitrage_hub')
              fetchAll()
            }}
            className="px-3 py-2 rounded-lg bg-red-600 hover:bg-red-700 text-white text-sm"
          >
            人工确认恢复
          </button>
        </div>
      )}

      {paperSession?.running && (
        <div className="mb-4 rounded-xl border border-green-500/40 bg-green-500/10 p-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="font-semibold text-green-700 dark:text-green-300 flex items-center gap-2">
              <span className="inline-block w-2 h-2 rounded-full bg-green-500 animate-pulse" />
              Paper 验证运行中
            </div>
            <div className="text-sm text-muted-foreground mt-1">
              账户 #{paperSession.account_id} · 策略 {paperSession.strategies?.join(' / ')} ·
              每 {paperSession.interval_seconds ?? 90}s 扫描 ·
              tick #{paperSession.tick_count ?? 0}
              {paperSession.last_tick ? (
                <>
                  {' · '}权益 ${Number(paperSession.last_tick.account_equity || 0).toFixed(0)}
                  {' · '}{paperSession.last_tick.viable_count ?? 0} 个可行
                  {paperSession.last_tick.auto_executed
                    ? ' · 已 Paper 开仓'
                    : paperSession.last_tick.auto_exec_error
                      ? ` · 未开仓：${paperSession.last_tick.auto_exec_error}`
                      : ''}
                </>
              ) : null}
            </div>
          </div>
          <button
            onClick={() => setTab('start_config')}
            className="px-3 py-2 rounded-lg bg-green-600 hover:bg-green-700 text-white text-sm"
          >
            查看启动配置
          </button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-2 mb-6 border-b border-border pb-2">
        {tabs.map(t => (
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
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {tab === 'overview' && <OverviewTab {...sharedProps} onNavigate={t => setTab(t as TabKey)} />}
      {tab === 'unified_account' && <UnifiedAccountOverviewPanel />}
      {tab === 'paper_account' && <ArbitragePaperAccountPanel />}
      {tab === 'trader_config' && <ArbitrageTraderConfigTab />}
      {tab === 'start_config' && <ArbitrageStartWizard onRefresh={fetchAll} onNavigate={t => setTab(t as TabKey)} externalSession={paperSession} />}
      {tab === 'qaa_pipeline' && <ArbitrageQAAStatusPanel />}
      {tab === 'points_arb' && <><ArbitrageProgramsPanel /><FundingMatrixPanel /><PointsTab {...sharedProps} mode="points_arb" /></>}
      {tab === 'trade_points' && <PointsTab {...sharedProps} mode="trade_points" />}
      {tab === 'wash_trade' && <MonitorTab {...sharedProps} focus="wash_trade" />}
      {tab === 'contracts' && <ContractsTab onRefresh={fetchAll} />}
      {tab === 'rule_sync' && <RuleSyncPanel gate={ruleGate} events={events} onRefresh={fetchAll} />}

      {/* AI 一键配置对话框 */}
      <AiConfigDialog
        open={aiConfigOpen}
        onOpenChange={setAiConfigOpen}
        currentEngine={aiEngine}
        currentRisk={aiRisk}
        currentPanels={aiPanels}
        onApply={applyAiConfig}
      />

      {/* Notification Toast Overlay */}
      {notifications.length > 0 && (
        <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-2 max-w-sm">
          {notifications.map(n => (
            <div
              key={n.id}
              className={cn(
                'px-4 py-3 rounded-lg shadow-lg border text-sm animate-in slide-in-from-right',
                n.type === 'error' ? 'bg-red-900/90 border-red-700 text-red-100' :
                n.type === 'warning' ? 'bg-yellow-900/90 border-yellow-700 text-yellow-100' :
                n.type === 'success' ? 'bg-green-900/90 border-green-700 text-green-100' :
                'bg-card border-border text-foreground',
              )}
            >
              <div className="flex items-start justify-between gap-2">
                <span>{n.message}</span>
                <button
                  onClick={() => setNotifications(prev => prev.filter(x => x.id !== n.id))}
                  className="text-muted-foreground hover:text-foreground shrink-0"
                >
                  &times;
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
