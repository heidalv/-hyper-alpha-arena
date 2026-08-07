import type { CorrelationMatrixResult } from '@/lib/aiLearningApi';

/**
 * CorrelationMatrixView — 币种相关性矩阵热力图
 * v3 整改：由 AILearningCenter 内联拆出，对应后端 /api/evolution/correlation-matrix。
 */
export default function CorrelationMatrixView({ data }: { data: CorrelationMatrixResult | null }) {
  if (!data || !data.symbols.length) {
    return <div className="text-gray-500 text-sm p-4">暂无相关性数据</div>;
  }

  const getColor = (val: number) => {
    if (val >= 0.8) return 'bg-red-600';
    if (val >= 0.5) return 'bg-orange-500';
    if (val >= 0.2) return 'bg-yellow-500';
    return 'bg-green-600';
  };

  return (
    <div className="overflow-x-auto">
      <table className="text-xs">
        <thead>
          <tr>
            <th className="p-1" />
            {data.symbols.map((s) => (
              <th key={s} className="p-1 text-gray-400 font-mono">{s.slice(0, 4)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.matrix.map((row, i) => (
            <tr key={data.symbols[i]}>
              <td className="p-1 text-gray-400 font-mono">{data.symbols[i].slice(0, 4)}</td>
              {row.map((val, j) => (
                <td
                  key={j}
                  className={`p-1 text-center ${getColor(Math.abs(val))} text-white`}
                  title={`${data.symbols[i]} vs ${data.symbols[j]}: ${val.toFixed(3)}`}
                >
                  {val.toFixed(2)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
