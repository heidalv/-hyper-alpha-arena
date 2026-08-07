import React, { useState, useEffect, useCallback, Suspense } from 'react';
import {
  getCoordinatorStatus,
  getPortfolioKelly,
  getEvolutionStatus,
  getCorrelationMatrix,
  getRegimeAnalysis,
  triggerCoordinatedOptimization,
  triggerEvolution,
  type CoordinatorStatus,
  type PortfolioKellyResult,
  type EvolutionStatus,
  type CorrelationMatrixResult,
  type RegimeAnalysisResult,
} from '@/lib/aiLearningApi';

// v3 整改：子组件已拆成独立文件，便于单测与跨页面复用
import SystemCoordinationBanner from './SystemCoordinationBanner';
import LearningLoopHeartbeat from './LearningLoopHeartbeat';
import MultiSymbolKellyTable from './MultiSymbolKellyTable';
import CorrelationMatrixView from './CorrelationMatrixView';
// v3 整改: 新增 tier 分布 / 阻断 TopN / feature flag 面板
import TierDistributionCard from './TierDistributionCard';
import BlockReportTop3 from './BlockReportTop3';
import FeatureFlagsPanel from './FeatureFlagsPanel';
import EvolutionHistoryTable from './EvolutionHistoryTable';
import LearningHealthCard from './LearningHealthCard';
import LearningDashboardPanel from './LearningDashboardPanel';

// 注：DRL 已于 2026-06-11 下线（无训练模型、影子数据无消费端），相关面板已移除
// Prompt 训练控制台（从 AI 决策中心迁移而来，统一整合到 AI 学习中心）
const PromptTrainingConsole = React.lazy(() => import('@/components/atas-v2/PromptTrainingConsole'));
// 注：LearningDashboard 需要真实 strategy_id，已从"进化系统"总览中移除，改由策略管理页面按需挂载

// ══════════════════════════════════════════════════
//  AILearningCenter (Main Page)
// ══════════════════════════════════════════════════

// 市场状态枚举（MarketRegime）→ 中文
// 与 backend/services/market_regime.py:MarketRegime 对齐
const REGIME_LABELS: Record<string, string> = {
  trending_up: '趋势上行',
  trending_down: '趋势下行',
  ranging: '震荡整理',
  high_volatility: '高波动',
  low_volatility: '低波动',
  crash: '崩盘',
  unknown: '未识别',
};

function regimeLabel(regime: string | null | undefined): string {
  if (!regime) return '暂无';
  return REGIME_LABELS[regime] ?? regime;
}

