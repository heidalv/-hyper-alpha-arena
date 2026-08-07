import { useEffect, useState } from 'react';
import {
  getP3FeatureFlags,
  setP3FeatureFlag,
} from '@/lib/aiLearningApi';

/**
 * FeatureFlagsPanel — P0-P3 AI 进化全栈特性开关（运行时）
 *
 * 数据源：GET/POST /api/learning/dashboard/feature-flags
 * 修改仅影响当前后端进程，重启后回落到 .env 默认值。
 */

interface FlagGroup {
  phase: string;
  flags: string[];
}

const FLAG_GROUPS: FlagGroup[] = [
  {
    phase: 'P0 学习基建',
    flags: [
      'AI_CAUSAL_DISCOVERY_ENABLED',
      'AI_FACTOR_STRATEGY_JOINT_ENABLED',
      'AI_CONCEPT_DRIFT_DETECTION_ENABLED',
      'AI_MEMORY_DECAY_ENABLED',
    ],
  },
  {
    phase: 'P1 深度推理',
    flags: [
      'AI_MULTI_ROUND_ANALYSIS_ENABLED',
      'AI_COUNTERFACTUAL_SANDBOX_ENABLED',
      'AI_TRADING_NARRATIVE_ENABLED',
      'AI_STRATEGY_DEEP_DIVE_ENHANCED_ENABLED',
    ],
  },
  {
    phase: 'P2 自主进化',
    flags: [
      'AI_FACTOR_DISCOVERY_ENABLED',
      'AI_STRUCTURAL_MUTATION_ENABLED',
      'AI_FACTOR_STRATEGY_FUSION_ENABLED',
      'AI_VPVR_V3_ENABLED',
      'AI_FREQUENCY_CONSTRAINT_CHAIN_ENABLED',
      'AI_WALK_FORWARD_VALIDATION_ENABLED',
    ],
  },
  {
    phase: 'P3 全局调度',
    flags: [
      'AI_CROSS_MARKET_TRANSFER_ENABLED',
    ],
  },
];

/** 给 AI_XXX_ENABLED 生成可读中文名 */
function flagLabel(key: string): string {
  const map: Record<string, string> = {
    AI_CAUSAL_DISCOVERY_ENABLED: '因果发现',
    AI_FACTOR_STRATEGY_JOINT_ENABLED: '因子策略联合',
    AI_CONCEPT_DRIFT_DETECTION_ENABLED: '概念漂移检测',
    AI_MEMORY_DECAY_ENABLED: '记忆衰减',
    AI_MULTI_ROUND_ANALYSIS_ENABLED: '多轮分析',
    AI_COUNTERFACTUAL_SANDBOX_ENABLED: '反事实沙盒',
    AI_TRADING_NARRATIVE_ENABLED: '交易叙事',
    AI_STRATEGY_DEEP_DIVE_ENHANCED_ENABLED: '策略深潜增强',
    AI_FACTOR_DISCOVERY_ENABLED: '因子自动发现',
    AI_STRUCTURAL_MUTATION_ENABLED: '结构突变',
    AI_FACTOR_STRATEGY_FUSION_ENABLED: '因子策略融合',
    AI_VPVR_V3_ENABLED: 'VPVR v3 成交量分布',
    AI_FREQUENCY_CONSTRAINT_CHAIN_ENABLED: '多周期约束链',
    AI_WALK_FORWARD_VALIDATION_ENABLED: '前向验证',
    AI_CROSS_MARKET_TRANSFER_ENABLED: '跨市场迁移',
    AI_AB_FRAMEWORK_ENABLED: 'A/B 实验框架（未接线，仅记录）',
  };
  return map[key] ?? key;
}

export default function FeatureFlagsPanel() {
  const [flags, setFlags] = useState<Record<string, boolean> | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  const load = async () => {
    const f = await getP3FeatureFlags();
    setFlags(f);
  };

  useEffect(() => {
    load();
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, []);

  const toggle = async (key: string) => {
    if (!flags) return;
    setPending(key);
    const ok = await setP3FeatureFlag(key, !flags[key]);
    if (ok) {
      setFlags({ ...flags, [key]: !flags[key] });
    } else {
      await load();
    }
    setPending(null);
  };

  if (!flags) {
    return <div className="text-gray-500 text-xs p-2">加载功能开关…</div>;
  }

  const onCount = Object.values(flags).filter(Boolean).length;
  const totalCount = Object.keys(flags).length;

  return (
    <div className="bg-gray-800 border border-gray-700 rounded p-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-bold text-gray-300">
          P0-P3 AI 进化开关
        </span>
        <span className="text-[10px] text-gray-500">
          {onCount}/{totalCount} ON
        </span>
      </div>

      {FLAG_GROUPS.map((group) => (
        <div key={group.phase} className="mb-2 last:mb-0">
          <div className="text-[10px] text-cyan-400 font-semibold uppercase tracking-wider mb-1">
            {group.phase}
          </div>
          <div className="space-y-1">
            {group.flags.map((key) => {
              const on = flags[key];
              const busy = pending === key;
              return (
                <div key={key} className="flex items-center justify-between text-[11px]">
                  <span className="text-gray-300 truncate mr-2">{flagLabel(key)}</span>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => toggle(key)}
                    aria-label={`toggle ${flagLabel(key)}`}
                    className={`px-2 py-0.5 rounded text-[10px] font-mono transition-colors shrink-0 ${
                      on
                        ? 'bg-green-600 hover:bg-green-500 text-white'
                        : 'bg-gray-700 hover:bg-gray-600 text-gray-400'
                    } ${busy ? 'opacity-50 cursor-wait' : ''}`}
                  >
                    {busy ? '…' : on ? 'ON' : 'OFF'}
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      ))}

      <div className="mt-2 pt-2 border-t border-gray-700 text-[10px] text-gray-500">
        说明：运行时开关，重启后回落到 .env 配置值。30s 自动刷新。
      </div>
    </div>
  );
}
