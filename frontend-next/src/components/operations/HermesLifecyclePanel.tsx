/**
 * Hermes 生命周期总览（S2-11）
 *
 * 将 Hermes 自进化的完整生命周期串联为一个视图：
 * - 生命周期阶段判定（萌芽/成长/成熟/自治，基于成熟度评分）
 * - L1-L4 四层成熟度进度
 * - 健康巡检（DB / Sidecar）
 * - 智慧闭环链路：L1 高置信模式 → 参数域扩展 → GA 进化搜索域
 * - L1-L4 四层调度心跳
 */
import { useCallback, useEffect, useState } from 'react';
import {
  getHermesMaturity,
  getHermesHealth,
  getHermesSchedule,
  getHermesPatterns,
  type MaturityScore,
  type HermesHealth,
  type HermesTaskSchedule,
} from '@/lib/hermesApi';
import { getParamDomain, type ParamDomainResponse } from '@/lib/intelligentLearningApi';
import { SectionCard, RefreshButton, StatCard } from './IlcUi';
import { Badge } from '@/components/ui/badge';
import {
  Sparkles,
  Brain,
  FileText,
  Layers,
  FlaskConical,
} from 'lucide-react';

// ──── 生命周期阶段判定（Aurora 语义色） ────

const STAGE_LIST = [
  { min: 75, label: '自治', color: 'text-profit bg-profit/15 border-profit/40' },
  { min: 50, label: '成熟', color: 'text-cyan-300 bg-cyan-400/15 border-cyan-400/40' },
  { min: 25, label: '成长', color: 'text-warning bg-warning/15 border-warning/40' },
  { min: 0, label: '萌芽', color: 'text-muted-foreground bg-white/5 border-border' },
];

function stageOf(score: number | null | undefined) {
  if (score == null) return STAGE_LIST[STAGE_LIST.length - 1];
  return STAGE_LIST.find((s) => score >= s.min) ?? STAGE_LIST[STAGE_LIST.length - 1];
}

const LAYER_META = [
  { key: 'L1', label: '提案智慧', field: 'l1_wisdom' as const, icon: Brain },
  { key: 'L2', label: 'Prompt 进化', field: 'l2_prompt' as const, icon: FileText },
  { key: 'L3', label: '架构进化', field: 'l3_architecture' as const, icon: Layers },
  { key: 'L4', label: '策略创生', field: 'l4_genesis' as const, icon: FlaskConical },
];

const LAYER_COLOR: Record<string, string> = {
  L1: 'text-cyan-400',
  L2: 'text-amber-400',
  L3: 'text-violet-400',
  L4: 'text-emerald-400',
};

const STATUS_STYLE: Record<string, { label: string; dot: string; text: string }> = {
  running: { label: '运行中', dot: 'bg-cyan-400 animate-pulse shadow-[0_0_8px_rgba(34,211,238,0.8)]', text: 'text-cyan-300' },
  ok: { label: '正常', dot: 'bg-profit shadow-[0_0_6px_rgba(52,211,153,0.8)]', text: 'text-profit' },
  error: { label: '失败', dot: 'bg-loss shadow-[0_0_6px_rgba(251,113,133,0.8)]', text: 'text-loss' },
};

function fmtClock(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '—';
  }
}

