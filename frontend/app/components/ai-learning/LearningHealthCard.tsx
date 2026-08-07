/**
 * 学习进化系统健康卡片 — P3 升级版（2026-06-22 接入 P3 仪表盘）
 *
 * 展示 P0/P1/P2/P3 各组件的健康状态（ok/error），
 * 数据源：GET /api/learning/dashboard/health（60s 轮询）
 */
import { useState, useEffect, useCallback } from 'react';
import { getDashboardHealth, type DashboardHealth } from '@/lib/aiLearningApi';

interface HealthItem {
  name: string;
  label: string;
  phase: string;
  status: 'ok' | 'error';
}

const PHASE_LABELS: Record<string, string> = {
  P0: '学习基建',
  P1: '深度推理',
  P2: '自主进化',
  P3: '全局调度',
};

const KEY_MAP: Array<{ key: keyof DashboardHealth; label: string; phase: string }> = [
  { key: 'causal_discovery', label: '因果发现', phase: 'P0' },
  { key: 'concept_drift', label: '概念漂移', phase: 'P0' },
  { key: 'memory_decay', label: '记忆衰减', phase: 'P0' },
  { key: 'counterfactual_sandbox', label: '反事实沙盒', phase: 'P1' },
  { key: 'trading_narrative', label: '交易叙事', phase: 'P1' },
  { key: 'factor_discovery', label: '因子发现', phase: 'P2' },
  { key: 'factor_strategy_fusion', label: '因子融合', phase: 'P2' },
  { key: 'walk_forward_validator', label: '前向验证', phase: 'P2' },
  { key: 'cross_market_transfer', label: '跨市场迁移', phase: 'P3' },
  { key: 'learning_ab_framework', label: 'A/B框架(已关)', phase: 'P3' },
];

export default function LearningHealthCard() {
  const [health, setHealth] = useState<DashboardHealth | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    getDashboardHealth()
      .then((data) => {
        if (data) {
          setHealth(data);
          setError(null);
        } else {
          setError('响应为空');
        }
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 60000);
    return () => clearInterval(interval);
  }, [refresh]);

  // 将扁平 health 转成分组 items
  const items: HealthItem[] = KEY_MAP.map((m) => ({
    name: m.key,
    label: m.label,
    phase: m.phase,
    status: health && health[m.key] === 'ok' ? 'ok' : 'error',
  }));

  const okCount = items.filter((i) => i.status === 'ok').length;
  const totalCount = items.length;
  const overallOk = okCount === totalCount;

  return (
    <div className="bg-gray-800/60 border border-gray-700 rounded p-3">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-bold text-gray-300">P0-P3 组件健康</h3>
        {health && (
          <span className={`text-xs flex items-center gap-1.5 ${overallOk ? 'text-green-400' : 'text-red-400'}`}>
            <span className={`w-2 h-2 rounded-full ${overallOk ? 'bg-green-500' : 'bg-red-500'}`} />
            {health.overall_health ?? `${okCount}/${totalCount}`}
          </span>
        )}
      </div>
      {error && (
        <div className="text-xs text-red-400 mb-2">健康检查异常：{error}</div>
      )}
      {!health && !error && (
        <div className="text-xs text-gray-500">加载中…</div>
      )}
      {health && (
        <div className="space-y-1.5">
          {/* 按 phase 分组 */}
          {(['P0', 'P1', 'P2', 'P3'] as const).map((phase) => {
            const phaseItems = items.filter((i) => i.phase === phase);
            if (phaseItems.length === 0) return null;
            return (
              <div key={phase}>
                <div className="text-[10px] text-cyan-400 font-semibold uppercase tracking-wider mb-0.5">
                  {PHASE_LABELS[phase] ?? phase}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {phaseItems.map((item) => (
                    <span
                      key={item.name}
                      title={health[item.name as keyof DashboardHealth] as string}
                      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] ${
                        item.status === 'ok'
                          ? 'bg-green-900/40 text-green-400 border border-green-800'
                          : 'bg-red-900/40 text-red-400 border border-red-800'
                      }`}
                    >
                      <span
                        className={`w-1.5 h-1.5 rounded-full ${
                          item.status === 'ok' ? 'bg-green-500' : 'bg-red-500'
                        }`}
                      />
                      {item.label}
                    </span>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
