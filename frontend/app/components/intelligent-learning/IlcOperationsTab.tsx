/**
 * 智能学习中心 — 运维闭环 Tab（合并旧 AI 学习中心核心能力）
 */
import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import {
  getCoordinatorStatus,
  getPortfolioKelly,
  type CoordinatorStatus,
  type PortfolioKellyResult,
} from '@/lib/aiLearningApi';
import { SectionCard, EmptyState } from './IlcUi';

const LearningHealthCard = lazy(() => import('../ai-learning/LearningHealthCard'));
const LearningLoopHeartbeat = lazy(() => import('../ai-learning/LearningLoopHeartbeat'));
const SystemCoordinationBanner = lazy(() => import('../ai-learning/SystemCoordinationBanner'));
const MultiSymbolKellyTable = lazy(() => import('../ai-learning/MultiSymbolKellyTable'));
const LearningDashboardPanel = lazy(() => import('../ai-learning/LearningDashboardPanel'));
const FeatureFlagsPanel = lazy(() => import('../ai-learning/FeatureFlagsPanel'));
const BlockReportTop3 = lazy(() => import('../ai-learning/BlockReportTop3'));
const WisdomLoopPanel = lazy(() => import('./WisdomLoopPanel'));
const ParamDomainPanel = lazy(() => import('./ParamDomainPanel'));
const QaaSchedulerPanel = lazy(() => import('./QaaSchedulerPanel'));
const DecisionChainPanel = lazy(() => import('./DecisionChainPanel'));
const CoinFeedbackPanel = lazy(() => import('./CoinFeedbackPanel'));

function Load({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={<EmptyState message="加载中…" />}>
      {children}
    </Suspense>
  );
}

export function IlcOperationsTab() {
  const [coordinatorStatus, setCoordinatorStatus] = useState<CoordinatorStatus | null>(null);
  const [portfolioKelly, setPortfolioKelly] = useState<PortfolioKellyResult | null>(null);

  const refresh = useCallback(() => {
    getCoordinatorStatus().then(setCoordinatorStatus).catch(() => setCoordinatorStatus(null));
    getPortfolioKelly([]).then(setPortfolioKelly).catch(() => setPortfolioKelly(null));
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30000);
    return () => clearInterval(t);
  }, [refresh]);

  return (
    <div className="space-y-4">
      {/* 阶段2(S2-11)：学习三通道看板（wisdom 闭环 / 参数域扩展 / QAA 调度） */}
      <SectionCard
        title="学习三通道（阶段 2）"
        description="wisdom 闭环 → 参数域扩展 → QAA 调度统一：实盘证据反哺进化的三条通道"
      >
        <Load>
          <div className="space-y-4">
            <WisdomLoopPanel />
            <ParamDomainPanel />
            <QaaSchedulerPanel />
          </div>
        </Load>
      </SectionCard>

      <SectionCard title="决策链路 & 选币反馈（阶段 2）">
        <Load>
          <div className="space-y-4">
            <DecisionChainPanel />
            <CoinFeedbackPanel />
          </div>
        </Load>
      </SectionCard>

      <SectionCard title="系统协调 & 学习心跳">
        <Load>
          <div className="space-y-3">
            <SystemCoordinationBanner status={coordinatorStatus} />
            <LearningLoopHeartbeat />
          </div>
        </Load>
      </SectionCard>

      <SectionCard title="P0–P3 组件健康">
        <Load>
          <LearningHealthCard />
        </Load>
      </SectionCard>

      <SectionCard title="Kelly 组合仓位">
        <Load>
          <MultiSymbolKellyTable data={portfolioKelly} />
        </Load>
      </SectionCard>

      <SectionCard title="阻断 Top3（code_reason）">
        <Load>
          <BlockReportTop3 />
        </Load>
      </SectionCard>

      <SectionCard title="学习仪表盘">
        <Load>
          <LearningDashboardPanel />
        </Load>
      </SectionCard>

      <SectionCard title="特性开关（可写）">
        <Load>
          <FeatureFlagsPanel />
        </Load>
      </SectionCard>
    </div>
  );
}

export default IlcOperationsTab;
