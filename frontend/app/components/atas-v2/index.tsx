/**
 * ATAS V2 策略中心主入口
 *
 * 导出所有公开组件供外部使用。
 * ATAS V2 的实际页面入口是 ATASV2Page，
 * 使用 Tab 布局集成 Dashboard、DesignerV2、AI策略、交易工具等模块。
 */

export { default as ATASV2Page } from './ATASV2Page';
export { default as ATASV2Dashboard } from './ATASV2Dashboard';
export { default as TradingTools } from './TradingTools';
export { default as StrategyPerformancePivot } from './StrategyPerformancePivot';
export { default as RiskMonitorPanel } from './RiskMonitorPanel';
export { default as CausalAnalysisView } from './CausalAnalysisView';
export { default as MetaStrategyView } from './MetaStrategyView';

export { default } from './ATASV2Page';
