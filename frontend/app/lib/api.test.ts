/**
 * API Layer Unit Tests
 * 
 * Tests for V3 frontend API modules:
 * - marketScannerApi.ts
 * - exchangeApi.ts
 * - aiLearningApi.ts
 * - api.ts (core)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// ── marketScannerApi tests ──

describe('marketScannerApi', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('getLatestScanResult returns null on failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      json: () => Promise.resolve({}),
    } as Response)
    
    const { getLatestScanResult } = await import('@/lib/marketScannerApi')
    const result = await getLatestScanResult()
    expect(result).toBeNull()
  })

  it('getAnomalyReport returns null on failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      json: () => Promise.resolve({}),
    } as Response)
    
    const { getAnomalyReport } = await import('@/lib/marketScannerApi')
    const result = await getAnomalyReport()
    expect(result).toBeNull()
  })

  it('getRegimeClassifications returns empty array on failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      json: () => Promise.resolve({}),
    } as Response)
    
    const { getRegimeClassifications } = await import('@/lib/marketScannerApi')
    const result = await getRegimeClassifications()
    expect(result).toEqual([])
  })

  it('getScanConfig returns defaults on failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      json: () => Promise.resolve({}),
    } as Response)
    
    const { getScanConfig } = await import('@/lib/marketScannerApi')
    const result = await getScanConfig()
    expect(result.top_n).toBe(20)
    expect(result.min_volume).toBe(1000000)
    expect(result.enabled).toBe(true)
    expect(result.anomaly_enabled).toBe(true)
  })

  it('triggerMarketScan throws on failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      json: () => Promise.resolve({}),
    } as Response)
    
    const { triggerMarketScan } = await import('@/lib/marketScannerApi')
    await expect(triggerMarketScan()).rejects.toThrow('Market scan failed')
  })
})

// ── exchangeApi tests ──

describe('exchangeApi', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('getExchangeStatuses returns empty on failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      json: () => Promise.resolve([]),
    } as Response)
    
    const { getExchangeStatuses } = await import('@/lib/exchangeApi')
    const result = await getExchangeStatuses()
    expect(result).toEqual([])
  })

  it('getCrossExchangeExposure returns safe defaults on failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      json: () => Promise.resolve({}),
    } as Response)
    
    const { getCrossExchangeExposure } = await import('@/lib/exchangeApi')
    const result = await getCrossExchangeExposure()
    expect(result.is_safe).toBe(true)
    expect(result.active_trades).toBe(0)
    expect(result.total_exposure_pct).toBe(0)
  })

  it('getLegRiskStatuses returns empty on failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      json: () => Promise.resolve([]),
    } as Response)
    
    const { getLegRiskStatuses } = await import('@/lib/exchangeApi')
    const result = await getLegRiskStatuses()
    expect(result).toEqual([])
  })
})
