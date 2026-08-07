/**
 * Lightweight in-memory API response cache.
 *
 * - Caches GET responses by URL for a configurable TTL (default 30s).
 * - Deduplicates concurrent identical requests (request coalescing).
 * - Provides `cachedApiGet(endpoint, ttl?)` as a drop-in replacement for
 *   `apiRequest(endpoint).then(r => r.json())`.
 * - Manual invalidation via `invalidateCache(endpoint?)`.
 */

import { apiRequest } from './api'

interface CacheEntry {
  data: any
  timestamp: number
  ttl: number
}

const cache = new Map<string, CacheEntry>()
const inflight = new Map<string, Promise<any>>()

const DEFAULT_TTL = 30_000 // 30 seconds

/**
 * GET an endpoint with in-memory caching + request deduplication.
 *
 * @param endpoint  API path, e.g. "/arena/positions?trading_mode=testnet"
 * @param ttl       Cache lifetime in ms (default 30 000)
 */
export async function cachedApiGet<T = any>(endpoint: string, ttl = DEFAULT_TTL): Promise<T> {
  const now = Date.now()
  const entry = cache.get(endpoint)

  if (entry && now - entry.timestamp < entry.ttl) {
    return entry.data as T
  }

  const pending = inflight.get(endpoint)
  if (pending) return pending as Promise<T>

  const promise = apiRequest(endpoint)
    .then(r => r.json())
    .then(data => {
      cache.set(endpoint, { data, timestamp: Date.now(), ttl })
      inflight.delete(endpoint)
      return data as T
    })
    .catch(err => {
      inflight.delete(endpoint)
      throw err
    })

  inflight.set(endpoint, promise)
  return promise
}

/**
 * Invalidate cache entries.
 * - No argument: clear everything.
 * - With `prefix`: remove entries whose key starts with `prefix`.
 */
export function invalidateCache(prefix?: string): void {
  if (!prefix) {
    cache.clear()
    return
  }
  for (const key of cache.keys()) {
    if (key.startsWith(prefix)) {
      cache.delete(key)
    }
  }
}

/**
 * Get current cache stats (for debugging).
 */
export function getCacheStats() {
  const now = Date.now()
  let active = 0
  let expired = 0
  for (const [, entry] of cache) {
    if (now - entry.timestamp < entry.ttl) active++
    else expired++
  }
  return { total: cache.size, active, expired, inflight: inflight.size }
}
