/**
 * LearningDashboardPanel — P3 AI 学习仪表盘总览面板
 *
 * 展示：全局概览 / 因子统计 / 策略分布 / 进化进度 / A/B 实验 / 跨市场迁移
 * 数据源：/api/learning/dashboard/*
 * 30s 自动刷新
 */
import { useEffect, useState, useCallback } from 'react';
import {
  getDashboardOverview,
  getDashboardFactors,
  getDashboardStrategies,
  getDashboardEvolution,
  getDashboardExperiments,
  getDashboardTransfer,
  type DashboardOverview,
  type DashboardStrategy,
} from '@/lib/aiLearningApi';

type TabKey = 'overview' | 'factors' | 'strategies' | 'evolution' | 'experiments' | 'transfer';

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'overview', label: '总览' },
  { key: 'factors', label: '因子' },
  { key: 'strategies', label: '策略' },
  { key: 'evolution', label: '进化' },
  { key: 'experiments', label: '实验' },
  { key: 'transfer', label: '迁移' },
];

export default function LearningDashboardPanel() {
  const [tab, setTab] = useState<TabKey>('overview');
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [factors, setFactors] = useState<Record<string, unknown> | null>(null);
  const [strategies, setStrategies] = useState<{ templates: DashboardStrategy[]; evolution_progress: Record<string, unknown>; walk_forward: Record<string, unknown> } | null>(null);
  const [evolution, setEvolution] = useState<Record<string, unknown> | null>(null);
  const [experiments, setExperiments] = useState<Record<string, unknown> | null>(null);
  const [transfer, setTransfer] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    const [o, f, s, ev, ex, tr] = await Promise.all([
      getDashboardOverview(),
      getDashboardFactors(),
      getDashboardStrategies(),
      getDashboardEvolution(),
      getDashboardExperiments(),
      getDashboardTransfer(),
    ]);
    setOverview(o);
    setFactors(f);
    setStrategies(s);
    setEvolution(ev);
    setExperiments(ex);
    setTransfer(tr);
    setLoading(false);
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 30000);
    return () => clearInterval(interval);
  }, [refresh]);

  return (
    <div className="bg-gray-800/60 border border-gray-700 rounded">
      {/* Tab bar */}
      <div className="flex border-b border-gray-700 px-3 pt-2">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={`px-3 py-1.5 text-xs font-medium transition-colors border-b-2 -mb-px ${
              tab === t.key
                ? 'border-cyan-400 text-cyan-400'
                : 'border-transparent text-gray-500 hover:text-gray-300'
            }`}
          >
            {t.label}
          </button>
        ))}
        {loading && (
          <span className="ml-auto text-[10px] text-gray-500 self-center">刷新中…</span>
        )}
      </div>

      {/* Content */}
      <div className="p-3 max-h-96 overflow-y-auto">
        {tab === 'overview' && <OverviewTab overview={overview} />}
        {tab === 'factors' && <FactorsTab factors={factors} />}
        {tab === 'strategies' && <StrategiesTab strategies={strategies} />}
        {tab === 'evolution' && <EvolutionTab evolution={evolution} />}
        {tab === 'experiments' && <ExperimentsTab experiments={experiments} />}
        {tab === 'transfer' && <TransferTab transfer={transfer} />}
      </div>
    </div>
  );
}

/* ── 各 Tab 内容 ── */

