/**
 * IntelligentLearningCenter — 统一智能学习中心 (F2 重设)
 *
 * 对齐后端 L1-L5 架构的 7 Tab 结构：
 *   总览 / 学习后端 / 进化系统 / 因子系统 / 配置控制 / OpenCode / 知识库
 *
 * 后端对应：
 *   - BackendRegistry (11 个学习后端) → 学习后端 Tab
 *   - LearningConfig (8 个集中开关) → 配置控制 Tab
 *   - FactorRegistry (124 因子, 含 20 legacy 短名) → 因子系统 Tab
 */

import { useState, useEffect, useCallback, lazy, Suspense } from 'react';
import { motion } from 'framer-motion';
import { Brain, RefreshCw } from 'lucide-react';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { cn } from '@/lib/utils';
import {
  StatCard,
  SectionCard,
  RefreshButton,
  InfoBanner,
  EmptyState,
} from './IlcUi';
import {
  getOverview,
  getEvolutionStatus,
  getEvolutionHistory,
  getOpenCodeInsights,
  getOpenCodeProposals,
  getFeatureFlags,
  getBackendsStatus,
  getLearningConfig,
  queryKnowledge,
  type OverviewResponse,
  type BackendStatus,
  type LearningConfigSnapshot,
} from '@/lib/intelligentLearningApi';
import { IlcOperationsTab } from './IlcOperationsTab';
import { IlcHermesTab } from './IlcHermesTab';
import { IlcGovernorTab } from './IlcGovernorTab';
import { NexusLivePipeline } from './nexus/NexusLivePipeline';
import { NexusRLLab } from './nexus/NexusRLLab';
import { NexusBacktestLoop } from './nexus/NexusBacktestLoop';
import { NexusCodegen } from './nexus/NexusCodegen';
import { ScalpFactorHealth } from './nexus/ScalpFactorHealth';
import { MidLongHealth } from './nexus/MidLongHealth';

// 因子体系页面（懒加载，避免 ILC 首屏体积膨胀）
const UnifiedFactorPage = lazy(() => import('@/components/factor-unified/UnifiedFactorPage'));

// ─────────────────────────────────────────────
//  Tab 配置（7 Tab，对齐后端架构）
// ─────────────────────────────────────────────

const TABS = [
  { id: 'pipeline', label: '总览·实时管线' },
  { id: 'evolution', label: '假设-进化' },
  { id: 'hermes', label: 'Hermes 自进化' },
  { id: 'factors', label: '因子体系' },
  { id: 'scalp_health', label: '短线因子健康' },
  { id: 'midlong_health', label: '中长线健康' },
  { id: 'rl', label: 'RL 决策实验室' },
  { id: 'backtest', label: '回测闭环' },
  { id: 'codegen', label: 'codegen 治理' },
  { id: 'governor', label: 'Governor' },
  { id: 'knowledge', label: '知识库' },
  { id: 'config', label: '配置' },
  { id: 'overview', label: 'KPI 概览' },
  { id: 'operations', label: '运维闭环' },
  { id: 'backends', label: '学习后端' },
  { id: 'opencode', label: 'OpenCode 提案' },
] as const;

type TabId = typeof TABS[number]['id'];

/** 兼容旧 Tab 内 KPI 调用，映射到统一 StatCard */
function KpiCard({ label, value, sub, color = 'blue' }: {
  label: string; value: string | number; sub?: string; color?: string;
}) {
  const tone =
    color === 'green' ? 'good'
      : color === 'red' ? 'bad'
        : color === 'yellow' ? 'warn'
          : 'default';
  return <StatCard label={label} value={value} hint={sub} tone={tone} />;
}

/** 保持 Tab 内容挂载，避免切换时布局跳动与重复 loading */
function IlcTabPane({ value, activeTab, visited, children }: {
  value: TabId; activeTab: TabId; visited: Set<TabId>; children: React.ReactNode;
}) {
  if (!visited.has(value)) return null;
  const active = activeTab === value;
  return (
    <motion.div
      role="tabpanel"
      hidden={!active}
      className="mt-6 min-h-[420px] focus-visible:outline-none"
      initial={false}
      animate={active ? { opacity: 1, y: 0 } : { opacity: 0, y: 10 }}
      transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
    >
      {children}
    </motion.div>
  );
}

// ─────────────────────────────────────────────
//  主组件
// ─────────────────────────────────────────────