export default function AILearningCenter() {
  const [activeTab, setActiveTab] = useState<'overview' | 'drl-kelly' | 'evolution' | 'feedback' | 'prompt-training'>('overview');
  const [coordinatorStatus, setCoordinatorStatus] = useState<CoordinatorStatus | null>(null);
  const [portfolioKelly, setPortfolioKelly] = useState<PortfolioKellyResult | null>(null);
  const [evolutionStatus, setEvolutionStatus] = useState<EvolutionStatus | null>(null);
  const [correlationMatrix, setCorrelationMatrix] = useState<CorrelationMatrixResult | null>(null);
  const [regimeAnalysis, setRegimeAnalysis] = useState<RegimeAnalysisResult | null>(null);

  const refreshData = useCallback(() => {
    getCoordinatorStatus().then(setCoordinatorStatus);
    // symbols 参数后端会忽略，使用 _collect_active_symbols() 自动取当前活跃池
    getPortfolioKelly([]).then(setPortfolioKelly);
    getEvolutionStatus().then(setEvolutionStatus);
    getCorrelationMatrix().then(setCorrelationMatrix);
    getRegimeAnalysis().then(setRegimeAnalysis);
  }, []);

  useEffect(() => {
    refreshData();
    const interval = setInterval(refreshData, 30000);
    return () => clearInterval(interval);
  }, [refreshData]);

  const tabs = [
    { key: 'overview' as const, label: '总览' },
    { key: 'drl-kelly' as const, label: 'Kelly 仓位' },
    { key: 'evolution' as const, label: '进化系统' },
    { key: 'prompt-training' as const, label: 'Prompt 训练' },
    { key: 'feedback' as const, label: '反馈闭环' },
  ];

  return (
    <div className="flex flex-col h-full bg-gray-50 text-gray-900">
      <div className="p-4 overflow-y-auto flex-1">
        <SystemCoordinationBanner status={coordinatorStatus} />
        <LearningLoopHeartbeat />

        {/* Tab Navigation */}
        <div className="flex gap-1 mb-4">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`px-3 py-1.5 text-xs rounded-t border-b-2 transition-colors ${
                activeTab === tab.key
                  ? 'bg-white border-blue-500 text-blue-600'
                  : 'bg-gray-100 border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        <div className="bg-white border border-gray-200 rounded-b-lg rounded-tr-lg p-4">
          {activeTab === 'overview' && (
            <div className="space-y-6">
              <LearningHealthCard />
              
              {/* P3 学习仪表盘 — 总览 / 因子 / 策略 / 进化 / 实验 */}
              <LearningDashboardPanel />

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <TierDistributionCard />
                <BlockReportTop3 />
              </div>
              <div>
                <h3 className="text-sm font-bold text-gray-700 mb-2">组合 Kelly</h3>
                <MultiSymbolKellyTable data={portfolioKelly} />
              </div>
              {regimeAnalysis && (() => {
                const regimeDist = regimeAnalysis.regime_distribution ?? {};
                const regimeConfidence = Number(regimeAnalysis.regime_confidence ?? 0);
                const recentCount = Number(regimeAnalysis.recent_count ?? 0);
                return (
                  <div>
                    <h3 className="text-sm font-bold text-gray-700 mb-2">市场状态</h3>
                    <div className="text-xs text-gray-400 space-y-1">
                      <div>
                        当前状态：<span className="text-white">{regimeLabel(regimeAnalysis.current_regime)}</span>
                        {regimeConfidence > 0 && (
                          <span className="ml-2">置信度：{(regimeConfidence * 100).toFixed(0)}%</span>
                        )}
                      </div>
                      {Object.keys(regimeDist).length > 0 && (
                        <div className="flex flex-wrap gap-2 pt-1">
                          {Object.entries(regimeDist).map(([regime, count]) => (
                            <span key={regime} className="bg-gray-100 px-2 py-0.5 rounded text-[11px]">
                              {regimeLabel(regime)}: {count}
                            </span>
                          ))}
                        </div>
                      )}
                      <div className="text-[10px] text-gray-500">
                        {regimeAnalysis.source ? `数据源：${regimeAnalysis.source}` : null}
                        {regimeAnalysis.anchor_symbol ? ` · 锚定：${regimeAnalysis.anchor_symbol}` : null}
                        {recentCount > 0 ? ` · 样本：${recentCount}` : null}
                      </div>
                    </div>
                  </div>
                );
              })()}
            </div>
          )}

          {activeTab === 'drl-kelly' && (
            <div className="space-y-6">
              <div className="px-3 py-2 text-[11px] bg-amber-500/5 border border-amber-500/20 rounded text-amber-300/90">
                DRL（强化学习）已于 2026-06-11 下线：无已训练模型、影子预测从未回填结果，
                算力已重分配给复盘/进化闭环。历史数据保留在 drl_performance 表。
              </div>
              <div>
                <h3 className="text-sm font-bold text-gray-700 mb-2">多币种 Kelly 仓位</h3>
                <MultiSymbolKellyTable data={portfolioKelly} />
              </div>
              <div>
                <h3 className="text-sm font-bold text-gray-700 mb-2">币种相关性矩阵</h3>
                <CorrelationMatrixView data={correlationMatrix} />
              </div>
            </div>
          )}

          {activeTab === 'evolution' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-bold text-gray-700">策略进化系统</h3>
                <div className="flex gap-2">
                  <button
                    onClick={() => triggerEvolution('manual')}
                    className="px-3 py-1 text-xs bg-blue-600 hover:bg-blue-500 rounded"
                  >
                    手动进化
                  </button>
                  <button
                    onClick={() => triggerEvolution('emergency')}
                    className="px-3 py-1 text-xs bg-red-600 hover:bg-red-500 rounded"
                  >
                    紧急进化
                  </button>
                </div>
              </div>
              <div className="px-3 py-2 text-[11px] bg-blue-500/5 border border-blue-500/20 rounded text-blue-300/90">
                本面板展示 <span className="font-semibold">运行时在线进化</span> 的冠军策略历史。
                <span className="opacity-80 ml-1">
                  想主动发起回测/深度进化？请到 <span className="font-semibold">策略管理 → 回测进化</span>。
                </span>
              </div>
              {/* 进化系统运行状态 */}
              {evolutionStatus && (() => {
                const progress = evolutionStatus.evolver_progress ?? {};
                const progressEntries =
                  progress && typeof progress === 'object' ? Object.entries(progress) : [];
                return (
                  <div className="bg-gray-100/60 rounded p-3 text-xs text-gray-600 space-y-1 border border-gray-200">
                    <div className="flex gap-4 flex-wrap">
                      <span>
                        进化器：
                        <span className={evolutionStatus.evolver_running ? 'text-green-400 ml-1' : 'text-gray-500 ml-1'}>
                          {evolutionStatus.evolver_running ? '运行中' : '空闲'}
                        </span>
                      </span>
                      <span>
                        调度器：
                        <span className="text-white ml-1">{evolutionStatus.scheduler_status ?? 'unknown'}</span>
                      </span>
                    </div>
                    {progressEntries.length > 0 && (
                      <div className="mt-2 bg-gray-50 p-2 rounded border border-gray-200">
                        {progressEntries.map(([k, v]) => (
                          <div key={k} className="font-mono text-[11px]">
                            {k}: {String(v)}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })()}

              {/* 进化历史（冠军策略）表 */}
              <div>
                <h3 className="text-sm font-bold text-gray-700 mb-2">冠军策略进化历史</h3>
                <EvolutionHistoryTable />
              </div>

              {/* 市场状态分布（进化决策依据） */}
              {regimeAnalysis && (() => {
                const dist = regimeAnalysis.regime_distribution ?? {};
                const entries = Object.entries(dist);
                if (entries.length === 0) return null;
                return (
                  <div>
                    <h4 className="text-xs font-bold text-gray-400 mb-1">市场状态分布（进化决策依据）</h4>
                    <div className="flex gap-2 flex-wrap">
                      {entries.map(([regime, count]) => (
                        <span key={regime} className="text-xs bg-gray-100 px-2 py-1 rounded">
                          {regimeLabel(regime)}: {count}
                        </span>
                      ))}
                    </div>
                  </div>
                );
              })()}

              {/* 策略级复盘（占位） */}
              <div>
                <h3 className="text-sm font-bold text-gray-700 mb-2">策略级学习复盘</h3>
                <div className="bg-gray-50 border border-gray-200 rounded p-3 text-xs text-gray-600 space-y-1">
                  <div>
                    当前视图为全局进化历史。要查看单个策略的逐笔复盘、记忆摘要与教训提取，请到
                    <span className="text-blue-300 mx-1">策略管理</span>
                    页面选择具体策略后进入其"学习仪表盘"。
                  </div>
                  <div className="text-[10px] text-gray-500">
                    （LearningDashboard 需要具体 strategy_id，默认值 <code className="font-mono">default</code> 仅为占位）
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'prompt-training' && (
            <div className="space-y-4">
              <div className="px-3 py-2 text-[11px] bg-blue-500/5 border border-blue-500/20 rounded text-blue-300/90">
                本面板用于 <span className="font-semibold">AI 提示词（Prompt）训练与评估</span>。
                <span className="opacity-80 ml-1">
                  原位于"AI 决策中心 → AI 学习"，已统一整合到此处。
                </span>
              </div>
              <Suspense fallback={<div className="text-gray-500 text-xs p-2">正在加载 Prompt 训练控制台…</div>}>
                <div className="max-w-5xl">
                  <PromptTrainingConsole />
                </div>
              </Suspense>
            </div>
          )}

          {activeTab === 'feedback' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-bold text-gray-700">反馈闭环</h3>
                <button
                  onClick={() => triggerCoordinatedOptimization('manual')}
                  className="px-3 py-1 text-xs bg-purple-600 hover:bg-purple-500 rounded"
                >
                  触发协调优化
                </button>
              </div>
              <div className="bg-gray-100 p-4 rounded text-xs text-gray-600">
                <div className="mb-2 text-gray-700 font-bold">数据闭环流转（2026-06-11 升级后）：</div>
                <div className="font-mono space-y-1">
                  <div>平仓 → 复盘(Retrospective) → 反馈服务 → v5_runtime_gates → 决策核心</div>
                  <div>离线进化(NSGA-II) → 冠军参数 → v5_runtime_gates → 决策核心</div>
                  <div>交易结果 → Kelly 聚合(30min) → 仓位上限夹紧</div>
                </div>
                <div className="mt-3 text-gray-700 font-bold">决策优先级：</div>
                <div className="font-mono">风控闸门 &gt; Kelly &gt; 进化参数</div>
              </div>
              <FeatureFlagsPanel />
              {coordinatorStatus && (
                <div>
                  <h4 className="text-xs font-bold text-gray-400 mb-2">系统状态</h4>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div className="bg-gray-100 p-2 rounded">
                      <span className="text-gray-400">Kelly：</span>{' '}
                      <span className={coordinatorStatus.kelly_available ? 'text-green-400' : 'text-gray-500'}>
                        {coordinatorStatus.kelly_available ? '就绪' : '未启用'}
                      </span>
                    </div>
                    <div className="bg-gray-100 p-2 rounded">
                      <span className="text-gray-400">组合风控：</span>{' '}
                      <span className={coordinatorStatus.risk_aggregator_available ? 'text-green-400' : 'text-gray-500'}>
                        {coordinatorStatus.risk_aggregator_available ? '已启用' : '未启用'}
                      </span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
