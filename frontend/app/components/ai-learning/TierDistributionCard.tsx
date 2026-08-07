import { useEffect, useState } from 'react';
import { getTierDistribution, type TierDistributionResult } from '@/lib/aiLearningApi';

/**
 * TierDistributionCard — 策略 Tier 分布与 quota 对比
 * v3 整改：配合 /api/ai-strategies/stats/tier-distribution，可视化 mid-skew 偏斜。
 *
 * - deviation > +0.15  超额（红）
 * - deviation < -0.15  缺位（黄）
 * - 否则 均衡（绿）
 */
export default function TierDistributionCard({
  accountId,
  refreshMs = 60000,
}: {
  accountId?: number;
  refreshMs?: number;
}) {
  const [data, setData] = useState<TierDistributionResult | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      const d = await getTierDistribution(accountId);
      if (!cancelled) setData(d);
      setLoading(false);
    };
    load();
    const t = setInterval(load, refreshMs);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [accountId, refreshMs]);

  if (!data || data.total === 0) {
    return (
      <div className="text-gray-500 text-xs p-2">
        {loading ? '加载中…' : '暂无活跃策略'}
      </div>
    );
  }

  const tiers: Array<'short' | 'mid' | 'long'> = ['short', 'mid', 'long'];
  const tierLabel: Record<string, string> = { short: '短周期', mid: '中周期', long: '长周期' };

  const statusOf = (dev: number) => {
    if (dev > 0.15) return { color: 'text-red-400', bg: 'bg-red-500', label: '超额' };
    if (dev < -0.15) return { color: 'text-yellow-400', bg: 'bg-yellow-500', label: '缺位' };
    return { color: 'text-green-400', bg: 'bg-green-500', label: '均衡' };
  };

  return (
    <div className="bg-gray-800 border border-gray-700 rounded p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="text-xs font-bold text-gray-300">周期分布（活跃策略 {data.total}）</div>
        <div className="text-[10px] text-gray-500">未分类：{data.distribution.unknown}</div>
      </div>

      <div className="space-y-2">
        {tiers.map((t) => {
          const ratio = data.ratio[t] || 0;
          const quota = data.quota[t] || 0;
          const dev = data.deviation[t] || 0;
          const st = statusOf(dev);
          const widthPct = Math.min(100, ratio * 100);
          const quotaLeftPct = Math.min(100, quota * 100);
          return (
            <div key={t} className="">
              <div className="flex justify-between text-[11px] mb-0.5">
                <span className="text-gray-300">
                  {tierLabel[t]}{' '}
                  <span className="text-gray-500">
                    ({data.distribution[t]})
                  </span>
                </span>
                <span className={`font-mono ${st.color}`}>
                  当前 {(ratio * 100).toFixed(1)}% / 目标 {(quota * 100).toFixed(0)}%{' '}
                  <span className="text-[9px]">· {st.label}</span>
                </span>
              </div>
              <div className="relative h-2 bg-gray-900 rounded overflow-hidden">
                <div
                  className={`absolute left-0 top-0 h-full ${st.bg}`}
                  style={{ width: `${widthPct}%` }}
                />
                <div
                  className="absolute top-0 h-full border-l border-white/70"
                  style={{ left: `${quotaLeftPct}%`, width: '1px' }}
                  title={`目标 ${(quota * 100).toFixed(0)}%`}
                />
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-2 text-[10px] text-gray-500">
        白色竖线为目标配额参考；当前占比与目标偏差 &gt; 15% 时会高亮提示。
      </div>
    </div>
  );
}