export default function IntelligentLearningCenter() {
  const [activeTab, setActiveTab] = useState<TabId>('pipeline');
  const [visitedTabs, setVisitedTabs] = useState<Set<TabId>>(() => new Set(['pipeline']));
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setVisitedTabs((prev) => new Set(prev).add(activeTab));
  }, [activeTab]);

  const fetchOverview = useCallback(async () => {
    try {
      setLoading(true);
      const data = await getOverview();
      setOverview(data);
      setError(null);
    } catch (err: any) {
      setError(err.message || 'Failed to load overview');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchOverview();
    const timer = setInterval(fetchOverview, 30000); // 30s refresh
    return () => clearInterval(timer);
  }, [fetchOverview]);

  if (loading && !overview) {
    return (
      <div className="container mx-auto py-6 flex items-center justify-center min-h-[320px]">
        <div className="flex items-center gap-2 text-muted-foreground">
          <RefreshCw className="h-4 w-4 animate-spin" />
          加载学习中心…
        </div>
      </div>
    );
  }

  const ocHealthy = overview?.opencode?.sidecar_healthy;

  return (
    <div className="container mx-auto py-6 space-y-6">
      <motion.div
        className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between"
        initial={{ opacity: 0, y: -12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
      >
        <div>
          <h1 className="text-3xl font-bold tracking-tight flex items-center gap-2">
            <motion.span
              initial={{ rotate: -20, scale: 0.7, opacity: 0 }}
              animate={{ rotate: 0, scale: 1, opacity: 1 }}
              transition={{ type: 'spring', stiffness: 260, damping: 18, delay: 0.05 }}
              className="inline-flex"
            >
              <Brain className="h-8 w-8 text-primary" />
            </motion.span>
            进化中枢
          </h1>
          <p className="text-muted-foreground mt-1">
            假设-进化、Hermes 自进化、因子体系、RL 决策与回测闭环的统一实时工作台
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={ocHealthy ? 'secondary' : 'destructive'} className="font-normal">
            OpenCode {ocHealthy ? '在线' : '离线'}
          </Badge>
          <Badge variant="outline" className="font-normal">
            因子 {overview?.factors?.total ?? '—'}
          </Badge>
          <Badge variant="outline" className="font-normal">
            策略 {overview?.strategies?.active ?? 0}/{overview?.strategies?.total ?? '—'}
          </Badge>
          <RefreshButton onClick={fetchOverview} loading={loading} />
        </div>
      </motion.div>

      {error && (
        <InfoBanner title="加载异常" variant="warn">
          {error}
        </InfoBanner>
      )}

      <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as TabId)} className="w-full">
        <TabsList className="flex h-auto w-full flex-wrap justify-start gap-1 bg-muted p-1">
          {TABS.map((tab) => (
            <TabsTrigger
              key={tab.id}
              value={tab.id}
              className="text-sm data-[state=active]:bg-background data-[state=active]:shadow-sm"
            >
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>

        <IlcTabPane value="pipeline" activeTab={activeTab} visited={visitedTabs}>
          <NexusLivePipeline />
        </IlcTabPane>
        <IlcTabPane value="rl" activeTab={activeTab} visited={visitedTabs}>
          <NexusRLLab />
        </IlcTabPane>
        <IlcTabPane value="backtest" activeTab={activeTab} visited={visitedTabs}>
          <NexusBacktestLoop />
        </IlcTabPane>
        <IlcTabPane value="codegen" activeTab={activeTab} visited={visitedTabs}>
          <NexusCodegen />
        </IlcTabPane>
        <IlcTabPane value="overview" activeTab={activeTab} visited={visitedTabs}>
          <OverviewTab overview={overview} />
        </IlcTabPane>
        <IlcTabPane value="operations" activeTab={activeTab} visited={visitedTabs}>
          <IlcOperationsTab />
        </IlcTabPane>
        <IlcTabPane value="hermes" activeTab={activeTab} visited={visitedTabs}>
          <IlcHermesTab />
        </IlcTabPane>
        <IlcTabPane value="governor" activeTab={activeTab} visited={visitedTabs}>
          <IlcGovernorTab />
        </IlcTabPane>
        <IlcTabPane value="backends" activeTab={activeTab} visited={visitedTabs}>
          <BackendsTab />
        </IlcTabPane>
        <IlcTabPane value="evolution" activeTab={activeTab} visited={visitedTabs}>
          <EvolutionTab />
        </IlcTabPane>
        <IlcTabPane value="factors" activeTab={activeTab} visited={visitedTabs}>
          <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">加载因子体系…</div>}>
            <UnifiedFactorPage />
          </Suspense>
        </IlcTabPane>
        <IlcTabPane value="scalp_health" activeTab={activeTab} visited={visitedTabs}>
          <ScalpFactorHealth />
        </IlcTabPane>
        <IlcTabPane value="midlong_health" activeTab={activeTab} visited={visitedTabs}>
          <MidLongHealth />
        </IlcTabPane>
        <IlcTabPane value="config" activeTab={activeTab} visited={visitedTabs}>
          <ConfigTab />
        </IlcTabPane>
        <IlcTabPane value="opencode" activeTab={activeTab} visited={visitedTabs}>
          <OpenCodeTab />
        </IlcTabPane>
        <IlcTabPane value="knowledge" activeTab={activeTab} visited={visitedTabs}>
          <KnowledgeTab />
        </IlcTabPane>
      </Tabs>
    </div>
  );
}

// ─────────────────────────────────────────────
//  Tab 1: 总览
// ─────────────────────────────────────────────

function OverviewTab({ overview }: { overview: OverviewResponse | null }) {
  const evo = overview?.evolution;
  const factors = overview?.factors;
  const strats = overview?.strategies;
  const oc = overview?.opencode;
  const ll = overview?.learning_loop;
  const kp = overview?.knowledge_pool;
  const alerts = overview?.alerts || [];

  return (
    <div className="space-y-4">
      {/* KPI Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard
          label="因子总数"
          value={factors?.total ?? '?'}
          sub={factors?.status === 'healthy' ? '✅ 健康' : factors?.status === 'degraded' ? '⚠️ 降级' : '🔴 异常'}
          color={factors?.status === 'healthy' ? 'green' : factors?.status === 'degraded' ? 'yellow' : 'red'}
        />
        <KpiCard
          label="活跃策略"
          value={strats?.active ?? '?'}
          sub={`总计 ${strats?.total ?? '?'} 个`}
          color="blue"
        />
        <KpiCard
          label="知识条目"
          value={kp?.total_lessons ?? '?'}
          sub={Object.entries(kp?.by_category || {}).map(([k, v]) => `${k}:${v}`).join(', ') || '暂无'}
          color="purple"
        />
        <KpiCard
          label="学习闭环"
          value={ll?.enabled ? (ll?.paused ? '⏸️ 暂停' : '▶️ 运行') : '⏹️ 停止'}
          sub={ll?.registered ? '已注册' : '未注册'}
          color={ll?.enabled && !ll?.paused ? 'green' : 'yellow'}
        />
      </div>

      {/* Second Row KPI */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard
          label="最近进化"
          value={evo?.last_evolution_type || '无'}
          sub={evo?.last_evolution_at ? new Date(evo.last_evolution_at).toLocaleString() : 'N/A'}
          color="blue"
        />
        <KpiCard
          label="OpenCode 状态"
          value={oc?.sidecar_healthy ? '✅ 在线' : '❌ 离线'}
          sub={`${oc?.open_insights ?? 0} 洞察 / ${oc?.pending_proposals ?? 0} 待审提案`}
          color={oc?.sidecar_healthy ? 'green' : 'red'}
        />
        <KpiCard
          label="进化提升"
          value={evo?.last_promoted_count ?? 0}
          sub={`最佳适应度: ${evo?.last_best_fitness?.toFixed(3) ?? 'N/A'}`}
          color="green"
        />
        <KpiCard
          label="Tier分布"
          value={Object.entries(strats?.by_tier || {}).length}
          sub="个层级"
          color="blue"
        />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard
          label="Hermes 成熟度"
          value={overview?.hermes?.maturity_score ?? '?'}
          sub="/ 100"
          color="purple"
        />
        <KpiCard
          label="Governor 待审"
          value={overview?.runtime_governor?.pending_count ?? 0}
          sub="patch 待批准"
          color={(overview?.runtime_governor?.pending_count ?? 0) > 0 ? 'yellow' : 'green'}
        />
      </div>

      {/* Alerts */}
      {alerts.length > 0 && (
        <InfoBanner title={`告警 (${alerts.length})`} variant="warn">
          <div className="space-y-2 max-h-48 overflow-y-auto">
            {alerts.map((a, i) => (
              <div key={i} className="flex items-center gap-2 text-sm">
                <Badge variant={a.severity === 'critical' ? 'destructive' : 'secondary'}>
                  {a.severity}
                </Badge>
                <span>{a.title}</span>
                <span className="text-xs text-muted-foreground ml-auto">{a.source}</span>
              </div>
            ))}
          </div>
        </InfoBanner>
      )}

      <InfoBanner title="学习架构（L1-L5）">
        交易平仓 → <code className="text-xs bg-muted px-1 rounded">process_outcome</code> 唯一入口 →
        9 步 EMA 核心 → <code className="text-xs bg-muted px-1 rounded">BackendRegistry.handle_all</code> 调度 14 个后端。
        Hermes L2 Prompt 优化后<strong>直接 active</strong>（Paper 不做 A/B）；L3/L4 经 Governor 审批。详见「运维闭环」「Hermes」「Governor」标签页。
      </InfoBanner>
    </div>
  );
}

// ─────────────────────────────────────────────
//  Tab 2: 学习后端（新增，对齐 BackendRegistry）
// ─────────────────────────────────────────────

/** 后端名称中文映射 */
const BACKEND_LABELS: Record<string, string> = {
  causal_diagnosis: '亏损根因诊断',
  reflexion: 'Reflexion 反思',
  promotion: '策略达标晋升',
  template_stats: '模板 live stats',
  qaa_evolution: 'QAA 进化',
  qaa_semantic_memory: 'QAA 语义记忆',
  factor_strategy_joint: '因子-策略联合',
  concept_drift: '概念漂移检测',
  periodic_review: '定期复盘',
  pattern_mining: '模式挖掘',
  pattern_extraction: '成功模板提取',
  causal_discovery: '因果发现',
  hermes_agent_wisdom: 'Hermes Agent 智慧',
  block_pattern_learning: '阻断模式学习',
};

function BackendsTab() {
  const [backends, setBackends] = useState<BackendStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const data = await getBackendsStatus();
      setBackends(data);
      setError(null);
    } catch (e: any) {
      setError(e.message || '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 30000);
    return () => clearInterval(timer);
  }, [load]);

  const enabledCount = backends.filter(b => b.enabled).length;

  return (
    <SectionCard
      title="学习后端注册表"
      description="process_outcome 通过 BackendRegistry.handle_all 统一调度全部后端"
      action={<RefreshButton onClick={load} loading={loading} />}
    >
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
        <KpiCard label="注册后端" value={backends.length || '—'} color="blue" />
        <KpiCard label="已启用" value={enabledCount || '—'} sub={`共 ${backends.length} 个`} color="green" />
        <KpiCard label="已关闭" value={(backends.length - enabledCount) || '—'} sub="受 env 开关控制" color="gray" />
      </div>

      {error && (
        <InfoBanner title="加载失败" variant="warn">{error}</InfoBanner>
      )}

      {!loading && backends.length > 0 && (
        <div className="rounded-md border overflow-hidden">
          <div className="grid grid-cols-12 gap-2 text-xs font-medium text-muted-foreground px-3 py-2 bg-muted/50 border-b">
            <div className="col-span-1">优先级</div>
            <div className="col-span-3">后端名称</div>
            <div className="col-span-4">功能</div>
            <div className="col-span-2">状态</div>
            <div className="col-span-2">调度类型</div>
          </div>
          {[...backends].sort((a, b) => a.priority - b.priority).map(b => {
            const asyncBackends = ['reflexion', 'causal_discovery'];
            const thresholdBackends = ['periodic_review', 'pattern_mining'];
            const kind = asyncBackends.includes(b.name) ? '异步后台'
              : thresholdBackends.includes(b.name) ? '计数触发'
              : '同步即时';
            return (
              <div key={b.name} className="grid grid-cols-12 gap-2 items-center px-3 py-2.5 border-b last:border-0 text-sm hover:bg-muted/30">
                <div className="col-span-1 text-muted-foreground font-mono text-xs">{b.priority}</div>
                <div className="col-span-3 font-mono text-xs">{b.name}</div>
                <div className="col-span-4">{BACKEND_LABELS[b.name] || b.name}</div>
                <div className="col-span-2">
                  <Badge variant={b.enabled ? 'secondary' : 'outline'}>
                    {b.enabled ? '启用' : '关闭'}
                  </Badge>
                </div>
                <div className="col-span-2 text-xs text-muted-foreground">{kind}</div>
              </div>
            );
          })}
        </div>
      )}

      {!loading && backends.length === 0 && !error && (
        <EmptyState message="后端注册表数据未返回，请确认后端已启动并完成注册。" />
      )}
    </SectionCard>
  );
}

// ─────────────────────────────────────────────
//  Tab 3: 进化系统
// ─────────────────────────────────────────────

function EvolutionTab() {
  const [status, setStatus] = useState<any>(null);
  const [history, setHistory] = useState<any[]>([]);

  useEffect(() => {
    getEvolutionStatus().then(setStatus).catch(() => {});
    getEvolutionHistory({ page_size: 10 }).then(d => setHistory(d?.items || d?.history || [])).catch(() => {});
  }, []);

  return (
    <SectionCard title="进化系统" description="NSGA-II 多目标进化与策略参数优化">
      <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard label="进化器状态" value={status?.evolver_running ? '运行中' : '空闲'} color="blue" />
        <KpiCard label="调度器" value={status?.scheduler_status || 'N/A'} color="green" />
        <KpiCard label="NSGA-II" value={status?.nsga2_enabled ? '启用' : '关闭'} color={status?.nsga2_enabled ? 'green' : 'gray'} />
        <KpiCard label="历史记录" value={history.length} sub="条进化记录" color="purple" />
      </div>

      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          onClick={async () => {
            const { triggerEvolution } = await import('@/lib/intelligentLearningApi');
            await triggerEvolution('manual');
            alert('手动进化已触发');
          }}
        >
          手动进化
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={async () => {
            const { triggerEvolution } = await import('@/lib/intelligentLearningApi');
            await triggerEvolution('emergency');
            alert('紧急进化已触发');
          }}
        >
          紧急进化
        </Button>
      </div>

      {/* 进化历史 */}
      {history.length > 0 && (
        <div>
          <h3 className="font-semibold text-gray-700 mb-2">最近进化记录</h3>
          <div className="space-y-2 max-h-80 overflow-y-auto">
            {history.map((h: any, i: number) => (
              <div key={i} className="border rounded p-2 text-sm">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs text-blue-600">{h.template_id || 'all'}</span>
                  <span className="text-xs text-gray-500">{h.type || h.evolution_type}</span>
                  {h.promoted_count > 0 && (
                    <span className="px-1.5 py-0.5 rounded text-xs bg-green-100 text-green-700">
                      晋升 {h.promoted_count}
                    </span>
                  )}
                  <span className="text-xs text-gray-400 ml-auto">
                    {h.created_at ? new Date(h.created_at).toLocaleString() : ''}
                  </span>
                </div>
                {h.best_fitness != null && (
                  <div className="text-xs text-gray-500 mt-1">最佳适应度: {h.best_fitness.toFixed(3)}</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {status?.evolver_progress && (
        <pre className="bg-muted p-3 rounded-md text-xs overflow-auto max-h-64 border">
          {JSON.stringify(status.evolver_progress, null, 2)}
        </pre>
      )}
      </div>
    </SectionCard>
  );
}

// ─────────────────────────────────────────────
//  Tab: 配置控制（重写：LearningConfig + FeatureFlags）
// ─────────────────────────────────────────────

/** LearningConfig 字段中文映射 */
const CONFIG_LABELS: Record<keyof LearningConfigSnapshot, string> = {
  loop_enabled: '学习闭环总开关',
  drl_retrain_auto: 'DRL 自动重训',
  enable_coordinator: '系统协调器',
  enable_kelly_position: 'Kelly 仓位',
  nsga2_enabled: 'NSGA-II 进化',
  factor_strategy_joint: '因子-策略联合后端',
  concept_drift_detection: '概念漂移检测后端',
  causal_discovery: '因果发现后端',
};

/** learning_core 内核开关中文说明（用于 RL / codegen / 假设自动进化门控） */
const CORE_FLAG_LABELS: Record<string, string> = {
  LEARNING_CORE_ENABLED: '统一内核总开关',
  LEARNING_LEDGER_ENABLED: '血缘账本记录',
  HYPOTHESIS_AUTO_EVOLVE: '假设晋升自动进化(GA)',
  RL_DECISION_ENABLED: 'RL 决策 agent 总开关',
  RL_SHADOW_ONLY: 'RL 仅影子模式(安全)',
  OPENCODE_CODEGEN_ENABLED: 'codegen 生成总开关',
  OPENCODE_CODEGEN_SHADOW_ONLY: 'codegen 仅影子沙箱(安全)',
};

/** 打开这些开关会放开安全护栏，需谨慎 */
const CORE_FLAG_DANGEROUS = new Set(['RL_DECISION_ENABLED', 'OPENCODE_CODEGEN_ENABLED']);

function ConfigTab() {
  const [config, setConfig] = useState<LearningConfigSnapshot | null>(null);
  const [flags, setFlags] = useState<Record<string, boolean>>({});
  const [coreFlags, setCoreFlags] = useState<Record<string, boolean>>({});
  const [coreBusy, setCoreBusy] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const cfg = await getLearningConfig();
      setConfig(cfg);
      // P0-P3 大写开关（过滤掉下划线开头的嵌套字段）
      const all = await getFeatureFlags();
      const flat: Record<string, boolean> = {};
      Object.entries(all).forEach(([k, v]) => {
        if (!k.startsWith('_') && typeof v === 'boolean') flat[k] = v;
      });
      setFlags(flat);
      // learning_core 内核开关（可运行时切换）
      try {
        const { getLearningFlags } = await import('@/lib/learningCoreApi');
        const lf = await getLearningFlags();
        setCoreFlags(lf.flags || {});
      } catch (e) {
        console.warn('learning_core flags load failed', e);
      }
    } catch (e) {
      console.error('Config load failed', e);
    } finally {
      setLoading(false);
    }
  }, []);

  const toggleCore = useCallback(async (key: string, next: boolean) => {
    if (next && CORE_FLAG_DANGEROUS.has(key)) {
      const ok = window.confirm(
        `确认开启「${CORE_FLAG_LABELS[key] || key}」？\n\n该开关会放开安全护栏，实盘/生成动作仍受 Governor 审批与影子模式保护，但请确保你了解影响。`
      );
      if (!ok) return;
    }
    setCoreBusy(key);
    // 乐观更新
    setCoreFlags(prev => ({ ...prev, [key]: next }));
    try {
      const { setLearningFlag } = await import('@/lib/learningCoreApi');
      await setLearningFlag(key, next);
    } catch (e) {
      console.error('setLearningFlag failed', e);
      // 回滚
      setCoreFlags(prev => ({ ...prev, [key]: !next }));
    } finally {
      setCoreBusy(null);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <SectionCard title="配置控制" description="LearningConfig 集中开关与 P0-P3 高级特性">
      <div className="space-y-4">
      {config ? (
        <div className="grid grid-cols-2 gap-2">
          {(Object.keys(CONFIG_LABELS) as Array<keyof LearningConfigSnapshot>).map(key => {
            const val = config[key];
            return (
              <div key={key} className="flex items-center justify-between rounded-md border p-3">
                <div>
                  <div className="text-sm font-medium">{CONFIG_LABELS[key]}</div>
                  <div className="text-xs text-muted-foreground font-mono">{key}</div>
                </div>
                <Badge variant={val ? 'secondary' : 'outline'}>
                  {val ? '启用' : '关闭'}
                </Badge>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-gray-400 text-sm">{loading ? '加载中...' : '配置数据未返回'}</p>
      )}

      <div className="border-t pt-4">
        <h3 className="text-sm font-medium mb-2">进化中枢内核开关 (learning_core)</h3>
        <p className="text-xs text-muted-foreground mb-3">
          控制 RL 决策实验室、codegen 治理与假设自动进化。<strong className="text-amber-600">危险开关</strong>默认关闭，
          开启后仍受影子模式 + Governor 审批双重保护；运行时切换，重启回落到默认值。
        </p>
        {Object.keys(coreFlags).length === 0 ? (
          <p className="text-xs text-gray-400">{loading ? '加载中...' : '内核开关未返回（/api/learning/flags 不可用）'}</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {Object.entries(coreFlags).map(([key, val]) => {
              const dangerous = CORE_FLAG_DANGEROUS.has(key);
              const busy = coreBusy === key;
              return (
                <button
                  key={key}
                  type="button"
                  disabled={busy}
                  onClick={() => toggleCore(key, !val)}
                  className={cn(
                    'flex items-center justify-between rounded-md border p-2 text-left transition-colors',
                    'hover:bg-muted/50 disabled:opacity-60',
                    dangerous && !val && 'border-amber-500/40',
                    val && 'border-emerald-500/50 bg-emerald-500/5'
                  )}
                  title={`点击${val ? '关闭' : '开启'}`}
                >
                  <div className="min-w-0 mr-2">
                    <div className="text-sm font-medium truncate">
                      {CORE_FLAG_LABELS[key] || key}
                      {dangerous && <span className="ml-1 text-[10px] text-amber-600">危险</span>}
                    </div>
                    <div className="text-[11px] font-mono text-muted-foreground truncate">{key}</div>
                  </div>
                  <span
                    className={cn(
                      'shrink-0 inline-flex h-5 w-9 items-center rounded-full px-0.5 transition-colors',
                      val ? 'bg-emerald-500 justify-end' : 'bg-muted-foreground/30 justify-start'
                    )}
                  >
                    <span className="h-4 w-4 rounded-full bg-white shadow" />
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      <div className="border-t pt-4">
        <h3 className="text-sm font-medium mb-2">P0-P3 高级特性开关</h3>
        <p className="text-xs text-muted-foreground mb-3">
          可通过 API 运行时切换，重启后回落到 .env 默认值。
        </p>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
          {Object.entries(flags).map(([key, val]) => (
            <div key={key} className="flex items-center justify-between rounded-md border p-2">
              <div className="text-xs font-mono text-muted-foreground truncate mr-2">{key}</div>
              <Badge variant={val ? 'secondary' : 'outline'} className="shrink-0">
                {val ? 'ON' : 'OFF'}
              </Badge>
            </div>
          ))}
        </div>
      </div>
      </div>
    </SectionCard>
  );
}

// ─────────────────────────────────────────────
//  P1-6: OpenCode Bridge 运行状态面板
// ─────────────────────────────────────────────

function BridgeStatusPanel() {
  const [status, setStatus] = useState<Record<string, any> | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchStatus = async () => {
    setLoading(true);
    try {
      const { getOpenCodeStatus } = await import('@/lib/intelligentLearningApi');
      const data = await getOpenCodeStatus();
      setStatus(data);
    } catch (e) {
      // API 不可用时静默
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchStatus(); }, []);

  // 30s 刷新一次
  useEffect(() => {
    const t = setInterval(fetchStatus, 30000);
    return () => clearInterval(t);
  }, []);

  if (!status && !loading) return null;

  const sidecarOk = status?.sidecar_healthy !== false;
  const enabled = status?.enabled !== false;
  const lastOk = status?.last_ok_ts ? new Date((status.last_ok_ts as number) * 1000).toLocaleTimeString() : null;
  const lastErr = (status?.last_error as string) || null;

  return (
    <div className="border rounded p-3 bg-gray-50 text-xs space-y-1">
      <div className="flex items-center gap-3 flex-wrap">
        <span className="font-medium text-gray-700">OpenCode Bridge</span>

        {/* Sidecar 状态 */}
        <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded ${sidecarOk ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${sidecarOk ? 'bg-green-500' : 'bg-red-500'}`} />
          Sidecar {sidecarOk ? '健康' : '离线'}
        </span>

        {/* 启用状态 */}
        <span className={`px-1.5 py-0.5 rounded ${enabled ? 'bg-blue-100 text-blue-700' : 'bg-gray-200 text-gray-500'}`}>
          {enabled ? '已启用' : '已禁用'}
        </span>

        {/* 模型 */}
        {status?.model && (
          <span className="text-gray-500">模型: {String(status.model).split('/').pop()}</span>
        )}

        {/* 最后成功 */}
        {lastOk && (
          <span className="text-gray-400">最后OK: {lastOk}</span>
        )}
      </div>

      {/* 错误信息 */}
      {lastErr && (
        <div className="text-red-500 truncate" title={lastErr}>
          ⚠ {lastErr.substring(0, 120)}
        </div>
      )}

      {/* 加载状态 */}
      {loading && !status && (
        <div className="text-gray-400">获取状态中...</div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────
//  Tab 6: OpenCode（合并原 OpenCode分析 + 提案管理）
// ─────────────────────────────────────────────

function OpenCodeTab() {
  const [insights, setInsights] = useState<any[]>([]);
  const [proposals, setProposals] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [subTab, setSubTab] = useState<'insights' | 'proposals'>('insights');
  const [busy, setBusy] = useState<number | null>(null);
  const [detail, setDetail] = useState<any | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const loadInsights = async () => {
    setLoading(true);
    try {
      const data = await getOpenCodeInsights({ limit: 20 });
      setInsights(data?.insights || data?.items || []);
    } catch (e) { console.error('Failed to load insights', e); }
    finally { setLoading(false); }
  };

  const loadProposals = async () => {
    setLoading(true);
    try {
      const data = await getOpenCodeProposals({});
      setProposals(data?.proposals || data?.items || []);
    } catch (e) { console.error('Failed to load proposals', e); }
    finally { setLoading(false); }
  };

  useEffect(() => {
    if (subTab === 'insights') loadInsights();
    else loadProposals();
  }, [subTab]);

  // 定时刷新提案列表
  useEffect(() => {
    if (subTab !== 'proposals') return;
    const t = setInterval(loadProposals, 30000);
    return () => clearInterval(t);
  }, [subTab]);

  const openDetail = async (id: number) => {
    try {
      const { getOpenCodeProposalDetail } = await import('@/lib/intelligentLearningApi');
      const d = await getOpenCodeProposalDetail(id);
      setDetail(d);
      setDetailOpen(true);
    } catch (e) {
      alert('无法加载提案详情');
    }
  };

  const handleApply = async (id: number) => {
    if (!confirm('确认将该提案应用到 Paper 环境？')) return;
    setBusy(id);
    try {
      const { applyOpenCodeProposal } = await import('@/lib/intelligentLearningApi');
      await applyOpenCodeProposal(id);
      alert('提案已应用，进入 paper_applying 验证');
      loadProposals();
    } catch (e: any) {
      alert(e.message || '应用失败');
    } finally { setBusy(null); }
  };

  const handleReject = async (id: number) => {
    const reason = prompt('拒绝原因（可选）：') || '';
    if (!confirm(`确认拒绝该提案？${reason ? ' 原因: ' + reason : ''}`)) return;
    setBusy(id);
    try {
      const { rejectOpenCodeProposal } = await import('@/lib/intelligentLearningApi');
      await rejectOpenCodeProposal(id, reason);
      alert('已拒绝');
      loadProposals();
    } catch (e: any) {
      alert(e.message || '拒绝失败');
    } finally { setBusy(null); }
  };

  const handleReview = async (id: number) => {
    setBusy(id);
    try {
      const { reviewOpenCodeProposal } = await import('@/lib/intelligentLearningApi');
      const r = await reviewOpenCodeProposal(id);
      const decision = r?.review?.decision || r?.status || '完成';
      alert(`评审完成: ${decision}`);
      loadProposals();
    } catch (e: any) {
      alert(e.message || '评审失败');
    } finally { setBusy(null); }
  };

  const handleReviewAll = async () => {
    setBusy(-3);
    try {
      const { reviewAllOpenCodeProposals } = await import('@/lib/intelligentLearningApi');
      const r = await reviewAllOpenCodeProposals();
      alert(`已触发评审 ${r.reviewed} 条提案`);
      loadProposals();
    } catch (e: any) {
      alert(e.message || '批量评审失败');
    } finally { setBusy(null); }
  };

  const handleRollback = async (id: number) => {
    if (!confirm('确认回滚该提案的修改？')) return;
    setBusy(id);
    try {
      const { rollbackOpenCodeProposal } = await import('@/lib/intelligentLearningApi');
      await rollbackOpenCodeProposal(id);
      alert('已回滚');
      loadProposals();
    } catch (e: any) {
      alert(e.message || '回滚失败');
    } finally { setBusy(null); }
  };

  const handleEvaluate = async (force: boolean) => {
    if (force && !confirm('立即评估所有 paper_applying（忽略等待期）？')) return;
    setBusy(-4);
    try {
      const { evaluateOpenCodeProposalsNow } = await import('@/lib/intelligentLearningApi');
      const r = await evaluateOpenCodeProposalsNow(force);
      alert(`评估 ${r.evaluated_this_run} 条 · 累计 ${r.evaluated_total}`);
      loadProposals();
    } catch (e: any) {
      alert(e.message || '评估失败');
    } finally { setBusy(null); }
  };

  const statusLabel = (s: string) => {
    const map: Record<string, string> = {
      pending: '待审核', paper_applying: '验证中', applied: '已应用',
      rejected: '已拒绝', rolled_back: '已回滚', paper_validated: '已验证',
      validating: '验证中', inconclusive: '无结论',
    };
    return map[s] || s;
  };

  const statusColor = (s: string) => {
    if (s === 'pending') return 'bg-yellow-100 text-yellow-800';
    if (s === 'paper_applying' || s === 'validating') return 'bg-blue-100 text-blue-800';
    if (s === 'applied' || s === 'paper_validated') return 'bg-green-100 text-green-800';
    if (s === 'rejected') return 'bg-red-100 text-red-800';
    if (s === 'rolled_back') return 'bg-orange-100 text-orange-800';
    return 'bg-gray-100 text-gray-600';
  };

  return (
    <SectionCard
      title="OpenCode 智能中枢"
      action={
        <Button
          size="sm"
          onClick={async () => {
            const { triggerOpenCodeAnalyze } = await import('@/lib/intelligentLearningApi');
            await triggerOpenCodeAnalyze();
            alert('分析已触发');
          }}
        >
          触发分析
        </Button>
      }
    >
    <div className="space-y-4">
      <Tabs value={subTab} onValueChange={(v) => setSubTab(v as 'insights' | 'proposals')}>
        <TabsList>
          <TabsTrigger value="insights">洞察 ({insights.length})</TabsTrigger>
          <TabsTrigger value="proposals">提案 ({proposals.length})</TabsTrigger>
        </TabsList>
      </Tabs>

      <BridgeStatusPanel />

      {subTab === 'insights' ? (
        insights.length === 0 ? (
          <p className="text-gray-400 text-sm">{loading ? '加载中...' : '暂无洞察'}</p>
        ) : (
          <div className="space-y-2 max-h-96 overflow-y-auto">
            {insights.map((ins: any, i: number) => (
              <div key={i} className="border rounded p-3 text-sm">
                <div className="flex items-center gap-2 mb-1">
                  <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                    ins.severity === 'critical' ? 'bg-red-200 text-red-800' :
                    ins.severity === 'major' ? 'bg-orange-200 text-orange-800' :
                    'bg-blue-200 text-blue-800'
                  }`}>
                    {ins.severity || 'info'}
                  </span>
                  <span className="text-gray-500">{ins.category || ins.domain}</span>
                  <span className={`ml-auto text-xs ${ins.status === 'open' ? 'text-green-600' : 'text-gray-400'}`}>
                    {ins.status}
                  </span>
                </div>
                <p className="text-gray-700">{ins.title || ins.message}</p>
              </div>
            ))}
          </div>
        )
      ) : (
        <>
          {/* 提案操作栏 */}
          <div className="flex items-center gap-2 flex-wrap">
            <button
              onClick={handleReviewAll}
              disabled={busy === -3}
              className="px-2 py-1 text-xs bg-purple-100 text-purple-700 rounded hover:bg-purple-200 disabled:opacity-50"
            >
              批量评审
            </button>
            <button
              onClick={() => handleEvaluate(false)}
              disabled={busy === -4}
              className="px-2 py-1 text-xs bg-blue-100 text-blue-700 rounded hover:bg-blue-200 disabled:opacity-50"
            >
              评估到期提案
            </button>
            <button
              onClick={() => handleEvaluate(true)}
              disabled={busy === -4}
              className="px-2 py-1 text-xs bg-orange-100 text-orange-700 rounded hover:bg-orange-200 disabled:opacity-50"
            >
              立即评估
            </button>
          </div>

          {proposals.length === 0 ? (
            <p className="text-gray-400 text-sm">{loading ? '加载中...' : '暂无提案'}</p>
          ) : (
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {proposals.map((p: any, i: number) => (
                <div key={i} className="border rounded p-3 text-sm hover:bg-gray-50">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                      p.severity === 'critical' ? 'bg-red-200 text-red-800' :
                      p.severity === 'major' ? 'bg-orange-200 text-orange-800' :
                      'bg-blue-200 text-blue-800'
                    }`}>
                      {p.severity || 'info'}
                    </span>
                    <button
                      type="button"
                      className="font-medium text-left hover:text-blue-600 hover:underline"
                      onClick={() => openDetail(p.id)}
                    >
                      <span className="text-gray-400 mr-1">#{p.id}</span>
                      {p.title || `Proposal #${p.id}`}
                    </button>
                    <span className={`ml-auto text-xs px-1.5 py-0.5 rounded ${statusColor(p.status)}`}>
                      {statusLabel(p.status)}
                    </span>
                  </div>
                  <div className="flex items-center gap-1 text-xs text-gray-400 ml-1">
                    <span>{p.patch_type || 'tuning'}</span>
                    {p.created_at && (
                      <span>· {new Date(p.created_at).toLocaleString('zh-CN')}</span>
                    )}
                  </div>
                  {/* 操作按钮 */}
                  <div className="flex gap-1 mt-1.5">
                    {p.status === 'pending' && (
                      <>
                        <button
                          type="button"
                          disabled={busy === p.id}
                          onClick={() => handleReview(p.id)}
                          className="px-2 py-0.5 text-xs bg-purple-100 text-purple-700 rounded hover:bg-purple-200 disabled:opacity-50"
                        >
                          评审
                        </button>
                        <button
                          type="button"
                          disabled={busy === p.id}
                          onClick={() => handleApply(p.id)}
                          className="px-2 py-0.5 text-xs bg-green-100 text-green-700 rounded hover:bg-green-200 disabled:opacity-50"
                        >
                          应用
                        </button>
                        <button
                          type="button"
                          disabled={busy === p.id}
                          onClick={() => handleReject(p.id)}
                          className="px-2 py-0.5 text-xs bg-red-100 text-red-700 rounded hover:bg-red-200 disabled:opacity-50"
                        >
                          拒绝
                        </button>
                      </>
                    )}
                    {['tuning', 'mixed'].includes(p.patch_type || '') && ['paper_applying', 'paper_validated', 'applied', 'validating'].includes(p.status) && (
                      <button
                        type="button"
                        disabled={busy === p.id}
                        onClick={() => handleRollback(p.id)}
                        className="px-2 py-0.5 text-xs bg-orange-100 text-orange-700 rounded hover:bg-orange-200 disabled:opacity-50"
                      >
                        回滚
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* 详情抽屉 */}
          {detailOpen && detail && (
            <div className="fixed inset-0 z-50 flex justify-end">
              <div className="absolute inset-0 bg-black/30" onClick={() => { setDetailOpen(false); setDetail(null); }} />
              <div className="relative w-full max-w-lg bg-white shadow-xl overflow-y-auto p-4">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="font-semibold text-base">提案详情 #{detail.id}</h3>
                  <button
                    onClick={() => { setDetailOpen(false); setDetail(null); }}
                    className="text-gray-400 hover:text-gray-600 text-lg leading-none"
                  >
                    ✕
                  </button>
                </div>

                <div className="space-y-3">
                  {/* 空数据警告 */}
                  {(() => {
                    const p = detail.proposal || {};
                    const patches = p.patches;
                    const isEmpty = !patches || (Array.isArray(patches) && patches.length === 0) || (typeof patches === 'object' && Object.keys(patches).length === 0);
                    return isEmpty ? (
                      <div className="bg-yellow-50 border border-yellow-300 rounded p-3 text-sm">
                        <div className="font-medium text-yellow-700 mb-1">⚠️ 提案数据异常</div>
                        <div className="text-yellow-600 text-xs">
                          该提案的 proposal 内容为空，可能来自数据库直接插入的孤立记录或历史迁移残留。
                          建议删除此记录。
                        </div>
                      </div>
                    ) : null;
                  })()}
                  <div className="flex gap-2 flex-wrap text-sm">
                    <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${statusColor(detail.status)}`}>
                      {statusLabel(detail.status)}
                    </span>
                    <span className="text-gray-500">{detail.patch_type || 'tuning'}</span>
                    {detail.severity && (
                      <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                        detail.severity === 'critical' ? 'bg-red-200 text-red-800' :
                        detail.severity === 'major' ? 'bg-orange-200 text-orange-800' :
                        'bg-blue-200 text-blue-800'
                      }`}>
                        {detail.severity}
                      </span>
                    )}
                  </div>

                  <div>
                    <div className="text-xs font-medium text-gray-500 mb-1">标题</div>
                    <div className="text-sm">{detail.title || '-'}</div>
                  </div>

                  <div>
                    <div className="text-xs font-medium text-gray-500 mb-1">Proposal 内容</div>
                    <pre className="text-xs bg-gray-50 p-2 rounded overflow-auto max-h-32 border">
                      {JSON.stringify(detail.proposal, null, 2)}
                    </pre>
                  </div>

                  {detail.proposal?.review && (
                    <div className="border rounded p-3 bg-purple-50">
                      <div className="text-xs font-medium text-purple-700 mb-2">📋 审核结果</div>
                      <div className="text-sm space-y-1">
                        <div>
                          决策: <strong className={(() => {
                            const d = String((detail.proposal.review as Record<string, unknown>).decision || '-');
                            return d === 'approve' ? 'text-green-600' : d === 'reject' ? 'text-red-600' : 'text-yellow-600';
                          })()}>
                            {(() => {
                              const d = String((detail.proposal.review as Record<string, unknown>).decision || '-');
                              return d === 'approve' ? '✅ 通过' : d === 'reject' ? '❌ 拒绝' : d === 'defer' ? '⏸ 延期' : d;
                            })()}
                          </strong>
                          {' '}
                          置信度: {Number((detail.proposal.review as Record<string, unknown>).confidence ?? 0).toFixed(2)}
                        </div>
                        {Array.isArray((detail.proposal.review as Record<string, unknown>).reasons) && (
                          <div>
                            <div className="text-xs text-gray-500 mt-1">评审理由：</div>
                            <ul className="text-xs text-gray-600 list-disc pl-4 mt-0.5">
                              {((detail.proposal.review as Record<string, unknown>).reasons as string[]).map((r, j) => (
                                <li key={j}>{r}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {Array.isArray((detail.proposal.review as Record<string, unknown>).risks) &&
                          ((detail.proposal.review as Record<string, unknown>).risks as any[]).length > 0 && (
                          <div>
                            <div className="text-xs text-orange-500 mt-1">风险提示：</div>
                            <ul className="text-xs text-orange-600 list-disc pl-4 mt-0.5">
                              {((detail.proposal.review as Record<string, unknown>).risks as any[]).map((r: any, j: number) => (
                                <li key={j}>{typeof r === 'string' ? r : JSON.stringify(r)}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {detail.proposal?.reject_reason && (
                    <div className="text-sm text-red-600 bg-red-50 p-2 rounded">
                      拒绝原因: {String(detail.proposal.reject_reason)}
                    </div>
                  )}

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div>
                      <div className="text-xs font-medium text-gray-500 mb-1">Baseline</div>
                      <pre className="text-xs bg-gray-50 p-2 rounded overflow-auto max-h-32 border">
                        {JSON.stringify(detail.baseline, null, 2)}
                      </pre>
                    </div>
                    <div>
                      <div className="text-xs font-medium text-gray-500 mb-1">After</div>
                      <pre className="text-xs bg-gray-50 p-2 rounded overflow-auto max-h-32 border">
                        {JSON.stringify(detail.after, null, 2)}
                      </pre>
                    </div>
                  </div>

                  <div className="text-xs text-gray-400">
                    创建: {detail.created_at ? new Date(detail.created_at).toLocaleString('zh-CN') : '-'}
                    {detail.applied_at && <> · 应用: {new Date(detail.applied_at).toLocaleString('zh-CN')}</>}
                    {detail.validated_at && <> · 验证: {new Date(detail.validated_at).toLocaleString('zh-CN')}</>}
                  </div>

                  {/* 详情页操作按钮 */}
                  <div className="flex gap-2 pt-2 border-t">
                    {detail.status === 'pending' && (
                      <>
                        <button
                          type="button"
                          onClick={() => { handleReview(detail.id); setDetailOpen(false); }}
                          className="px-3 py-1 text-sm bg-purple-500 text-white rounded hover:bg-purple-600"
                        >
                          触发评审
                        </button>
                        <button
                          type="button"
                          onClick={() => { handleApply(detail.id); setDetailOpen(false); }}
                          className="px-3 py-1 text-sm bg-green-500 text-white rounded hover:bg-green-600"
                        >
                          手动应用
                        </button>
                        <button
                          type="button"
                          onClick={() => { handleReject(detail.id); setDetailOpen(false); }}
                          className="px-3 py-1 text-sm bg-red-500 text-white rounded hover:bg-red-600"
                        >
                          拒绝
                        </button>
                      </>
                    )}
                    {['tuning', 'mixed'].includes(detail.patch_type || '') && ['paper_applying', 'paper_validated', 'applied', 'validating'].includes(detail.status) && (
                      <button
                        type="button"
                        onClick={() => { handleRollback(detail.id); setDetailOpen(false); }}
                        className="px-3 py-1 text-sm bg-orange-500 text-white rounded hover:bg-orange-600"
                      >
                        回滚
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
    </SectionCard>
  );
}

// ─────────────────────────────────────────────
//  Tab 7: 知识库
// ─────────────────────────────────────────────

function KnowledgeTab() {
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('');

  const load = async (categories?: string) => {
    setLoading(true);
    try {
      const data = await queryKnowledge({ categories: categories || undefined, limit: 30 });
      setItems(data.items || []);
    } catch (e) {
      console.error('Failed to load knowledge', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  return (
    <SectionCard
      title="统一知识池"
      action={
        <div className="flex gap-2">
          <Select
            value={filter || 'all'}
            onValueChange={(v) => {
              const next = v === 'all' ? '' : v;
              setFilter(next);
              load(next || undefined);
            }}
          >
            <SelectTrigger className="w-32 h-8 text-xs">
              <SelectValue placeholder="分类" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部</SelectItem>
              <SelectItem value="insight">洞察</SelectItem>
              <SelectItem value="lesson">教训</SelectItem>
              <SelectItem value="narrative">叙事</SelectItem>
              <SelectItem value="pattern">模式</SelectItem>
              <SelectItem value="param_wisdom">参数智慧</SelectItem>
            </SelectContent>
          </Select>
          <RefreshButton onClick={() => load(filter || undefined)} loading={loading} />
        </div>
      }
    >
      {loading ? (
        <EmptyState message="加载中…" />
      ) : items.length === 0 ? (
        <EmptyState message="知识池为空" />
      ) : (
        <div className="space-y-2 max-h-[500px] overflow-y-auto">
          {items.map((item: any, i: number) => (
            <div key={i} className="rounded-md border p-3 text-sm hover:bg-muted/30">
              <div className="flex items-center gap-2 mb-1">
                <Badge variant="outline">{item.source || 'unknown'}</Badge>
                <span className="text-xs text-muted-foreground">{item.category || item.type}</span>
                {item.severity && (
                  <Badge variant={item.severity === 'critical' ? 'destructive' : 'secondary'}>
                    {item.severity}
                  </Badge>
                )}
              </div>
              <p>{item.title || item.lesson}</p>
              {item.ingested_at && (
                <p className="text-xs text-muted-foreground mt-1">
                  {new Date(item.ingested_at).toLocaleString()}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </SectionCard>
  );
}
