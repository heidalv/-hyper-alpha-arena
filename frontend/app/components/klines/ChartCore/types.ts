/**
 * ChartCore — 共享类型定义
 */

export interface KlineBar {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface IndicatorSeries {
  key: string
  label: string
  category: 'trend' | 'volume' | 'momentum' | 'volatility' | 'strength' | 'channel'
  isSubplot: boolean
  color: string
  lineStyle?: number
  description: string
}

export interface MarketFlowConfig {
  key: string
  label: string
  color: { up: string; down: string; line: string }
}

export interface PatternInfo {
  id: string
  name: string
  pattern_type: 'bullish' | 'bearish' | 'neutral'
  timestamp: number
  confidence: number
  description: string
  trading_hints: string[]
  reliability: string
}

export interface SRLevel {
  price: number
  label: string
  level_type: 'support' | 'resistance' | 'neutral'
  method: string
  strength: number
}

export interface VolumeAnomalyInfo {
  timestamp: number
  type: string          // volume_spike | climax_volume | volume_dry_up | accumulation | distribution
  severity: 'high' | 'medium' | 'low'
  description: string
  zscore: number
}

export type ChartType = 'candlestick' | 'line' | 'area'
