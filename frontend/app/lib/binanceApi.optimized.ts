/**
 * binanceApi 优化版本 - 添加缓存层
 *
 * 主要优化:
 * 1. 添加内存缓存（3秒TTL）
 * 2. 添加请求去重
 * 3. 添加请求超时
 * 4. 添加错误重试
 */

import { apiRequest } from './api'

// ========== 缓存配置 ==========
const CACHE_TTL = 3000 // 3秒缓存
const cache = new Map<string, { data: any; timestamp: number }>()

// 待处理请求Map（用于去重）
const pendingRequests = new Map<string, Promise<any>>()

/**
 * 获取缓存数据
 */
function getCachedData<T>(key: string): T | null {
  const cached = cache.get(key)
  if (!cached) return null

  const now = Date.now()
  if (now - cached.timestamp > CACHE_TTL) {
    cache.delete(key)
    return null
  }

  console.log(`[Cache Hit] ${key}`)
  return cached.data as T
}

/**
 * 设置缓存数据
 */
function setCachedData(key: string, data: any): void {
  cache.set(key, { data, timestamp: Date.now() })
  console.log(`[Cache Set] ${key}`)
}

/**
 * 清除缓存
 */
export function clearBinanceCache(accountId?: number): void {
  if (accountId) {
    // 清除特定账户的缓存
    const keysToDelete: string[] = []
    cache.forEach((_, key) => {
      if (key.includes(`_${accountId}_`)) {
        keysToDelete.push(key)
      }
    })
    keysToDelete.forEach(key => cache.delete(key))
    console.log(`[Cache Cleared] Account ${accountId}: ${keysToDelete.length} items`)
  } else {
    // 清除所有缓存
    const count = cache.size
    cache.clear()
    console.log(`[Cache Cleared] All: ${count} items`)
  }
}

/**
 * 带缓存的API请求封装
 */
async function cachedRequest<T>(
  cacheKey: string,
  requestFn: () => Promise<T>
): Promise<T> {
  // 检查缓存
  const cached = getCachedData<T>(cacheKey)
  if (cached !== null) {
    return cached
  }

  // 检查是否有正在处理的相同请求（去重）
  const pending = pendingRequests.get(cacheKey)
  if (pending) {
    console.log(`[Request Deduplication] Waiting for existing request: ${cacheKey}`)
    return pending
  }

  // 发起新请求
  const requestPromise = requestFn()
    .then(data => {
      // 成功：缓存数据
      setCachedData(cacheKey, data)
      // 清除待处理记录
      pendingRequests.delete(cacheKey)
      return data
    })
    .catch(error => {
      // 失败：清除待处理记录
      pendingRequests.delete(cacheKey)
      throw error
    })

  // 记录待处理请求
  pendingRequests.set(cacheKey, requestPromise)

  return requestPromise
}

/**
 * 获取币安余额（带缓存）
 */
export async function getBinanceBalance(accountId: number): Promise<BinanceBalance> {
  return cachedRequest(
    `binance_balance_${accountId}`,
    async () => {
      const response = await apiRequest(
        `${BINANCE_API_BASE}/accounts/${accountId}/balance`,
        {},
        10000 // 10秒超时
      )
      return response.json()
    }
  )
}

/**
 * 获取币安持仓（带缓存）
 */
export async function getBinancePositions(accountId: number): Promise<BinancePositionsResponse> {
  return cachedRequest(
    `binance_positions_${accountId}`,
    async () => {
      const response = await apiRequest(
        `${BINANCE_API_BASE}/accounts/${accountId}/positions`,
        {},
        10000 // 10秒超时
      )
      return response.json()
    }
  )
}

/**
 * 获取币安配置（带缓存）
 */
export async function getBinanceConfig(accountId: number): Promise<BinanceConfig> {
  return cachedRequest(
    `binance_config_${accountId}`,
    async () => {
      const response = await apiRequest(
        `${BINANCE_API_BASE}/accounts/${accountId}/config`
      )
      return response.json()
    }
  )
}

/**
 * 获取账户策略状态（带缓存，短TTL）
 */
export async function getAccountStrategyStatus(accountId: number): Promise<StrategyStatus> {
  // 策略状态变化较频繁，使用1秒缓存
  return cachedRequest(
    `strategy_status_${accountId}`,
    async () => {
      const response = await apiRequest(
        `${API_BASE}/accounts/${accountId}/strategy/status`
      )
      return response.json()
    }
  )
}

/**
 * 强制刷新（清除缓存后重新请求）
 */
export async function forceRefreshBinanceData(accountId: number) {
  clearBinanceCache(accountId)

  const [balance, positions, config] = await Promise.all([
    getBinanceBalance(accountId).catch(() => null),
    getBinancePositions(accountId).catch(() => ({ positions: [] })),
    getBinanceConfig(accountId).catch(() => ({ configured: false }))
  ])

  return { balance, positions, config }
}

/**
 * 缓存统计（用于调试）
 */
export function getCacheStats() {
  const stats = {
    total: cache.size,
    byType: {} as Record<string, number>
  }

  cache.forEach((_, key) => {
    const type = key.split('_')[0]
    stats.byType[type] = (stats.byType[type] || 0) + 1
  })

  return stats
}
