/**
 * ChartCore — 图表配置（颜色、指标定义、默认值）
 */

import type { IndicatorSeries, MarketFlowConfig } from './types'

export const CHART_COLORS = {
  candleUp: '#22c55e',
  candleDown: '#ef4444',
  volumeUp: 'rgba(34, 197, 94, 0.5)',
  volumeDown: 'rgba(239, 68, 68, 0.5)',
  text: '#9ca3af',
  grid: 'rgba(156, 163, 175, 0.1)',
  bullish: '#22c55e',
  bearish: '#ef4444',
  neutral: '#eab308',
}

export const INDICATOR_DEFINITIONS: IndicatorSeries[] = [
  // Trend
  { key: 'MA5', label: 'MA5', category: 'trend', isSubplot: false, color: '#ff6b6b', description: '5期简单移动平均线' },
  { key: 'MA10', label: 'MA10', category: 'trend', isSubplot: false, color: '#fbbf24', description: '10期简单移动平均线' },
  { key: 'MA20', label: 'MA20', category: 'trend', isSubplot: false, color: '#60a5fa', description: '20期简单移动平均线' },
  { key: 'EMA20', label: 'EMA20', category: 'trend', isSubplot: false, color: '#34d399', description: '20期指数移动平均线' },
  { key: 'EMA50', label: 'EMA50', category: 'trend', isSubplot: false, color: '#a78bfa', description: '50期指数移动平均线' },
  { key: 'EMA100', label: 'EMA100', category: 'trend', isSubplot: false, color: '#f472b6', description: '100期指数移动平均线' },

  // Volume
  { key: 'VWAP', label: 'VWAP', category: 'volume', isSubplot: false, color: '#f59e0b', description: '成交量加权平均价' },
  { key: 'OBV', label: 'OBV', category: 'volume', isSubplot: true, color: '#10b981', description: '能量潮指标' },

  // Momentum
  { key: 'RSI14', label: 'RSI14', category: 'momentum', isSubplot: true, color: '#e11d48', description: '14期相对强弱指数' },
  { key: 'RSI7', label: 'RSI7', category: 'momentum', isSubplot: true, color: '#f97316', description: '7期相对强弱指数' },
  { key: 'STOCH', label: 'STOCH', category: 'momentum', isSubplot: true, color: '#3b82f6', description: '随机震荡指标' },
  { key: 'MACD', label: 'MACD', category: 'momentum', isSubplot: true, color: '#3b82f6', description: '移动平均收敛发散指标' },

  // Volatility & Channels
  { key: 'BOLL', label: 'BOLL', category: 'channel', isSubplot: false, color: '#9333ea', description: '布林带' },
  { key: 'KELTNER', label: 'KELTNER', category: 'channel', isSubplot: false, color: '#06b6d4', description: '肯特纳通道' },
  { key: 'ICHIMOKU', label: 'ICHIMOKU', category: 'channel', isSubplot: false, color: '#8b5cf6', lineStyle: 2, description: '一目均衡表' },

  // Strength
  { key: 'ATR14', label: 'ATR14', category: 'volatility', isSubplot: true, color: '#8b5cf6', description: '14期平均真实波幅' },
  { key: 'ADX', label: 'ADX', category: 'strength', isSubplot: true, color: '#f97316', description: '平均趋向指数' },
  { key: 'WILLIAMS_R', label: 'WILLIAMS_R', category: 'strength', isSubplot: true, color: '#a855f7', description: '威廉指标' },
]

export const MARKET_FLOW_CONFIGS: MarketFlowConfig[] = [
  { key: 'cvd', label: 'CVD', color: { up: '#22c55e', down: '#ef4444', line: '#3b82f6' } },
  { key: 'taker_volume', label: 'Taker Vol', color: { up: '#22c55e', down: '#ef4444', line: '#3b82f6' } },
  { key: 'oi', label: 'OI', color: { up: '#22c55e', down: '#ef4444', line: '#f59e0b' } },
  { key: 'oi_delta', label: 'OI Delta', color: { up: '#22c55e', down: '#ef4444', line: '#8b5cf6' } },
  { key: 'funding', label: 'Funding', color: { up: '#22c55e', down: '#ef4444', line: '#3b82f6' } },
  { key: 'depth_ratio', label: 'Depth(log)', color: { up: '#22c55e', down: '#ef4444', line: '#a855f7' } },
  { key: 'order_imbalance', label: 'Imbalance', color: { up: '#22c55e', down: '#ef4444', line: '#f97316' } },
]

export const CHART_STYLE = {
  background: 'transparent',
  textColor: CHART_COLORS.text,
  gridColor: CHART_COLORS.grid,
  locale: 'en-US' as const,
}

export const POLLING_INTERVAL = 5000
export const DEFAULT_SERIES_COUNT = 500
