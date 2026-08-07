import type { PortfolioKellyResult } from '@/lib/aiLearningApi';

/**
 * MultiSymbolKellyTable — 多币种 Kelly 仓位表
 * v3 整改：由 AILearningCenter 内联拆出，对应后端 /api/rl/kelly/portfolio。
 */
export default function MultiSymbolKellyTable({ data }: { data: PortfolioKellyResult | null }) {
  if (!data || !data.allocations.length) {
    return <div className="text-gray-500 text-sm p-4">暂无组合 Kelly 数据</div>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-gray-400 border-b border-gray-700">
            <th className="text-left p-2">币种</th>
            <th className="text-right p-2">Kelly%</th>
            <th className="text-right p-2">调整后%</th>
            <th className="text-right p-2">组合占比%</th>
            <th className="text-right p-2">风险贡献</th>
            <th className="text-right p-2">相关性</th>
          </tr>
        </thead>
        <tbody>
          {data.allocations.map((a) => (
            <tr key={a.symbol} className="border-b border-gray-800">
              <td className="p-2 text-white font-mono">{a.symbol}</td>
              <td className="p-2 text-right text-yellow-400">{(a.kelly_fraction * 100).toFixed(1)}%</td>
              <td className="p-2 text-right text-green-400">{(a.adjusted_fraction * 100).toFixed(1)}%</td>
              <td className="p-2 text-right text-gray-300">{(a.portfolio_fraction * 100).toFixed(1)}%</td>
              <td className="p-2 text-right text-orange-400">{a.risk_contribution.toFixed(3)}</td>
              <td className="p-2 text-right text-gray-400">{a.correlation_with_others.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-2 flex gap-4 text-xs text-gray-400 p-2">
        <span>总风险：<span className="text-orange-400">{data.total_risk.toFixed(3)}</span></span>
        <span>相关性风险：<span className="text-yellow-400">{data.correlation_risk.toFixed(3)}</span></span>
      </div>
      {data.forced_adjustments.length > 0 && (
        <div className="mt-1 text-xs text-red-400 p-2">
          {data.forced_adjustments.map((adj, i) => <div key={i}>{adj}</div>)}
        </div>
      )}
    </div>
  );
}
