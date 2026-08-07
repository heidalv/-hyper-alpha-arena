/**
 * 学习三通道看板 — 通道一：Wisdom 闭环
 *
 * 展示 WisdomTracker 的完整闭环数据：
 * - 验证强度排序 Top 智慧（eff × 质量命中权重 × log1p(applied)）
 * - 总览统计（总数 / 活跃 / 停用 / 按类型分布）
 * - 净扣费与质量闸门配置说明
 */
import { useEffect, useState } from 'react';
import {
  getWisdomLoop,
  type WisdomLoopResponse,
  type WisdomRankedItem,
} from '@/lib/intelligentLearningApi';
import { SectionCard, RefreshButton, StatCard } from './IlcUi';
import { Badge } from '@/components/ui/badge';

const TYPE_LABEL: Record<string, string> = {
  risk: '风控',
  regime: 'regime',
  signal: '信号',
  lesson: '教训',
};

export function WisdomLoopPanel() {
  const [data, setData] = useState<WisdomLoopResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = () => {
    setLoading(true);
    getWisdomLoop()
      .then(setData)
      .catch(() => setData({ error: '加载失败' }))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 60000);
    return () => clearInterval(t);
  }, []);

  const report = data?.report ?? {};
  const ranked = data?.ranked ?? [];
  const byType = report.by_type ?? {};

  return (
    <SectionCard
      title="通道一 · Wisdom 闭环"
      description="净扣费 tanh(|pnl|/50) 金额加权信号 + 质量闸门（|pnl_pct|≥0.3% 或 |pnl|≥$1）+ 验证强度排序"
      action={<RefreshButton onClick={refresh} loading={loading} />}
    >
      {data?.error && <p className="text-sm text-red-500 mb-3">{data.error}</p>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <StatCard label="智慧总数" value={report.total ?? 0} />
        <StatCard label="活跃" value={report.active ?? 0} tone="good" />
        <StatCard label="已停用" value={report.deactivated ?? 0} tone="warn" />
        <StatCard
          label="质量命中样本"
          value={ranked.reduce((s, r) => s + (r.quality_hit_count ?? 0), 0)}
          hint="通过质量闸门的净盈利样本"
        />
      </div>

      {Object.keys(byType).length > 0 && (
        <div className="flex flex-wrap gap-2 mb-4">
          {Object.entries(byType).map(([type, t]) => (
            <Badge key={type} variant="outline" className="font-normal">
              {TYPE_LABEL[type] ?? type}: {t.count} 条 / 均分 {t.avg_effectiveness ?? 0} /
              应用 {t.total_applied ?? 0} 次
            </Badge>
          ))}
        </div>
      )}

      {ranked.length === 0 ? (
        <p className="text-sm text-muted-foreground py-6 text-center">
          暂无智慧样本（回测产出后自动注入）
        </p>
      ) : (
        <div className="space-y-2">
          {ranked.map((w: WisdomRankedItem, idx: number) => (
            <div
              key={w.id}
              className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
            >
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-muted-foreground w-6 text-right tabular-nums">
                  {idx + 1}
                </span>
                <Badge variant="secondary" className="font-normal shrink-0">
                  {TYPE_LABEL[w.type ?? ''] ?? w.type ?? 'unknown'}
                </Badge>
                <span className="text-muted-foreground shrink-0">{w.tier ?? '-'}</span>
                <span className="truncate font-mono text-xs">{w.template_id}</span>
              </div>
              <div className="flex items-center gap-3 shrink-0 tabular-nums">
                <span className={getScoreClass(w.effectiveness)}>
                  {fmtPct(w.effectiveness)}
                </span>
                <span className="text-xs text-muted-foreground">
                  命中 {w.quality_hit_count ?? 0}/{w.evaluation_count ?? 0} · 应用{' '}
                  {w.applied_count ?? 0}
                </span>
                <span className="text-xs text-muted-foreground">强度 {w.strength?.toFixed(3)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </SectionCard>
  );
}

function getScoreClass(v?: number | null) {
  if (v == null) return 'text-muted-foreground';
  if (v >= 0.6) return 'text-green-600 dark:text-green-400 font-semibold';
  if (v >= 0.35) return 'text-amber-600 dark:text-amber-400 font-semibold';
  return 'text-red-600 dark:text-red-400 font-semibold';
}

function fmtPct(v?: number | null) {
  if (v == null) return 'n/a';
  return `${(v * 100).toFixed(1)}%`;
}

export default WisdomLoopPanel;
