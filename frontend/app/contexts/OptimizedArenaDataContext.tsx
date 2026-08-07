/**
 * PERFORMANCE OPTIMIZATION: Split Arena Data Context
 *
 * PROBLEM: Single Context causes all consumers to re-render when any data changes
 *
 * SOLUTION: Split into two contexts:
 * - ArenaDataContext: Holds the data (infrequently changes)
 * - ArenaDataDispatchContext: Holds update functions (never changes)
 *
 * This allows components to subscribe only to the parts they need.
 * Components that only call updateData won't re-render when data changes.
 */

import { createContext, useContext, useState, useCallback, ReactNode, useMemo } from 'react'
import { ArenaTrade, ArenaModelChatEntry, ArenaPositionsAccount, ArenaAccountMeta } from '@/lib/api'

interface ArenaDataState {
  trades: ArenaTrade[]
  modelChat: ArenaModelChatEntry[]
  positions: ArenaPositionsAccount[]
  accountsMeta: ArenaAccountMeta[]
  lastFetched: number
}

interface ArenaDataContextType {
  data: Record<string, ArenaDataState>
}

interface ArenaDataDispatchContextType {
  updateData: (accountKey: string, newData: Partial<ArenaDataState>) => void
  getData: (accountKey: string) => ArenaDataState | null
}

// Create separate contexts
const ArenaDataContext = createContext<ArenaDataContextType | undefined>(undefined)
const ArenaDataDispatchContext = createContext<ArenaDataDispatchContextType | undefined>(undefined)

/**
 * Provider that manages arena data
 *
 * PERFORMANCE: Contexts are split to prevent unnecessary re-renders
 */
export function OptimizedArenaDataProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<Record<string, ArenaDataState>>({})

  // Memoize dispatch functions so they never change
  const updateData = useCallback((accountKey: string, newData: Partial<ArenaDataState>) => {
    setData(prev => {
      const existing = prev[accountKey] || {
        trades: [],
        modelChat: [],
        positions: [],
        accountsMeta: [],
        lastFetched: 0
      }

      return {
        ...prev,
        [accountKey]: {
          ...existing,
          ...newData,
          lastFetched: newData.lastFetched ?? Date.now()
        }
      }
    })
  }, [])

  const getData = useCallback((accountKey: string) => {
    return data[accountKey] || null
  }, [data])

  // Memoize dispatch context value to prevent re-renders
  const dispatchValue = useMemo(() => ({
    updateData,
    getData
  }), [updateData, getData])

  // Memoize data context value
  const dataValue = useMemo(() => ({
    data
  }), [data])

  return (
    <ArenaDataContext.Provider value={dataValue}>
      <ArenaDataDispatchContext.Provider value={dispatchValue}>
        {children}
      </ArenaDataDispatchContext.Provider>
    </ArenaDataContext.Provider>
  )
}

/**
 * Hook to access arena data
 *
 * PERFORMANCE: Components using this hook will ONLY re-render when data changes
 * Use this when you need to READ data
 */
export function useArenaDataContext() {
  const context = useContext(ArenaDataContext)
  if (context === undefined) {
    throw new Error('useArenaDataContext must be used within an OptimizedArenaDataProvider')
  }
  return context.data
}

/**
 * Hook to access arena data dispatch functions
 *
 * PERFORMANCE: Components using this hook will NEVER re-render
 * Use this when you only need to UPDATE data
 */
export function useArenaDataDispatch() {
  const context = useContext(ArenaDataDispatchContext)
  if (context === undefined) {
    throw new Error('useArenaDataDispatch must be used within an OptimizedArenaDataProvider')
  }
  return context
}

/**
 * Optimized hook to access specific account data
 *
 * PERFORMANCE: Component will only re-render when THIS ACCOUNT's data changes
 *
 * Usage:
 * ```tsx
 * function MyComponent({ accountId }) {
 *   const accountData = useArenaAccountData(accountId)
 *   // Only re-renders when this account's data changes
 * }
 * ```
 */
export function useArenaAccountData(accountKey: string) {
  const data = useArenaDataContext()
  return useMemo(() => data[accountKey] || null, [data, accountKey])
}

/**
 * Optimized hook to access trades for a specific account
 *
 * PERFORMANCE: Component will only re-render when trades change for this account
 */
export function useArenaTrades(accountKey: string) {
  const accountData = useArenaAccountData(accountKey)
  return useMemo(() => accountData?.trades || [], [accountData])
}

/**
 * Optimized hook to access model chat for a specific account
 *
 * PERFORMANCE: Component will only re-render when model chat changes for this account
 */
export function useArenaModelChat(accountKey: string) {
  const accountData = useArenaAccountData(accountKey)
  return useMemo(() => accountData?.modelChat || [], [accountData])
}

/**
 * Optimized hook to access positions for a specific account
 *
 * PERFORMANCE: Component will only re-render when positions change for this account
 */
export function useArenaPositions(accountKey: string) {
  const accountData = useArenaAccountData(accountKey)
  return useMemo(() => accountData?.positions || [], [accountData])
}

/**
 * Convenience hook for backward compatibility
 * Combines both data and dispatch (may cause extra re-renders)
 *
 * PERFORMANCE AWARE: If you only need to read OR write, use the specific hooks above
 */
export function useArenaData() {
  const data = useArenaDataContext()
  const dispatch = useArenaDataDispatch()

  return useMemo(() => ({
    data,
    updateData: dispatch.updateData,
    getData: dispatch.getData
  }), [data, dispatch])
}
