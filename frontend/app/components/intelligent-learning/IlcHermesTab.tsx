/**
 * 智能学习中心 — Hermes 自进化 Tab（S2-11 生命周期增强）
 *
 * 顶部：Hermes 生命周期总览（阶段判定 / 健康巡检 / 智慧闭环链路 / 四层调度心跳）
 * 下方：完整 Hermes 进化面板（成熟度仪表 / L1-L4 详情）
 */
import { lazy, Suspense } from 'react';
import { EmptyState } from './IlcUi';
import { HermesLifecyclePanel } from './HermesLifecyclePanel';

const HermesEvolutionPanel = lazy(() => import('../opencode/HermesEvolutionPanel'));

export function IlcHermesTab() {
  return (
    <div className="space-y-4">
      <HermesLifecyclePanel />
      <Suspense fallback={<EmptyState message="加载 Hermes 进化面板…" />}>
        <HermesEvolutionPanel />
      </Suspense>
    </div>
  );
}

export default IlcHermesTab;
