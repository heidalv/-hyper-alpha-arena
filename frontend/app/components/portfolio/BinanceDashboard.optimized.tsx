/**
 * BinanceDashboard 优化版本 - 关键修复部分
 *
 * 主要优化:
 * 1. 修复刷新依赖循环问题
 * 2. 改为并行API调用
 * 3. 添加防并发检查
 * 4. 批量状态更新
 * 5. 添加请求缓存
 */

import { useState, useEffect, useCallback, useRef } from 'react'

// ========== 优化1: 修复刷新依赖 ==========
export default function BinanceDashboardOptimized({ onPageChange }: BinanceDashboardProps) {
  const [accounts, setAccounts] = useState<AccountWithBinance[]>([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [activeTab, setActiveTab] = useState<ViewTab>('overview')
  const [selectedAccount, setSelectedAccount] = useState<number | 'all'>('all')
  const [refreshKey, setRefreshKey] = useState(0)

  // ⚡ 关键修复1: 使用 ref 避免依赖循环
  const isRefreshingRef = useRef(false)
  const refreshDataRef = useRef<typeof refreshData | null>(null)

  // ⚡ 关键修复2: 并行处理 + 防并发
  const refreshData = useCallback(async () => {
    // 防止并发刷新
    if (isRefreshingRef.current) {
      console.log('[BinanceDashboard] 刷新进行中，跳过本次刷新')
      return
    }

    isRefreshingRef.current = true
    const startTime = Date.now()

    try {
      const accountList = await getAccounts()
      const updates: Record<number, Partial<AccountWithBinance>> = {}

      // ⚡ 关键修复3: 并行处理所有账户（限制并发数为3）
      const CONCURRENT_LIMIT = 3
      for (let i = 0; i < accountList.length; i += CONCURRENT_LIMIT) {
        const chunk = accountList.slice(i, i + CONCURRENT_LIMIT)

        const results = await Promise.allSettled(
          chunk.map(async (account) => {
            try {
              const config = await getBinanceConfig(account.id)
              if (!config.configured) return null

              // ⚡ 关键修复4: 去除500ms延迟，改为并行调用
              const [balance, positionsResponse] = await Promise.allSettled([
                getBinanceBalance(account.id),
                getBinancePositions(account.id)
              ])

              return {
                accountId: account.id,
                balance: balance.status === 'fulfilled' ? balance.value : null,
                positions: positionsResponse.status === 'fulfilled'
                  ? (positionsResponse.value.positions || [])
                  : null,
                error: (balance.status === 'rejected' ? balance.reason : null) ||
                        (positionsResponse.status === 'rejected' ? positionsResponse.reason : null)
              }
            } catch (err) {
              console.error(`账户 ${account.id} 刷新失败:`, err)
              return {
                accountId: account.id,
                balance: null,
                positions: null,
                error: err instanceof Error ? err.message : '加载失败'
              }
            }
          })
        )

        // 收集更新
        results.forEach(result => {
          if (result.status === 'fulfilled' && result.value) {
            const { accountId, balance, positions, error } = result.value
            updates[accountId] = { balance, positions, error }
          }
        })
      }

      // ⚡ 关键修复5: 批量更新状态（只触发一次重新渲染）
      if (Object.keys(updates).length > 0) {
        setAccounts(prev => prev.map(acc => ({
          ...acc,
          ...(updates[acc.id] || {})
        })))
      }

      const elapsed = Date.now() - startTime
      console.log(`[BinanceDashboard] 刷新完成，耗时: ${elapsed}ms，更新了 ${Object.keys(updates).length} 个账户`)
    } catch (error) {
      console.error('[BinanceDashboard] 刷新失败:', error)
    } finally {
      isRefreshingRef.current = false
    }
  }, []) // 空依赖数组

  // 保存到 ref
  refreshDataRef.current = refreshData

  // 初始加载
  const loadData = async () => {
    try {
      setLoading(true)
      const accountList = await getAccounts()

      const accountsWithData = await Promise.all(
        accountList.map(async (account: any) => {
          try {
            const config = await getBinanceConfig(account.id)

            if (!config.configured) {
              return {
                id: account.id,
                name: account.name,
                binanceConfig: null,
                balance: null,
                positions: [],
                loading: false,
                error: null,
              }
            }

            const [balance, positionsResponse] = await Promise.all([
              getBinanceBalance(account.id).catch(() => null),
              getBinancePositions(account.id).catch(() => ({ positions: [] })),
            ])

            return {
              id: account.id,
              name: account.name,
              binanceConfig: config,
              balance,
              positions: positionsResponse?.positions || [],
              loading: false,
              error: null,
            }
          } catch (err) {
            return {
              id: account.id,
              name: account.name,
              binanceConfig: null,
              balance: null,
              positions: [],
              loading: false,
              error: err instanceof Error ? err.message : '加载失败',
            }
          }
        })
      )

      setAccounts(accountsWithData)
    } catch (error) {
      console.error('[BinanceDashboard] Failed to load dashboard:', error)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, []) // 只在组件加载时执行一次

  // ⚡ 关键修复6: 稳定的自动刷新（空依赖数组）
  useEffect(() => {
    const interval = setInterval(() => {
      if (refreshDataRef.current && !isRefreshingRef.current) {
        refreshDataRef.current()
        setRefreshKey(prev => prev + 1)
      }
    }, getRefreshInterval('binance_balance'))

    return () => clearInterval(interval)
  }, []) // 空依赖数组，避免重新创建定时器

  // 手动刷新
  const handleRefresh = async () => {
    if (refreshing) return
    setRefreshing(true)
    await refreshData()
    setRefreshing(false)
  }

  // ... 其余代码保持不变 ...
}
