/**
 * ATAS 交易控制台组件导出
 * v3.0 - 统一控制台版本
 */

// 新版统一 ATAS 控制台 (推荐使用)
export { UnifiedATASConsole } from './UnifiedATASConsole'
export { UnifiedATASConsole as TradingConsole } from './UnifiedATASConsole'

// 旧版控制台 (向后兼容)
export { TradingConsole as LegacyTradingConsole } from './TradingConsole'

// 辅助面板组件
export { MarketOverviewPanel } from './MarketOverviewPanel'
export { TrendPanel } from './TrendPanel'
export { SignalPanel } from './SignalPanel'
export { AIDecisionPanel } from './AIDecisionPanel'
export { PositionPanel } from './PositionPanel'
export { DecisionLog } from './DecisionLog'
export { RuntimeGovernorPanel } from './RuntimeGovernorPanel'

// 默认导出 - 新版统一控制台
import { UnifiedATASConsole } from './UnifiedATASConsole'
export default UnifiedATASConsole