export function HermesLifecyclePanel() {
  const [maturity, setMaturity] = useState<MaturityScore | null>(null);
  const [health, setHealth] = useState<HermesHealth | null>(null);
  const [tasks, setTasks] = useState<HermesTaskSchedule[]>([]);
  const [patternsCount, setPatternsCount] = useState(0);
  const [paramDomain, setParamDomain] = useState<ParamDomainResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.allSettled([
      getHermesMaturity(),
      getHermesHealth(),
      getHermesSchedule(),
      getHermesPatterns(2),
      getParamDomain(),
    ]).then(([m, h, s, p, pd]) => {
      if (m.status === 'fulfilled') setMaturity(m.value);
      if (h.status === 'fulfilled') setHealth(h.value);
      if (s.status === 'fulfilled') setTasks(s.value.tasks ?? []);
      if (p.status === 'fulfilled') setPatternsCount(p.value.patterns?.length ?? 0);
      if (pd.status === 'fulfilled') setParamDomain(pd.value);
      const failed = [m, h, s, p, pd].filter((r) => r.status === 'rejected');
      if (failed.length > 0) setError(`${failed.length} 个数据源加载失败（Hermes API 或智能学习中心未启动）`);
      setLoading(false);
    });
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 60000);
    return () => clearInterval(t);
  }, [refresh]);

  const score = maturity?.maturity_score ?? null;
  const stage = stageOf(score);
  const details = maturity?.details;
  const dbOk = health?.db_ok !== false;
  const registeredTasks = tasks.filter((t) => t.registered);
  const cfg = paramDomain?.cfg;
  const expandedCount = paramDomain?.expanded_count ?? 0;
  const patternTotal = paramDomain?.patterns?.total ?? patternsCount;

  return (
    <div className="space-y-4">
      {/* ──── 生命周期状态行 ──── */}
      <SectionCard
        title="生命周期总览"
        description="成熟度评分 → 阶段判定 → 健康巡检 → 四层调度心跳"
        action={<RefreshButton onClick={refresh} loading={loading} />}
      >
        {error && <p className="text-sm text-loss mb-3">{error}</p>}

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <div className="rounded-lg border p-3 glass">
            <div className="text-xs text-muted-foreground mb-1 flex items-center gap-1">
              <Sparkles className="w-3 h-3 text-violet-400" /> 成熟度评分
            </div>
            <div className="flex items-baseline gap-2">
              <span
                className={`text-2xl font-bold tabular-nums ${
                  (score ?? 0) >= 75
                    ? 'grad-text-green'
                    : (score ?? 0) >= 50
                      ? 'grad-text'
                      : (score ?? 0) >= 25
                        ? 'text-warning'
                        : 'text-muted-foreground'
                }`}
              >
                {score != null ? Math.round(score) : '—'}
              </span>
              <Badge variant="outline" className={`font-normal ${stage.color}`}>
                {stage.label}
              </Badge>
            </div>
          </div>
          <StatCard
            label="健康巡检"
            value={dbOk ? 'DB 正常' : 'DB 异常'}
            tone={dbOk ? 'good' : 'warn'}
            hint={health?.sidecar_ok ? 'Sidecar 在线' : 'Sidecar 离线'}
          />
          <StatCard
            label="L1 智慧记录"
            value={details?.wisdom_records ?? 0}
            hint={`高置信模式 ${details?.high_confidence_patterns ?? 0} 条`}
          />
          <StatCard
            label="调度任务"
            value={registeredTasks.length}
            hint={`${tasks.length} 个已注册 / 心跳正常`}
            tone={registeredTasks.length > 0 ? 'good' : 'default'}
          />
        </div>

        {/* 四层成熟度进度 */}
        <div className="space-y-2 mb-1">
          {LAYER_META.map((layer) => {
            const v = maturity ? (maturity[layer.field] as number) : 0;
            const Icon = layer.icon;
            return (
              <div key={layer.key} className="flex items-center gap-2 text-sm min-w-0">
                <Icon className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                <span className={`w-8 font-mono font-semibold text-xs shrink-0 ${LAYER_COLOR[layer.key] ?? ''}`}>
                  {layer.key}
                </span>
                <span className="w-24 text-muted-foreground text-xs shrink-0">{layer.label}</span>
                <div className="flex-1 min-w-0 h-1.5 bg-white/10 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-cyan-400 to-violet-500 rounded-full transition-all"
                    style={{ width: `${Math.min(100, (v / 25) * 100)}%` }}
                  />
                </div>
                <span className="w-10 text-right text-xs font-mono text-muted-foreground">
                  {v.toFixed(1)}/25
                </span>
              </div>
            );
          })}
        </div>
      </SectionCard>

      {/* ──── 智慧闭环链路 ──── */}
      <SectionCard
        title="智慧闭环链路"
        description="L1 实盘归因高置信模式 → 参数域动态扩展 → GA 进化搜索（S2-10 通道一/二联动）"
      >
        <div className="grid md:grid-cols-[1fr_auto_1fr_auto_1fr] gap-3 items-stretch mb-3">
          <div className="rounded-lg border p-3 text-center">
            <div className="text-xs text-muted-foreground mb-1">L1 高置信模式</div>
            <div className="text-2xl font-bold tabular-nums">{patternTotal}</div>
            <div className="text-[10px] text-muted-foreground mt-1">
              ↑ {paramDomain?.patterns?.by_direction?.increase ?? 0} / ↓{' '}
              {paramDomain?.patterns?.by_direction?.decrease ?? 0}
            </div>
          </div>
          <div className="flex items-center justify-center text-muted-foreground">→</div>
          <div className="rounded-lg border p-3 text-center">
            <div className="text-xs text-muted-foreground mb-1">参数域扩展</div>
            <div className="text-2xl font-bold tabular-nums">{expandedCount}</div>
            <div className="text-[10px] text-muted-foreground mt-1">
              {cfg?.enabled ? `系数 ${cfg.expand_ratio} / 封顶 ${cfg.expand_max}` : '未启用（env=false）'}
            </div>
          </div>
          <div className="flex items-center justify-center text-muted-foreground">→</div>
          <div className="rounded-lg border p-3 text-center">
            <div className="text-xs text-muted-foreground mb-1">GA 进化搜索域</div>
            <div className="text-2xl font-bold tabular-nums">
              {Object.keys(paramDomain?.expanded_ranges ?? {}).length}
            </div>
            <div className="text-[10px] text-muted-foreground mt-1">
              全参数域（基础 + 扩展）
            </div>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          链路：hermes L1 智慧模式（param_effect_patterns, outcome=improved）→
          param_domain_expander（increase 上界×1.2 / decrease 下界÷1.2，封顶×1.5）→
          evolution_scheduler 搜索域注入。智慧证据与搜索空间共享同一份演化记忆。
        </p>
      </SectionCard>

      {/* ──── 四层调度心跳 ──── */}
      <SectionCard
        title="四层调度心跳"
        description="L1 智慧积累 → L2 Prompt 进化 → L3 架构进化 → L4 策略创生"
      >
        {tasks.length === 0 ? (
          <p className="text-sm text-muted-foreground py-4 text-center">
            暂无调度信息（需系统启动后自动注册）
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs min-w-[560px]">
              <thead>
                <tr className="text-muted-foreground border-b">
                  <th className="text-left font-medium py-2 pr-2">层</th>
                  <th className="text-left font-medium py-2 pr-2">任务</th>
                  <th className="text-left font-medium py-2 pr-2">间隔</th>
                  <th className="text-left font-medium py-2 pr-2">上次开始</th>
                  <th className="text-left font-medium py-2 pr-2">预计下次</th>
                  <th className="text-left font-medium py-2">状态</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((t) => {
                  const running = t.is_running;
                  const stKey = running ? 'running' : t.last_status || '';
                  const st = STATUS_STYLE[stKey] || {
                    label: t.registered ? '待运行' : '未注册',
                    dot: 'bg-white/25',
                    text: 'text-muted-foreground',
                  };
                  return (
                    <tr key={t.job_id} className="border-b border-border/20 last:border-0 hover:bg-white/[0.03] transition-colors">
                      <td className={`py-2 pr-2 font-mono font-semibold ${LAYER_COLOR[t.layer] ?? ''}`}>
                        {t.layer}
                      </td>
                      <td className="py-2 pr-2">
                        <div className="font-medium">{t.label}</div>
                        <div className="text-muted-foreground text-[10px]">{t.desc}</div>
                      </td>
                      <td className="py-2 pr-2 font-mono text-muted-foreground">{fmtInterval(t.interval_s)}</td>
                      <td className="py-2 pr-2 font-mono">{fmtClock(t.last_started_at)}</td>
                      <td className="py-2 pr-2 font-mono">
                        {t.registered ? fmtClock(t.next_run_time) : '—'}
                      </td>
                      <td className="py-2">
                        <span className={`inline-flex items-center gap-1 ${st.text}`}>
                          <span className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
                          {st.label}
                        </span>
                        {t.last_error && (
                          <div className="text-[10px] text-loss mt-0.5 break-all leading-snug max-w-[200px]">
                            {t.last_error}
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </SectionCard>
    </div>
  );
}

function fmtInterval(s: number): string {
  if (s >= 86400 && s % 86400 === 0) return `${s / 86400}d`;
  if (s >= 3600 && s % 3600 === 0) return `${s / 3600}h`;
  if (s >= 60) return `${Math.round(s / 60)}min`;
  return `${s}s`;
}

export default HermesLifecyclePanel;
