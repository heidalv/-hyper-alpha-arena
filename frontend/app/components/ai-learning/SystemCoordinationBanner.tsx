import type { CoordinatorStatus } from '@/lib/aiLearningApi';

/**
 * SystemCoordinationBanner — 系统协调状态横幅
 * v3 整改：由 AILearningCenter 内联拆出，便于独立复用与单测。
 */
// 后端 feature_flags 的 key → 中文标签映射
// 若后端新增 flag 会自动降级到 key.replace(/_/g, ' ')，避免 UI 崩
const FLAG_LABELS: Record<string, string> = {
  // DRL 已于 2026-06-11 下线，相关标签已移除（自动降级到 key.replace(/_/g, ' ')）
  kelly_position: 'Kelly 仓位',
  portfolio_risk: '组合风险',
  coordinator: '系统协调器',
};

export default function SystemCoordinationBanner({ status }: { status: CoordinatorStatus | null }) {
  if (!status) return null;

  const flags = status.feature_flags;
  const activeCount = Object.values(flags).filter(Boolean).length;
  const totalFlags = Object.keys(flags).length;

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-4 mb-4">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-bold text-gray-300">系统协调状态</h3>
        <span className="text-xs text-gray-500">
          已启用 {activeCount}/{totalFlags} 项
        </span>
      </div>
      <div className="grid grid-cols-3 gap-2 text-xs">
        {Object.entries(flags).map(([key, enabled]) => (
          <div key={key} className="flex items-center gap-1">
            <span className={`w-2 h-2 rounded-full ${enabled ? 'bg-green-500' : 'bg-gray-600'}`} />
            <span className={enabled ? 'text-green-400' : 'text-gray-500'}>
              {FLAG_LABELS[key] ?? key.replace(/_/g, ' ')}
            </span>
          </div>
        ))}
      </div>
      <div className="mt-2 flex items-center gap-4 text-xs text-gray-400">
        <span>Kelly：{status.kelly_available ? '就绪' : '未启用'}</span>
        <span>组合风控：{status.risk_aggregator_available ? '已启用' : '未启用'}</span>
        <span className="text-gray-600">DRL 已下线（2026-06-11）</span>
      </div>
    </div>
  );
}
