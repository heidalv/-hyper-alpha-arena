/**
 * 决策链路视图 — AI 决策 → Wisdom 应用 → 交易结果评估
 *
 * 从 ai_decision_logs 提取带 wisdom_applied 的最近决策，
 * 关联 trading_wisdom 详情与 realized_pnl，展示完整闭环链路。
 */
import { useEffect, useState } from 'react';
import {
  getDecisionChain,
  type DecisionChainResponse,
} from '@/lib/intelligentLearningApi';
import { SectionCard, RefreshButton, StatCard } from './IlcUi';
import { Badge } from '@/components/ui/badge';

const OP_LABEL: Record<string, { text: string; tone: string }> = {
  buy: { text: '买入', tone: 'text-green-600 dark:text-green-400' },
  sell: { text: '卖出', tone: 'text-red-600 dark:text-red-400' },
  hold: { text: '持有', tone: 'text-muted-foreground' },
};

export function DecisionChainPanel() {
  const [data, setData] = useState<DecisionChainResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = () => {
    setLoading(true);
    getDecisionChain(20)
      .then(setData)
      .catch(() => setData({ error: '加载失败' }))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 60000);
    return () => clearInterval(t);
  }, []);

  const chain = data?.chain ?? [];

  return (
    <SectionCard
      title="决策链路视图"
      description="AI 决策（wisdom_applied）→ 智慧评分 → 交易结果（realized_pnl）"
      action={<RefreshButton onClick={refresh} loading={loading} />}
    >
      {data?.error && <p className="text-sm text-red-500 mb-3">{data.error}</p>}

      <div className="grid grid-cols-3 gap-3 mb-4">
        <StatCard label="决策总数" value={data?.total_decisions ?? 0} />
        <StatCard label="智慧覆盖决策" value={data?.wisdom_covered ?? 0} tone="good" />
        <StatCard
          label="覆盖采样"
          value={`${data?.sampled ?? 0} 条`}
          hint="最近带智慧应用的决策"
        />
      </div>

      {chain.length === 0 ? (
        <p className="text-sm text-muted-foreground py-6 text-center">
          暂无带 wisdom_applied 的决策记录（智慧注入后自动出现）
        </p>
      ) : (
        <div className="space-y-2">
          {chain.map((d) => {
            const op = OP_LABEL[d.operation ?? ''] ?? {
              text: d.operation ?? '?',
              tone: 'text-muted-foreground',
            };
            const pnl = d.realized_pnl;
            return (
              <div
                key={d.id}
                className="rounded-md border px-3 py-2 text-sm"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="font-medium shrink-0">{d.symbol ?? '-'}</span>
                    <span className={`${op.tone} shrink-0`}>{op.text}</span>
                    <Badge variant="outline" className="font-normal shrink-0">
                      {d.decision_source ?? 'llm'}
                    </Badge>
                    <span className="text-xs text-muted-foreground truncate">
                      #{d.id} · {fmtTime(d.decision_time)}
                    </span>
                  </div>
                  <span
                    className={`text-xs font-semibold tabular-nums shrink-0 ${
                      pnl == null
                        ? 'text-muted-foreground'
                        : pnl > 0
                          ? 'text-green-600 dark:text-green-400'
                          : pnl < 0
                            ? 'text-red-600 dark:text-red-400'
                            : 'text-muted-foreground'
                    }`}
                  >
                    {pnl == null ? '未平仓' : `$${pnl.toFixed(2)}`}
                  </span>
                </div>
                {d.wisdoms.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-2 pl-1">
                    {d.wisdoms.map((w, i) => (
                      <span
                        key={i}
                        className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
                      >
                        {w.type ?? '?'}/{w.tier ?? '-'}
                        <span className={scoreCls(w.effectiveness)}>
                          {w.effectiveness == null ? 'n/a' : `${(w.effectiveness * 100).toFixed(0)}%`}
                        </span>
                        <span>
                          {w.quality_hit_count ?? 0}/{w.evaluation_count ?? 0} 命中
                        </span>
                        {w.is_active === false && (
                          <span className="text-red-500 font-medium">已停用</span>
                        )}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </SectionCard>
  );
}

function scoreCls(v?: number | null) {
  if (v == null) return 'text-muted-foreground';
  if (v >= 0.6) return 'text-green-600 dark:text-green-400 font-medium';
  if (v >= 0.35) return 'text-amber-600 dark:text-amber-400 font-medium';
  return 'text-red-600 dark:text-red-400 font-medium';
}

function fmtTime(iso?: string | null) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

export default DecisionChainPanel;