function OverviewTab({ overview }: { overview: DashboardOverview | null }) {
  if (!overview) return <div className="text-xs text-gray-500">加载中…</div>;

  const cards = [
    { label: '因子', value: overview.factors.loaded, unit: '个' },
    { label: '策略', value: overview.strategies.active, unit: '个' },
    { label: '进化中', value: overview.strategies.evolving, unit: '个' },
    { label: '教训', value: overview.memory.total_lessons, unit: '条' },
    { label: '进化代数', value: overview.evolution.generation, unit: '代' },
    { label: '运行', value: overview.uptime_hours, unit: 'h', decimals: 1 },
  ];

  return (
    <div>
      <div className="grid grid-cols-3 gap-2 mb-3">
        {cards.map((c) => (
          <div key={c.label} className="bg-gray-900/60 border border-gray-700 rounded px-2.5 py-2 text-center">
            <div className="text-lg font-bold text-white">
              {c.decimals ? (c.value as number).toFixed(c.decimals) : String(c.value)}
            </div>
            <div className="text-[10px] text-gray-500">{c.label}{c.unit && ` (${c.unit})`}</div>
          </div>
        ))}
      </div>

      {/* 每日交易 */}
      {overview.daily_trades !== undefined && (
        <div className="text-xs text-gray-400 mb-2">
          今日交易: {overview.daily_trades} 笔 | PnL: {' '}
          <span className={overview.daily_pnl && overview.daily_pnl >= 0 ? 'text-green-400' : 'text-red-400'}>
            {overview.daily_pnl?.toFixed(2)}
          </span>
        </div>
      )}

      {/* OpenCode */}
      {overview.opencode?.sessions_active > 0 && (
        <div className="text-xs text-gray-400 mb-2">
          OpenCode 活跃会话: {overview.opencode.sessions_active}
        </div>
      )}

      {/* MLTO 学习指标 */}
      {overview.mlto && (
        <div className="border border-purple-800/50 rounded p-2 mb-2 bg-purple-950/20">
          <div className="text-[10px] text-purple-300 font-medium mb-1.5">MLTO 研判学习</div>
          <div className="grid grid-cols-3 gap-2">
            <div className="text-center">
              <div className="text-sm font-bold text-white">
                {overview.mlto.thesis_hit_rate != null
                  ? `${(overview.mlto.thesis_hit_rate * 100).toFixed(0)}%`
                  : '—'}
              </div>
              <div className="text-[10px] text-gray-500">命中率</div>
            </div>
            <div className="text-center">
              <div className="text-sm font-bold text-white">
                {overview.mlto.premature_open_rate != null
                  ? `${(overview.mlto.premature_open_rate * 100).toFixed(0)}%`
                  : '—'}
              </div>
              <div className="text-[10px] text-gray-500">过早开仓</div>
            </div>
            <div className="text-center">
              <div className="text-sm font-bold text-white">
                {(overview.mlto.sample_count ?? 0) >= 5
                  ? overview.mlto.sample_count
                  : '积累中'}
              </div>
              <div className="text-[10px] text-gray-500">样本</div>
            </div>
          </div>
          {(overview.mlto.thesis_drift_resets ?? 0) > 0 && (
            <div className="text-[10px] text-gray-500 mt-1">
              Regime 重置 {overview.mlto.thesis_drift_resets} 次
            </div>
          )}
        </div>
      )}

      {/* 教训主题 */}
      {overview.memory.key_themes.length > 0 && (
        <div>
          <div className="text-[10px] text-gray-500 mb-1">教训主题 Top5</div>
          <div className="flex flex-wrap gap-1">
            {overview.memory.key_themes.map(([theme, count]) => (
              <span key={theme} className="px-2 py-0.5 rounded text-[10px] bg-gray-700 text-gray-300">
                {theme} ×{count}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function FactorsTab({ factors }: { factors: Record<string, unknown> | null }) {
  if (!factors) return <div className="text-xs text-gray-500">加载中…</div>;

  const list = factors.factors as Array<{ name: string; category: string; signal: string }> | undefined;
  const discovery = factors.factor_discovery as Record<string, unknown> | undefined;
  const fusion = factors.factor_fusion as Record<string, unknown> | undefined;

  return (
    <div className="space-y-2">
      {/* 发现 + 融合状态 */}
      <div className="grid grid-cols-2 gap-2">
        <div className="bg-gray-900/60 border border-gray-700 rounded p-2">
          <div className="text-[10px] text-gray-500">因子发现</div>
          <div className="text-xs text-white">
            发现 {String(discovery?.discovered_count ?? 0)} | 验证 {String(discovery?.validated_count ?? 0)}
          </div>
        </div>
        <div className="bg-gray-900/60 border border-gray-700 rounded p-2">
          <div className="text-[10px] text-gray-500">融合引擎</div>
          <div className="text-xs text-white">
            {String(fusion?.mode ?? '—')} | 操作 {String(fusion?.operation_count ?? 0)}
          </div>
        </div>
      </div>

      {/* 因子列表 */}
      {list && (
        <div className="grid grid-cols-2 gap-1">
          {list.slice(0, 20).map((f, i) => (
            <div key={i} className="flex items-center justify-between text-[10px] bg-gray-900/40 px-2 py-1 rounded">
              <span className="text-gray-300 truncate">{f.name}</span>
              <span className={`ml-1 ${f.signal === 'bullish' ? 'text-green-400' : f.signal === 'bearish' ? 'text-red-400' : 'text-gray-500'}`}>
                {f.signal === 'neutral' ? '—' : f.signal}
              </span>
            </div>
          ))}
        </div>
      )}
      {list && list.length > 20 && (
        <div className="text-[10px] text-gray-500">显示 20/{list.length} 个因子</div>
      )}
    </div>
  );
}

function StrategiesTab({ strategies }: { strategies: { templates: DashboardStrategy[]; evolution_progress: Record<string, unknown>; walk_forward: Record<string, unknown> } | null }) {
  if (!strategies) return <div className="text-xs text-gray-500">加载中…</div>;

  const { templates } = strategies;

  // 按 tier 统计
  const tierCounts: Record<string, number> = {};
  templates.forEach((t) => {
    const tier = t.tier || 'unknown';
    tierCounts[tier] = (tierCounts[tier] || 0) + 1;
  });

  // 按 status 统计
  const statusCounts: Record<string, number> = {};
  templates.forEach((t) => {
    const st = t.status || 'unknown';
    statusCounts[st] = (statusCounts[st] || 0) + 1;
  });

  return (
    <div className="space-y-2">
      {/* 分布统计 */}
      <div className="grid grid-cols-2 gap-2">
        <div className="bg-gray-900/60 border border-gray-700 rounded p-2">
          <div className="text-[10px] text-gray-500 mb-1">Tier 分布</div>
          {Object.entries(tierCounts).map(([k, v]) => (
            <div key={k} className="flex justify-between text-[10px]">
              <span className="text-gray-300">{k}</span>
              <span className="text-white">{v}</span>
            </div>
          ))}
        </div>
        <div className="bg-gray-900/60 border border-gray-700 rounded p-2">
          <div className="text-[10px] text-gray-500 mb-1">状态分布</div>
          {Object.entries(statusCounts).map(([k, v]) => (
            <div key={k} className="flex justify-between text-[10px]">
              <span className="text-gray-300">{k}</span>
              <span className="text-white">{v}</span>
            </div>
          ))}
        </div>
      </div>

      {/* 策略列表 */}
      <div className="space-y-1 max-h-60 overflow-y-auto">
        {templates.slice(0, 15).map((t) => (
          <div key={t.template_id} className="flex items-center justify-between text-[10px] bg-gray-900/40 px-2 py-1 rounded">
            <div className="flex-1 truncate">
              <span className="text-gray-300">{t.name}</span>
              <span className="text-gray-600 ml-1">({t.symbol})</span>
            </div>
            <div className="flex gap-2 shrink-0 ml-2">
              <span className="text-gray-500">{t.tier}</span>
              <span className={t.status === 'evolving' ? 'text-yellow-400' : t.status === 'promoted' ? 'text-green-400' : 'text-gray-400'}>
                {t.status}
              </span>
              {t.sharpe != null && (
                <span className={t.sharpe >= 1 ? 'text-green-400' : 'text-gray-500'}>
                  SR:{t.sharpe.toFixed(2)}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
      {templates.length > 15 && (
        <div className="text-[10px] text-gray-500">显示 15/{templates.length} 个策略</div>
      )}
    </div>
  );
}

function EvolutionTab({ evolution }: { evolution: Record<string, unknown> | null }) {
  if (!evolution) return <div className="text-xs text-gray-500">加载中…</div>;

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-3 gap-2">
        <div className="bg-gray-900/60 border border-gray-700 rounded p-2 text-center">
          <div className="text-lg font-bold text-white">{String(evolution.generation ?? 0)}</div>
          <div className="text-[10px] text-gray-500">进化代数</div>
        </div>
        <div className="bg-gray-900/60 border border-gray-700 rounded p-2 text-center">
          <div className="text-lg font-bold text-white">{String(evolution.active_populations ?? 0)}</div>
          <div className="text-[10px] text-gray-500">活跃种群</div>
        </div>
        <div className="bg-gray-900/60 border border-gray-700 rounded p-2 text-center">
          <div className="text-sm font-bold text-white">
            {typeof evolution.is_running === 'boolean'
              ? evolution.is_running ? '✅' : '⏸'
              : '—'}
          </div>
          <div className="text-[10px] text-gray-500">
            {typeof evolution.is_running === 'boolean'
              ? evolution.is_running ? '运行中' : '已暂停'
              : '未知'}
          </div>
        </div>
      </div>

      {/* 记忆衰减 */}
      {(evolution.memory_decay as object | undefined) && (
        <div className="bg-gray-900/60 border border-gray-700 rounded p-2">
          <div className="text-[10px] text-gray-500">记忆衰减</div>
          <pre className="text-[10px] text-gray-300 mt-1 whitespace-pre-wrap">
            {JSON.stringify(evolution.memory_decay, null, 2)}
          </pre>
        </div>
      )}

      {/* 交易叙事 */}
      {(evolution.narrative as object | undefined) && (
        <div className="bg-gray-900/60 border border-gray-700 rounded p-2">
          <div className="text-[10px] text-gray-500">交易叙事</div>
          <pre className="text-[10px] text-gray-300 mt-1 whitespace-pre-wrap">
            {JSON.stringify(evolution.narrative, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

function ExperimentsTab({ experiments }: { experiments: Record<string, unknown> | null }) {
  if (!experiments) return <div className="text-xs text-gray-500">加载中…</div>;

  if (experiments.error) {
    return <div className="text-xs text-red-400">错误：{String(experiments.error)}</div>;
  }

  const items = experiments.experiments as Array<Record<string, unknown>> | undefined;

  return (
    <div className="space-y-1">
      {items && items.length > 0 ? (
        items.map((exp, i) => (
          <div key={i} className="bg-gray-900/60 border border-gray-700 rounded p-2">
            <div className="flex items-center justify-between text-[10px]">
              <span className="text-gray-300">{String(exp.experiment_id ?? `#${i + 1}`)}</span>
              <span className="text-gray-500">{String(exp.control_group ?? '—')} vs {String(exp.experiment_group ?? '—')}</span>
            </div>
            <div className="flex gap-2 mt-1 text-[11px]">
              <span className={`${String(exp.winner) === String(exp.experiment_group) ? 'text-green-400' : 'text-gray-500'}`}>
                状态: {String(exp.status ?? '—')}
              </span>
              {exp.p_value != null && (
                <span className="text-gray-500">p={(exp.p_value as number).toFixed(4)}</span>
              )}
              {(exp.winner as string | undefined) && (
                <span className="text-yellow-400">胜:{String(exp.winner)}</span>
              )}
            </div>
          </div>
        ))
      ) : (
        <div className="text-xs text-gray-500">暂无活跃实验</div>
      )}
    </div>
  );
}

function TransferTab({ transfer }: { transfer: Record<string, unknown> | null }) {
  if (!transfer) return <div className="text-xs text-gray-500">加载中…</div>;

  if (transfer.error) {
    return <div className="text-xs text-red-400">错误：{String(transfer.error)}</div>;
  }

  return (
    <div className="space-y-2">
      <div className="bg-gray-900/60 border border-gray-700 rounded p-2">
        <div className="text-[10px] text-gray-500">跨市场迁移状态</div>
        <pre className="text-[10px] text-gray-300 mt-1 whitespace-pre-wrap max-h-48 overflow-y-auto">
          {JSON.stringify(transfer, null, 2)}
        </pre>
      </div>
    </div>
  );
}
