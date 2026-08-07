/**
 * EvolutionHistoryTable — 策略进化历史（冠军策略）表
 *
 * 数据来自 `/api/evolution/history`，展示每次进化产出的冠军策略与回测/实盘对比。
 * 支持：
 *   - 是否仅看冠军（is_champion）
 *   - 按模板（template_id）筛选
 *   - 分页
 */

import { useEffect, useMemo, useState } from 'react';
import { getEvolutionHistory, type EvolutionHistoryResult } from '@/lib/aiLearningApi';

type Record = EvolutionHistoryResult['records'][number];

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '-';
  return `${(v * 100).toFixed(1)}%`;
}

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '-';
  return v.toFixed(digits);
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '-';
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString('zh-CN', { hour12: false });
  } catch {
    return iso;
  }
}

export default function EvolutionHistoryTable() {
  const [data, setData] = useState<EvolutionHistoryResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(10);
  const [templateFilter, setTemplateFilter] = useState<string>('');
  const [championOnly, setChampionOnly] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getEvolutionHistory(templateFilter || undefined, page, pageSize)
      .then((r) => {
        if (!cancelled) setData(r);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [templateFilter, page, pageSize]);

  // 前端二次过滤：仅冠军
  const visibleRecords: Record[] = useMemo(() => {
    if (!data?.records) return [];
    return championOnly ? data.records.filter((r) => r.is_champion) : data.records;
  }, [data, championOnly]);

  // 模板下拉候选（从当前页数据中取所有出现过的 template_id，便于快速筛选）
  const templateOptions = useMemo(() => {
    const set = new Set<string>();
    (data?.records ?? []).forEach((r) => r.template_id && set.add(r.template_id));
    return Array.from(set).sort();
  }, [data]);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-gray-400">模板：</span>
          <select
            value={templateFilter}
            onChange={(e) => {
              setTemplateFilter(e.target.value);
              setPage(1);
            }}
            aria-label="按模板筛选进化历史"
            className="h-7 px-2 text-xs bg-gray-800 border border-gray-600 rounded text-white font-mono"
          >
            <option value="">全部</option>
            {templateOptions.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-1 text-xs text-gray-400 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={championOnly}
              onChange={(e) => setChampionOnly(e.target.checked)}
              className="accent-blue-500"
            />
            仅看冠军
          </label>
        </div>
        <div className="text-[10px] text-gray-500">
          合计 {data?.total ?? 0} 条{loading ? ' · 加载中…' : ''}
        </div>
      </div>

      <div className="overflow-x-auto border border-gray-700 rounded">
        <table className="w-full text-xs text-gray-300">
          <thead className="bg-gray-800 text-gray-400">
            <tr>
              <th className="text-left px-2 py-1.5 font-normal">模板</th>
              <th className="text-left px-2 py-1.5 font-normal">代次</th>
              <th className="text-right px-2 py-1.5 font-normal">夏普</th>
              <th className="text-right px-2 py-1.5 font-normal">胜率</th>
              <th className="text-right px-2 py-1.5 font-normal">最大回撤</th>
              <th className="text-right px-2 py-1.5 font-normal">总收益</th>
              <th className="text-center px-2 py-1.5 font-normal">冠军</th>
              <th className="text-left px-2 py-1.5 font-normal">时间</th>
            </tr>
          </thead>
          <tbody>
            {visibleRecords.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-2 py-6 text-center text-gray-500">
                  {loading ? '加载中…' : '暂无进化历史数据'}
                </td>
              </tr>
            ) : (
              visibleRecords.map((r) => (
                <tr key={r.run_id} className="border-t border-gray-800 hover:bg-gray-850/50">
                  <td className="px-2 py-1.5 font-mono">{r.template_id}</td>
                  <td className="px-2 py-1.5">{r.generation}</td>
                  <td
                    className={`px-2 py-1.5 text-right font-mono ${
                      r.sharpe_ratio >= 1 ? 'text-green-400' : r.sharpe_ratio < 0 ? 'text-red-400' : 'text-gray-300'
                    }`}
                  >
                    {fmtNum(r.sharpe_ratio)}
                  </td>
                  <td
                    className={`px-2 py-1.5 text-right font-mono ${
                      r.win_rate >= 0.6 ? 'text-green-400' : r.win_rate < 0.45 ? 'text-red-400' : 'text-gray-300'
                    }`}
                  >
                    {fmtPct(r.win_rate)}
                  </td>
                  <td className="px-2 py-1.5 text-right font-mono text-orange-300">{fmtPct(r.max_drawdown)}</td>
                  <td
                    className={`px-2 py-1.5 text-right font-mono ${
                      r.total_return > 0 ? 'text-green-400' : r.total_return < 0 ? 'text-red-400' : 'text-gray-300'
                    }`}
                  >
                    {fmtPct(r.total_return)}
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    {r.is_champion ? (
                      <span className="text-yellow-400" title="冠军策略">
                        ★
                      </span>
                    ) : (
                      <span className="text-gray-600">–</span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-gray-400">{fmtTime(r.created_at)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {data && data.total > pageSize && (
        <div className="flex items-center justify-between text-xs text-gray-400">
          <div>
            第 {page} / {totalPages} 页
          </div>
          <div className="flex gap-2">
            <button
              disabled={page <= 1 || loading}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="px-2 py-0.5 bg-gray-800 hover:bg-gray-700 rounded disabled:opacity-40"
            >
              上一页
            </button>
            <button
              disabled={page >= totalPages || loading}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              className="px-2 py-0.5 bg-gray-800 hover:bg-gray-700 rounded disabled:opacity-40"
            >
              下一页
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
