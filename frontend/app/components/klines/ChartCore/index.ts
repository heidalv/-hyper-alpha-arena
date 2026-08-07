/**
 * ChartCore — 图表核心组件
 *
 * 从 TradingViewChart 单体中提取的可复用模块：
 * - types.ts      共享类型定义
 * - chartConfig.ts 颜色、指标、配置
 * - useKlinesData.ts  数据获取、轮询、缓存管理
 * - useChartOverlays.ts 形态标记、S/R 线管理
 */

export { CHART_COLORS, INDICATOR_DEFINITIONS, MARKET_FLOW_CONFIGS, CHART_STYLE, POLLING_INTERVAL } from './chartConfig'
export type { KlineBar, IndicatorSeries, MarketFlowConfig, PatternInfo, SRLevel, VolumeAnomalyInfo, ChartType } from './types'
export { useKlinesData } from './useKlinesData'
export { useChartPatterns, useChartSRLevels, useVolumeAnomalies } from './useChartOverlays'
