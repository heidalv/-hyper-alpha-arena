import { useEffect, useState } from 'react';
import { getBlockReportTop, type BlockReportResult } from '@/lib/aiLearningApi';

/**
 * BlockReportTop3 — 阻断事件 Top-N
 * v3 整改：解释"为什么今天没开单"，来源 /api/system/block-report-top。
 *
 * 仅聚合进程内最近 hours 小时内的阻断事件（风控/冷却/防守/熔断等）。
 */
export default function BlockReportTop3({
  hours = 24,
  n = 3,
  refreshMs = 30000,
}: {
  hours?: number;
  n?: number;
  refreshMs?: number;
}) {
  const [data, setData] = useState<BlockReportResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const d = await getBlockReportTop(n, hours);
      if (!cancelled) setData(d);
    };
    load();
    const t = setInterval(load, refreshMs);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [hours, n, refreshMs]);

  return (
    <div className="bg-gray-800 border border-gray-700 rounded p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="text-xs font-bold text-gray-300">
          阻断 Top {n}（最近 {hours}h）
        </div>
        <div className="text-[10px] text-gray-500">
          合计：{data?.total ?? 0}
        </div>
      </div>

      {!data || data.total === 0 ? (
        <div className="text-gray-500 text-xs p-2">窗口内未记录到阻断事件。</div>
      ) : (
        <ol className="space-y-2">
          {data.top.map((it, i) => (
            <li key={it.code} className="bg-gray-900/60 rounded p-2">
              <div className="flex items-center justify-between text-xs">
                <div className="flex items-center gap-2">
                  <span className="text-gray-500 font-mono">#{i + 1}</span>
                  <span className="text-white font-mono">{it.code}</span>
                </div>
                <div className="font-mono text-orange-400">
                  {it.count} · {(it.ratio * 100).toFixed(0)}%
                </div>
              </div>
              {it.samples.length > 0 && (
                <div className="mt-1 text-[10px] text-gray-400 space-y-0.5">
                  {it.samples.map((s, j) => (
                    <div key={j} className="truncate" title={s}>
                      ↳ {s}
                    </div>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
