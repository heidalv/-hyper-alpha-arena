/**
 * 学习三通道看板 — 通道二：参数域扩展
 *
 * Hermes L1 实盘归因的高置信模式（param_effect_patterns outcome=improved）
 * 反哺 GA 搜索域：increase → 上界 ×1.2 / decrease → 下界 ÷1.2，总封顶 ×1.5。
 */
import { useEffect, useState } from 'react';
import {
  getParamDomain,
  type ParamDomainResponse,
} from '@/lib/intelligentLearningApi';
import { SectionCard, RefreshButton, StatCard } from './IlcUi';
import { Badge } from '@/components/ui/badge';

export function ParamDomainPanel() {
  const [data, setData] = useState<ParamDomainResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = () => {
    setLoading(true);
    getParamDomain()
      .then(setData)
      .catch(() => setData({ error: '加载失败' }))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 60000);
    return () => clearInterval(t);
  }, []);

  const patterns = data?.patterns ?? {};
  const byDir = patterns.by_direction ?? {};
  const byKey = patterns.by_key ?? {};
  const changes = data?.changes ?? [];
  const cfg = data?.cfg;
  const baseRanges = data?.base_ranges ?? {};
  const expandedRanges = data?.expanded_ranges ?? {};

  return (
    <SectionCard
      title="通道二 · 参数域扩展"
      description="Hermes L1 高置信模式 → GA 搜索域动态扩展（智慧证据反哺进化）"
      action={<RefreshButton onClick={refresh} loading={loading} />}
    >
      {data?.error && <p className="text-sm text-red-500 mb-3">{data.error}</p>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <StatCard label="高置信模式" value={patterns.total ?? 0} />
        <StatCard label="↑ increase" value={byDir.increase ?? 0} tone="good" />
        <StatCard label="↓ decrease" value={byDir.decrease ?? 0} tone="warn" />
        <StatCard
          label="已扩展参数"
          value={data?.expanded_count ?? 0}
          tone={data?.expanded_count ? 'good' : 'default'}
          hint={cfg?.enabled ? `系数 ${cfg.expand_ratio} / 封顶 ${cfg.expand_max}` : '未启用'}
        />
      </div>

      {Object.keys(byKey).length > 0 && (
        <div className="flex flex-wrap gap-2 mb-4">
          {Object.entries(byKey).map(([key, k]) => (
            <Badge key={key} variant="outline" className="font-normal">
              {key}: ↑{k.increase} / ↓{k.decrease} · 均影响 {k.avg_pnl_impact ?? 0}
            </Badge>
          ))}
        </div>
      )}

      {changes.length > 0 ? (
        <div className="space-y-2">
          {changes.map((c) => {
            const base = baseRanges[c.param_key];
            const expanded = expandedRanges[c.param_key];
            const isUp = c.direction === 'increase';
            return (
              <div
                key={c.param_key}
                className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <Badge
                    variant={isUp ? 'default' : 'destructive'}
                    className="font-normal shrink-0"
                  >
                    {isUp ? '↑ 上界扩展' : '↓ 下界扩展'}
                  </Badge>
                  <span className="font-mono text-xs truncate">{c.param_key}</span>
                  <span className="text-xs text-muted-foreground shrink-0">
                    {c.n_patterns} 条模式
                  </span>
                </div>
                <span className="text-xs tabular-nums shrink-0 text-muted-foreground">
                  {base ? fmtRange(base) : '-'} → {expanded ? fmtRange(expanded) : '-'}
                </span>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground py-6 text-center">
          {cfg?.enabled
            ? '暂无高置信模式，搜索域保持基础范围'
            : '参数域扩展未启用（PARAM_DOMAIN_EXPAND_ENABLED=false）'}
        </p>
      )}
    </SectionCard>
  );
}

function fmtRange([lo, hi]: [number, number]) {
  return `[${lo}, ${hi}]`;
}

export default ParamDomainPanel;
