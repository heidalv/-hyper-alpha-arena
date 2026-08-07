/**
 * 智能学习中心 — RuntimeGovernor 审批 Tab
 */
import { RuntimeGovernorPanel } from '../trading-console/RuntimeGovernorPanel';
import { InfoBanner } from './IlcUi';

export function IlcGovernorTab() {
  return (
    <div className="space-y-4">
      <InfoBanner title="运行时门槛审批">
        Hermes L3 架构提案、L4 策略晋升、OpenCode runtime patch 统一经 Governor 审批后生效。
        Paper 模式可在此批准/拒绝待处理 patch。
      </InfoBanner>
      <RuntimeGovernorPanel />
    </div>
  );
}

export default IlcGovernorTab;
