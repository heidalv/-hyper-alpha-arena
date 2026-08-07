/**
 * 市场扫描器 API
 * 对接后端 Phase 4 MarketScanner / AnomalyDetector
 */

import { apiRequest } from '@/lib/api'

export interface SymbolScore {
  symbol: string
  volume_score: number
  volatility_score: number
  trend_score: number
  funding_score: number
  total_score: number
}

export interface ScanResult {
  timestamp: string
  top_symbols: SymbolScore[]
  total_scanned: number
  scan_duration_ms: number
  error?: string
  status?: string
}

export interface AnomalyEvent {
  symbol: string
  anomaly_type: string
  severity: 'low' | 'medium' | 'high' | 'critical'
  z_score: number
  value: number
  expected_range: [number, number]
  detected_at: string
  description: string
}

export interface AnomalyReport {
  timestamp: string
  events: AnomalyEvent[]
  symbols_scanned: number
  error?: string
}

export interface RegimeClassification {
  symbol: string
  regime: 'trending' | 'ranging' | 'volatile' | 'crash'
  confidence: number
  trend_direction: 'up' | 'down' | 'neutral'
  volatility_percentile: number
  volume_percentile: number
  timestamp: string
}

export interface ScanConfig {
  top_n: number
  min_volume: number
  enabled: boolean
  anomaly_enabled: boolean
}

export async function triggerMarketScan(params?: {
  top_n?: number
  min_volume?: number
}): Promise<ScanResult> {
  try {
    const res = await apiRequest('/market/scan', {
      method: 'POST',
      body: JSON.stringify(params || {}),
    })
    return await res.json()
  } catch (e: any) {
    throw new Error(e?.message && !e.message.startsWith('HTTP undefined') ? e.message : 'Market scan failed')
  }
}

export async function getLatestScanResult(): Promise<ScanResult | null> {
  try {
    const res = await apiRequest('/market/scan/latest')
    const data = await res.json()
    return data
  } catch {
    return null
  }
}

export async function getAnomalyReport(): Promise<AnomalyReport | null> {
  try {
    const res = await apiRequest('/market/anomaly/latest')
    const data = await res.json()
    return data
  } catch {
    return null
  }
}

export async function getRegimeClassifications(): Promise<RegimeClassification[]> {
  try {
    const res = await apiRequest('/market/regime/list')
    const data = await res.json()
    return Array.isArray(data) ? data : []
  } catch {
    return []
  }
}

export async function getRegimeClassification(symbol: string): Promise<RegimeClassification | null> {
  try {
    const res = await apiRequest(`/market/regime/${encodeURIComponent(symbol)}`)
    return await res.json()
  } catch {
    return null
  }
}

export async function getScanConfig(): Promise<ScanConfig> {
  try {
    const res = await apiRequest('/market/scan/config')
    return await res.json()
  } catch {
    return { top_n: 20, min_volume: 1000000, enabled: true, anomaly_enabled: true }
  }
}

export async function updateScanConfig(config: Partial<ScanConfig>): Promise<ScanConfig> {
  try {
    const res = await apiRequest('/market/scan/config', {
      method: 'PUT',
      body: JSON.stringify(config),
    })
    return await res.json()
  } catch (e: any) {
    throw new Error(e.message || '更新配置失败')
  }
}

export async function getScannableSymbols(): Promise<{ symbols: string[]; count: number }> {
  try {
    const res = await apiRequest('/market/symbols')
    return await res.json()
  } catch {
    return { symbols: [], count: 0 }
  }
}
