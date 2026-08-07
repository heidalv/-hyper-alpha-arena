import React, { useEffect, useMemo, useState, useRef, useCallback, startTransition } from 'react'
import { useInterval } from '@/hooks/useInterval'
import { useTranslation } from 'react-i18next'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { usePageActive } from '@/hooks/usePageActive'
import {
  ArenaAccountMeta,
  ArenaModelChatEntry,
  ArenaPositionsAccount,
  ArenaTrade,
  getArenaModelChat,
  getArenaPositions,
  getArenaTrades,
  getAccounts,
  getModelChatSnapshots,
  ModelChatSnapshots,
  getHyperliquidWatchlist,
  updateArenaPnl,
} from '@/lib/api'
import { useArenaData } from '@/contexts/ArenaDataContext'
import { useTradingMode } from '@/contexts/TradingModeContext'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { getModelLogo } from './logoAssets'
import FlipNumber from './FlipNumber'
import HighlightWrapper from './HighlightWrapper'
import { formatDateTime } from '@/lib/dateTime'
import { Loader2 } from 'lucide-react'
import { copyToClipboard } from '@/lib/utils'

interface AlphaArenaFeedProps {
  refreshKey?: number
  autoRefreshInterval?: number
  wsRef?: React.MutableRefObject<WebSocket | null>
  selectedAccount?: number | 'all'
  onSelectedAccountChange?: (accountId: number | 'all') => void
  walletAddress?: string
  onPageChange?: (page: string) => void
}

type FeedTab = 'model-chat'

const DEFAULT_LIMIT = 30
const MODEL_CHAT_LIMIT = 60

type CacheKey = string

// Use formatDateTime from @/lib/dateTime with 'short' style for compact display
const formatDate = (value?: string | null) => formatDateTime(value, { style: 'short' })

function formatPercent(value?: number | null) {
  if (value === undefined || value === null) return '—'
  return `${(value * 100).toFixed(2)}%`
}

function renderSymbolBadge(symbol?: string, size: 'sm' | 'md' = 'md') {
  if (!symbol) return null
  const text = symbol.slice(0, 4).toUpperCase()
  const baseClasses = 'inline-flex items-center justify-center rounded bg-muted text-muted-foreground font-semibold'
  const sizeClasses = size === 'sm' ? 'h-4 w-4 text-[9px]' : 'h-5 w-5 text-[10px]'
  return <span className={`${baseClasses} ${sizeClasses}`}>{text}</span>
}

// 三周期配置：中文名 + 独立边框/背景色（与方向颜色解耦）
const TIMEFRAME_CONFIG: Record<string, { label: string; border: string; bg: string; text: string }> = {
  short:  { label: '短期', border: 'border-amber-500/30',  bg: 'bg-amber-500/5',  text: 'text-amber-700 dark:text-amber-400' },
  mid:    { label: '中期', border: 'border-blue-500/30',   bg: 'bg-blue-500/5',   text: 'text-blue-700 dark:text-blue-400' },
  long:   { label: '长期', border: 'border-purple-500/30', bg: 'bg-purple-500/5', text: 'text-purple-700 dark:text-purple-400' },
}

// 方向颜色映射
const BIAS_ARROW: Record<string, string> = {
  bullish: '▲',
  bearish: '▼',
  neutral: '─',
}
const BIAS_TEXT_COLOR: Record<string, string> = {
  bullish: 'text-emerald-600',
  bearish: 'text-red-500',
  neutral: 'text-gray-400',
}

function renderTimeframeIndicator(tfKey: string, bias?: string | null, confidence?: number | null) {
  const cfg = TIMEFRAME_CONFIG[tfKey]
  if (!cfg) return null
  // 无数据（bias 为 null/undefined）完全不显示；但 neutral 仍显示为灰色
  if (!bias) return null
  
  const arrow = BIAS_ARROW[bias] || '─'
  const arrowColor = BIAS_TEXT_COLOR[bias] || 'text-gray-400'
  const hasSignal = bias !== 'neutral' || (confidence != null && confidence > 0)
  const confPct = hasSignal && confidence != null ? `${(confidence * 100).toFixed(0)}%` : ''
  
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] leading-none ${cfg.border} ${cfg.bg} ${!hasSignal ? 'opacity-50' : ''}`}>
      <span className={`font-medium ${cfg.text}`}>{cfg.label}</span>
      <span className={arrowColor}>{arrow}</span>
      {confPct && <span className="text-muted-foreground tabular-nums">{confPct}</span>}
    </span>
  )
}

function renderTimeframeRow(entry: ArenaModelChatEntry) {
  // 始终显示三个周期，无 bias 时跳过
  const indicators = [
    renderTimeframeIndicator('short', entry.short_bias, entry.short_confidence),
    renderTimeframeIndicator('mid', entry.mid_bias, entry.mid_confidence),
    renderTimeframeIndicator('long', entry.long_bias, entry.long_confidence),
  ].filter(Boolean)
  if (indicators.length === 0) return null  // 连 mid 都没有，完全不显示
  
  return (
    <div className="flex flex-wrap items-center gap-1.5 mt-1.5 pt-1.5 border-t border-border/30">
      {indicators}
    </div>
  )
}


export default function AlphaArenaFeed({
  refreshKey,
  autoRefreshInterval = 60_000,
  wsRef,
  selectedAccount: selectedAccountProp,
  onSelectedAccountChange,
  walletAddress,
  onPageChange,
}: AlphaArenaFeedProps) {
  const { t } = useTranslation()
  const { getData, updateData } = useArenaData()
  const { tradingMode } = useTradingMode()
  const pageActive = usePageActive()
  const [activeTab, setActiveTab] = useState<FeedTab>('model-chat')
  const [allTraderOptions, setAllTraderOptions] = useState<ArenaAccountMeta[]>([])
  const [loadingAccounts, setLoadingAccounts] = useState(false)
  const [internalSelectedAccount, setInternalSelectedAccount] = useState<number | 'all'>(
    selectedAccountProp ?? 'all',
  )
  const [expandedChat, setExpandedChat] = useState<number | null>(null)
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({})
  const [copiedSections, setCopiedSections] = useState<Record<string, boolean>>({})
  const [fullViewSections, setFullViewSections] = useState<Set<string>>(new Set())
  const [manualRefreshKey, setManualRefreshKey] = useState(0)
  const [loadingTrades, setLoadingTrades] = useState(false)
  const [loadingModelChat, setLoadingModelChat] = useState(false)
  const [loadingPositions, setLoadingPositions] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [updatingPnl, setUpdatingPnl] = useState(false)
  const [pnlUpdateResult, setPnlUpdateResult] = useState<string | null>(null)
  const [showPnlConfirm, setShowPnlConfirm] = useState(false)

  const [trades, setTrades] = useState<ArenaTrade[]>([])
  const [modelChat, setModelChat] = useState<ArenaModelChatEntry[]>([])
  const [positions, setPositions] = useState<ArenaPositionsAccount[]>([])
  const [accountsMeta, setAccountsMeta] = useState<ArenaAccountMeta[]>([])

  // Lazy loading states for ModelChat
  const [hasMoreModelChat, setHasMoreModelChat] = useState(true)
  const [isLoadingMoreModelChat, setIsLoadingMoreModelChat] = useState(false)

  // Snapshot lazy loading cache and states
  // PERFORMANCE: Bounded cache — evicts oldest entries when exceeding MAX_SNAPSHOT_CACHE
  const MAX_SNAPSHOT_CACHE = 50
  const snapshotCache = useRef<Map<number, ModelChatSnapshots>>(new Map())
  const [loadingSnapshots, setLoadingSnapshots] = useState<Set<number>>(new Set())

  // New states for symbol selection
  const [symbolOptions, setSymbolOptions] = useState<string[]>([])
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)

  // Track seen items for highlight animation
  const seenTradeIds = useRef<Set<number>>(new Set())
  const seenDecisionIds = useRef<Set<number>>(new Set())
  const prevManualRefreshKey = useRef(manualRefreshKey)
  const prevRefreshKey = useRef(refreshKey)
  const prevTradingMode = useRef(tradingMode)

  // Ref for model chat scroll container
  const modelChatScrollRef = useRef<HTMLDivElement>(null)

  // PERFORMANCE: Keep a ref in sync so callbacks can read current value without re-creating
  const modelChatRef = useRef(modelChat)
  useEffect(() => { modelChatRef.current = modelChat }, [modelChat])

  // Sync external account selection with internal state
  useEffect(() => {
    if (selectedAccountProp !== undefined) {
      setInternalSelectedAccount(selectedAccountProp)
    }
  }, [selectedAccountProp])

  // Compute active account and cache key
  const activeAccount = useMemo(() => selectedAccountProp ?? internalSelectedAccount, [selectedAccountProp, internalSelectedAccount])
  const prevActiveAccount = useRef<number | 'all'>(activeAccount)
  const cacheKey: CacheKey = useMemo(() => {
    const accountKey = activeAccount === 'all' ? 'all' : String(activeAccount)
    const walletKey = walletAddress ? walletAddress.toLowerCase() : 'nowallet'
    return `${accountKey}_${tradingMode}_${walletKey}`
  }, [activeAccount, tradingMode, walletAddress])

  // PERFORMANCE: Ref for stable callbacks — filters read from ref so callbacks don't rebuild on filter changes
  const filterRef = useRef({ activeAccount, selectedSymbol, tradingMode, walletAddress, cacheKey })
  useEffect(() => {
    filterRef.current = { activeAccount, selectedSymbol, tradingMode, walletAddress, cacheKey }
  }, [activeAccount, selectedSymbol, tradingMode, walletAddress, cacheKey])

  // Initialize from global state on mount or account change
  useEffect(() => {
    const globalData = getData(cacheKey)
    if (globalData) {
      setTrades(globalData.trades)
      setModelChat(globalData.modelChat)
      setPositions(globalData.positions)
      setAccountsMeta(globalData.accountsMeta)
      setLoadingTrades(false)
      setLoadingModelChat(false)
      setLoadingPositions(false)
    }
  }, [cacheKey, getData])

  const writeCache = useCallback(
    (key: CacheKey, data: Partial<{ trades: ArenaTrade[]; modelChat: ArenaModelChatEntry[]; positions: ArenaPositionsAccount[] }>) => {
      updateData(key, data)
    },
    [updateData],
  )

  // Listen for real-time WebSocket updates
  // PERFORMANCE: Uses startTransition for non-urgent cache updates to reduce render blocking
  useEffect(() => {
    if (!wsRef?.current) return

    const handleMessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data)

        // Filter by trading mode/environment first
        const msgEnvironment = msg.trade?.environment || msg.decision?.environment || msg.trading_mode
        if (msgEnvironment && msgEnvironment !== tradingMode) {
          // Ignore messages from different trading environments
          return
        }

        // Only process messages for the active account or all accounts
        const msgAccountId = msg.trade?.account_id || msg.decision?.account_id
        const shouldProcess = activeAccount === 'all' || !msgAccountId || msgAccountId === activeAccount

        if (!shouldProcess) return

        const messageWallet: string | undefined =
          msg.trade?.wallet_address || msg.decision?.wallet_address || undefined
        if (walletAddress) {
          if (!messageWallet) return
          if (messageWallet.toLowerCase() !== walletAddress.toLowerCase()) return
        }

        if (msg.type === 'trade_update' && msg.trade) {
          // Prepend new trade to the list
          setTrades((prev) => {
            // Check if trade already exists to prevent duplicates
            const exists = prev.some((t) => t.trade_id === msg.trade.trade_id)
            if (exists) return prev
            const next = [msg.trade, ...prev].slice(0, DEFAULT_LIMIT)
            // PERFORMANCE: Defer cache write to lower priority to prevent blocking UI
            startTransition(() => {
              writeCache(cacheKey, { trades: next })
            })
            return next
          })
        }

        if (msg.type === 'position_update' && msg.positions) {
          // Update positions for the relevant account
          setPositions((prev) => {
            // If no account_id specified in message, this is a full update for one account
            const accountId = msg.positions[0]?.account_id
            if (!accountId) return msg.positions

            // Replace positions for this specific account
            const otherAccounts = prev.filter((acc) => acc.account_id !== accountId)
            // Find if we have position data in the message
            const newAccountPositions = msg.positions.filter((p: any) => p.account_id === accountId)

            if (newAccountPositions.length > 0) {
              // Construct account snapshot from positions
            const previousMeta = prev.find((acc) => acc.account_id === accountId)
            const accountSnapshot = {
                account_id: accountId,
                account_name: previousMeta?.account_name || '',
                environment: previousMeta?.environment || null,
                available_cash: 0, // Will be updated by next snapshot
                used_margin: previousMeta?.used_margin ?? 0,
                total_unrealized_pnl: 0,
                total_assets: previousMeta?.total_assets ?? 0,
                initial_capital: previousMeta?.initial_capital ?? 0,
                total_return: previousMeta?.total_return ?? null,
                margin_usage_percent: previousMeta?.margin_usage_percent ?? null,
                margin_mode: previousMeta?.margin_mode ?? null,
                positions: newAccountPositions,
              }
              const next = [...otherAccounts, accountSnapshot]
              // PERFORMANCE: Defer cache write to lower priority
              startTransition(() => {
                writeCache(cacheKey, { positions: next })
              })
              return next
            }

            return prev
          })
        }

        if (msg.type === 'model_chat_update' && msg.decision) {
          // Prepend new AI decision to the list
          setModelChat((prev) => {
            // Check if decision already exists to prevent duplicates
            const exists = prev.some((entry) => entry.id === msg.decision.id)
            if (exists) return prev
            const next = [msg.decision, ...prev].slice(0, MODEL_CHAT_LIMIT)
            // PERFORMANCE: Defer cache write to lower priority
            startTransition(() => {
              writeCache(cacheKey, { modelChat: next })
            })

            // 自动滚动到顶部显示新的AI决策
            setTimeout(() => {
              if (modelChatScrollRef.current) {
                modelChatScrollRef.current.scrollTo({ top: 0, behavior: 'smooth' })
              }
            }, 100)

            return next
          })
        }
      } catch (err) {
        console.error('Failed to parse AlphaArenaFeed WebSocket message:', err)
      }
    }

    wsRef.current.addEventListener('message', handleMessage)

    return () => {
      wsRef.current?.removeEventListener('message', handleMessage)
    }
  }, [wsRef, activeAccount, cacheKey, walletAddress, writeCache])

  // Load accounts for dropdown - use dedicated API instead of positions data
  const loadAccounts = useCallback(async () => {
    try {
      setLoadingAccounts(true)
      const accounts = await getAccounts()
      const accountMetas = accounts.map(acc => ({
        account_id: acc.id,
        name: acc.name,
        model: acc.model ?? null,
      }))
      setAllTraderOptions(accountMetas)
    } catch (err) {
      console.error('[AlphaArenaFeed] Failed to load accounts:', err)
    } finally {
      setLoadingAccounts(false)
    }
  }, [])

  // Load accounts immediately on mount
  useEffect(() => {
    if (allTraderOptions.length === 0 && !loadingAccounts) {
      loadAccounts()
    }
  }, [])

  // Individual loaders for each data type
  // PERFORMANCE: Reads filters from filterRef so the callback identity is stable
  const loadTradesData = useCallback(async () => {
    try {
      setLoadingTrades(true)
      const { activeAccount, selectedSymbol, tradingMode, walletAddress, cacheKey } = filterRef.current
      const accountId = activeAccount === 'all' ? undefined : activeAccount
      const symbol = selectedSymbol || undefined
      const tradeRes = await getArenaTrades({
        limit: DEFAULT_LIMIT,
        account_id: accountId,
        trading_mode: tradingMode,
        wallet_address: walletAddress,
        symbol: symbol,
      })
      const newTrades = tradeRes.trades || []
      setTrades(newTrades)
      updateData(cacheKey, { trades: newTrades })

      // Extract metadata from trades
      if (tradeRes.accounts) {
        const metas = tradeRes.accounts
        setAccountsMeta(prev => {
          const metaMap = new Map(prev.map(m => [m.account_id, m]))
          metas.forEach(m => metaMap.set(m.account_id, m))
          return Array.from(metaMap.values())
        })
        updateData(cacheKey, { accountsMeta: Array.from(new Map(tradeRes.accounts.map(m => [m.account_id, m])).values()) })
      }

      setLoadingTrades(false)
      return tradeRes
    } catch (err) {
      console.error('[AlphaArenaFeed] Failed to load trades:', err)
      setLoadingTrades(false)
      return null
    }
  }, [updateData])

  // Helper function to merge and deduplicate model chat entries
  const mergeModelChatData = useCallback((existing: ArenaModelChatEntry[], newData: ArenaModelChatEntry[]) => {
    // Create a Map for fast lookup by id
    const idMap = new Map(existing.map(item => [item.id, item]))

    // Add new data, skip duplicates
    newData.forEach(item => {
      if (!idMap.has(item.id)) {
        idMap.set(item.id, item)
      }
    })

    // Convert back to array and sort by decision_time descending
    return Array.from(idMap.values()).sort((a, b) => {
      const timeA = a.decision_time ? new Date(a.decision_time).getTime() : 0
      const timeB = b.decision_time ? new Date(b.decision_time).getTime() : 0
      return timeB - timeA
    })
  }, [])

  const loadModelChatData = useCallback(async (isBackgroundRefresh: boolean = false) => {
    try {
      setLoadingModelChat(true)
      const { activeAccount, selectedSymbol, tradingMode, walletAddress, cacheKey } = filterRef.current
      const accountId = activeAccount === 'all' ? undefined : activeAccount
      const symbol = selectedSymbol || undefined
      const chatRes = await getArenaModelChat({
        limit: MODEL_CHAT_LIMIT,
        account_id: accountId,
        trading_mode: tradingMode,
        wallet_address: walletAddress,
        symbol: symbol,
      })
      const newModelChat = chatRes.entries || []

      // Read current value from ref to avoid dep on modelChat state
      const currentChat = modelChatRef.current

      // If this is a background refresh and user has loaded more history, merge instead of replace
      if (isBackgroundRefresh && currentChat.length > MODEL_CHAT_LIMIT) {
        // Merge new data with existing data, preserving user's loaded history
        const merged = mergeModelChatData(currentChat, newModelChat)
        setModelChat(merged)
        updateData(cacheKey, { modelChat: merged })
        // Keep hasMoreModelChat state unchanged during background refresh
      } else {
        // Initial load or manual refresh: replace all data
        setModelChat(newModelChat)
        updateData(cacheKey, { modelChat: newModelChat })
        // Reset lazy loading state when loading fresh data
        setHasMoreModelChat(newModelChat.length === MODEL_CHAT_LIMIT)
      }

      // Extract metadata from modelchat
      if (chatRes.entries && chatRes.entries.length > 0) {
        const metas = chatRes.entries.map(entry => ({
          account_id: entry.account_id,
          name: entry.account_name,
          model: entry.model ?? null,
        }))
        setAccountsMeta(prev => {
          const metaMap = new Map(prev.map(m => [m.account_id, m]))
          metas.forEach(m => metaMap.set(m.account_id, m))
          return Array.from(metaMap.values())
        })
      }

      setLoadingModelChat(false)
      return chatRes
    } catch (err) {
      console.error('[AlphaArenaFeed] Failed to load model chat:', err)
      setLoadingModelChat(false)
      return null
    }

  }, [updateData, mergeModelChatData])

  // Load more model chat entries (lazy loading)
  const loadMoreModelChat = useCallback(async () => {
    const currentChat = modelChatRef.current
    if (isLoadingMoreModelChat || !hasMoreModelChat || currentChat.length === 0) return

    try {
      setIsLoadingMoreModelChat(true)

      // Get the oldest decision_time from current list
      const oldestEntry = currentChat[currentChat.length - 1]
      const beforeTime = oldestEntry?.decision_time

      if (!beforeTime) {
        setHasMoreModelChat(false)
        setIsLoadingMoreModelChat(false)
        return
      }

      const { activeAccount, tradingMode, walletAddress, cacheKey } = filterRef.current
      const accountId = activeAccount === 'all' ? undefined : activeAccount
      const chatRes = await getArenaModelChat({
        limit: MODEL_CHAT_LIMIT,
        account_id: accountId,
        trading_mode: tradingMode,
        wallet_address: walletAddress,
        before_time: beforeTime,
      })

      const newEntries = chatRes.entries || []

      // Merge and deduplicate
      const merged = mergeModelChatData(currentChat, newEntries)
      setModelChat(merged)
      updateData(cacheKey, { modelChat: merged })

      // If we got fewer entries than requested, there's no more data
      setHasMoreModelChat(newEntries.length === MODEL_CHAT_LIMIT)

      setIsLoadingMoreModelChat(false)
    } catch (err) {
      console.error('[AlphaArenaFeed] Failed to load more model chat:', err)
      setIsLoadingMoreModelChat(false)
    }
  }, [updateData, hasMoreModelChat, isLoadingMoreModelChat, mergeModelChatData])

  const loadPositionsData = useCallback(async () => {
    try {
      setLoadingPositions(true)
      const { activeAccount, tradingMode, cacheKey } = filterRef.current
      const accountId = activeAccount === 'all' ? undefined : activeAccount
      const positionRes = await getArenaPositions({ account_id: accountId, trading_mode: tradingMode })
      const newPositions = positionRes.accounts || []
      setPositions(newPositions)
      updateData(cacheKey, { positions: newPositions })

      // Extract metadata from positions
      if (positionRes.accounts) {
        const metas = positionRes.accounts.map(account => ({
          account_id: account.account_id,
          name: account.account_name,
          model: account.model ?? null,
        }))
        setAccountsMeta(prev => {
          const metaMap = new Map(prev.map(m => [m.account_id, m]))
          metas.forEach(m => metaMap.set(m.account_id, m))
          return Array.from(metaMap.values())
        })
        updateData(cacheKey, { accountsMeta: Array.from(new Map(metas.map(m => [m.account_id, m])).values()) })
      }

      setLoadingPositions(false)
      return positionRes
    } catch (err) {
      console.error('[AlphaArenaFeed] Failed to load positions:', err)
      setLoadingPositions(false)
      return null
    }
  }, [updateData])

  // Lazy load data when tab becomes active
  useEffect(() => {
    const cached = getData(cacheKey)

    if (modelChat.length === 0 && !loadingModelChat) {
      if (cached?.modelChat && cached.modelChat.length > 0) {
        setModelChat(cached.modelChat)
      } else {
        loadModelChatData(false) // false = initial load, not background refresh
      }
    }
  }, [cacheKey])

  // Background polling - refresh all data regardless of active tab
  // Pauses when page is hidden in keep-alive mode
  // PERFORMANCE: useInterval keeps stable interval — callback updates via ref, no interval rebuild on filter changes
  const pollAllData = useCallback(async () => {
    await Promise.allSettled([
      loadTradesData(),
      loadModelChatData(true),
      loadPositionsData()
    ])
  }, [loadTradesData, loadModelChatData, loadPositionsData])

  useInterval(pollAllData, autoRefreshInterval > 0 && pageActive ? autoRefreshInterval : null)

  // Manual refresh trigger handler
  useEffect(() => {
    const shouldForce =
      manualRefreshKey !== prevManualRefreshKey.current ||
      refreshKey !== prevRefreshKey.current

    if (shouldForce) {
      prevManualRefreshKey.current = manualRefreshKey
      prevRefreshKey.current = refreshKey

      // Force refresh all data (manual refresh = full reload, not background refresh)
      Promise.allSettled([
        loadTradesData(),
        loadModelChatData(false), // false = full reload, reset to initial 60 entries
        loadPositionsData()
      ])
    }
  }, [manualRefreshKey, refreshKey, loadTradesData, loadModelChatData, loadPositionsData])

  // Reload data when account filter changes
  useEffect(() => {
    // Skip initial mount
    if (prevActiveAccount.current !== activeAccount) {
      prevActiveAccount.current = activeAccount

      // Reset lazy loading state when account changes
      setHasMoreModelChat(true)

      // Reload all data with new account filter (full reload, not background refresh)
      Promise.allSettled([
        loadTradesData(),
        loadModelChatData(false), // false = full reload when switching accounts
        loadPositionsData()
      ])
    }
  }, [activeAccount, loadTradesData, loadModelChatData, loadPositionsData])

  // Fetch watchlist symbols and filter by current positions
  useEffect(() => {
    const fetchWatchlist = async () => {
      try {
        // 只在 Hyperliquid 模式下获取监控列表
        if (tradingMode === 'testnet' || tradingMode === 'mainnet') {
          const response = await getHyperliquidWatchlist();
          const allSymbols = response.symbols || [];

          setSymbolOptions(allSymbols);
          if (selectedSymbol && !allSymbols.includes(selectedSymbol)) {
            setSelectedSymbol(null);
          }
        } else {
          // 币安模式下，从持仓中提取交易对
          const symbols = Array.from(new Set(positions.flatMap(acc => 
            acc.positions.map(pos => pos.symbol)
          )));
          setSymbolOptions(symbols);
          if (selectedSymbol && !symbols.includes(selectedSymbol)) {
            setSelectedSymbol(null);
          }
        }
      } catch (err) {
        console.error('Failed to fetch watchlist:', err);
        setSelectedSymbol(null);
      }
    };
    
    fetchWatchlist();
  }, [positions, activeAccount, tradingMode]);



  const accountOptions = useMemo(() => {
    return allTraderOptions.sort((a, b) => a.name.localeCompare(b.name))
  }, [allTraderOptions])

  const handleRefreshClick = () => {
    setManualRefreshKey((key) => key + 1)
  }

  const handleAccountFilterChange = (value: number | 'all') => {
    if (selectedAccountProp === undefined) {
      setInternalSelectedAccount(value)
    }
    onSelectedAccountChange?.(value)
    setExpandedChat(null)
    setExpandedSections({})

    // Data reload will be triggered by useEffect when activeAccount updates
  }

  const toggleSection = (entryId: number, section: 'prompt' | 'reasoning' | 'decision') => {
    const key = `${entryId}-${section}`
    setExpandedSections((prev) => ({
      ...prev,
      [key]: !prev[key],
    }))
  }

  const isSectionExpanded = (entryId: number, section: 'prompt' | 'reasoning' | 'decision') =>
    !!expandedSections[`${entryId}-${section}`]

  const handleCopySection = async (entryId: number, section: 'prompt' | 'reasoning' | 'decision', content: string) => {
    const key = `${entryId}-${section}`
    const success = await copyToClipboard(content)
    if (success) {
      setCopiedSections((prev) => ({ ...prev, [key]: true }))
      setTimeout(() => {
        setCopiedSections((prev) => ({ ...prev, [key]: false }))
      }, 2000)
    } else {
      console.error('Failed to copy')
    }
  }

  const isSectionCopied = (entryId: number, section: 'prompt' | 'reasoning' | 'decision') =>
    !!copiedSections[`${entryId}-${section}`]

  // Handle PnL data update
  const handleUpdatePnl = async () => {
    setUpdatingPnl(true)
    setPnlUpdateResult(null)
    try {
      const result = await updateArenaPnl()
      if (result.success) {
        // Calculate total updates across all environments
        let totalTrades = 0
        let totalDecisions = 0
        Object.values(result.environments).forEach((env) => {
          totalTrades += env.trades_updated
          totalDecisions += env.decisions_updated
        })
        setPnlUpdateResult(
          t('feed.pnlUpdateSuccess', 'Updated {{trades}} trades, {{decisions}} decisions', {
            trades: totalTrades,
            decisions: totalDecisions,
          })
        )
        // Refresh trades data to show updated values
        setManualRefreshKey((key) => key + 1)
      } else {
        setPnlUpdateResult(result.message || t('feed.pnlUpdateFailed', 'Update failed'))
      }
    } catch (err) {
      console.error('Failed to update PnL:', err)
      setPnlUpdateResult(t('feed.pnlUpdateError', 'Error updating PnL data'))
    } finally {
      setUpdatingPnl(false)
      // Clear result message after 5 seconds
      setTimeout(() => setPnlUpdateResult(null), 5000)
    }
  }

  // Load snapshots for a specific entry when expanded
  const loadSnapshots = useCallback(async (entryId: number) => {
    // Skip if already cached or loading
    if (snapshotCache.current.has(entryId) || loadingSnapshots.has(entryId)) {
      return
    }

    setLoadingSnapshots((prev) => new Set(prev).add(entryId))

    try {
      const snapshots = await getModelChatSnapshots(entryId)
      snapshotCache.current.set(entryId, snapshots)

      // Evict oldest entries if cache exceeds limit
      if (snapshotCache.current.size > MAX_SNAPSHOT_CACHE) {
        const keysToDelete = Array.from(snapshotCache.current.keys()).slice(0, snapshotCache.current.size - MAX_SNAPSHOT_CACHE)
        keysToDelete.forEach(k => snapshotCache.current.delete(k))
      }

      // Update the modelChat entry with snapshot data
      setModelChat((prev) =>
        prev.map((entry) =>
          entry.id === entryId
            ? {
                ...entry,
                prompt_snapshot: snapshots.prompt_snapshot,
                reasoning_snapshot: snapshots.reasoning_snapshot,
                decision_snapshot: snapshots.decision_snapshot,
              }
            : entry
        )
      )
    } catch (err) {
      console.error(`[AlphaArenaFeed] Failed to load snapshots for entry ${entryId}:`, err)
    } finally {
      setLoadingSnapshots((prev) => {
        const next = new Set(prev)
        next.delete(entryId)
        return next
      })
    }
  }, [loadingSnapshots])

  // Get snapshot data for an entry (from cache or entry itself)
  const getSnapshotData = useCallback((entry: ArenaModelChatEntry) => {
    const cached = snapshotCache.current.get(entry.id)
    return {
      prompt_snapshot: cached?.prompt_snapshot ?? entry.prompt_snapshot,
      reasoning_snapshot: cached?.reasoning_snapshot ?? entry.reasoning_snapshot,
      decision_snapshot: cached?.decision_snapshot ?? entry.decision_snapshot,
    }
  }, [])

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 mb-4">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{t('feed.filter', 'Filter')}</span>
          <select
            value={activeAccount === 'all' ? '' : activeAccount}
            onChange={(e) => {
              const value = e.target.value
              handleAccountFilterChange(value ? Number(value) : 'all')
            }}
            className="h-8 rounded border border-border bg-muted px-2 text-xs uppercase tracking-wide text-foreground"
          >
            <option value="">{t('feed.allTraders', 'All Traders')}</option>
            {accountOptions.map((meta) => (
              <option key={meta.account_id} value={meta.account_id}>
                {meta.name}{meta.model ? ` (${meta.model})` : ''}
              </option>
            ))}
          </select>
          <select
            value={selectedSymbol || ''}
            onChange={(e) => setSelectedSymbol(e.target.value || null)}
            className="h-8 rounded border border-border bg-muted px-2 text-xs uppercase tracking-wide text-foreground"
            disabled={symbolOptions.length === 0}
          >
            <option value="">{t('feed.allSymbols', 'All Symbols')}</option>
            {symbolOptions.map((sym) => (
              <option key={sym} value={sym}>
                {sym}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" className="h-7 text-xs" onClick={handleRefreshClick} disabled={loadingTrades || loadingModelChat || loadingPositions}>
            {t('common.refresh', 'Refresh')}
          </Button>
        </div>
      </div>

      <div className="flex-1 flex flex-col min-h-0">
        {/* Header - AI Decisions */}
        <div className="flex items-center justify-between border border-border bg-muted px-3 py-2 rounded-t-md">
          <span className="text-xs font-semibold uppercase tracking-wide text-foreground">
            {t('feed.aiDecisions', 'AI DECISIONS')}
          </span>
        </div>

        <div className="flex-1 border border-t-0 border-border bg-card min-h-0 flex flex-col overflow-hidden">
          {error && (
            <div className="p-4 text-sm text-red-500">
              {error}
            </div>
          )}

          {!error && (
            <>
              <div className="flex-1 h-0 overflow-y-auto mt-0 p-4 space-y-3" ref={modelChatScrollRef}>
                {loadingModelChat && modelChat.length === 0 ? (
                  <div className="text-xs text-muted-foreground">{t('feed.loadingModelChat', 'Loading model chat...')}</div>
                ) : modelChat.length === 0 ? (
                  <div className="text-xs text-muted-foreground">{t('feed.noModelChat', 'No recent AI commentary.')}</div>
                ) : (
                  <>
                  {modelChat.map((entry) => {
                    const isExpanded = expandedChat === entry.id
                    const modelLogo = getModelLogo(entry.account_name || entry.model)
                    const isNew = !seenDecisionIds.current.has(entry.id)
                    if (!seenDecisionIds.current.has(entry.id)) {
                      seenDecisionIds.current.add(entry.id)
                    }

                    return (
                      <HighlightWrapper key={entry.id} isNew={isNew}>
                        <button
                          type="button"
                          className="w-full text-left border border-border rounded bg-muted/30 p-4 space-y-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                          onClick={() =>
                            setExpandedChat((current) => {
                              const next = current === entry.id ? null : entry.id
                              if (current === entry.id) {
                                setExpandedSections((prev) => {
                                  const nextState = { ...prev }
                                  Object.keys(nextState).forEach((key) => {
                                    if (key.startsWith(`${entry.id}-`)) {
                                      delete nextState[key]
                                    }
                                  })
                                  return nextState
                                })
                                // Clear full view state for this entry
                                setFullViewSections((prev) => {
                                  const next = new Set(prev)
                                  next.forEach((k) => {
                                    if (k.startsWith(`${entry.id}-`)) next.delete(k)
                                  })
                                  return next
                                })
                              } else {
                                // Load snapshots when expanding
                                loadSnapshots(entry.id)
                              }
                              return next
                            })
                          }
                        >
                        <div className="flex flex-wrap items-center justify-between gap-2 text-xs uppercase tracking-wide text-muted-foreground">
                          <div className="flex items-center gap-2">
                            {modelLogo && (
                              <img
                                src={modelLogo.src}
                                alt={modelLogo.alt}
                                className="h-5 w-5 rounded-full object-contain bg-background"
                                loading="lazy"
                              />
                            )}
                            <span className="font-semibold text-foreground">{entry.account_name}</span>
                          </div>
                          <span>{formatDate(entry.decision_time)}</span>
                        </div>
                        <div className="text-sm font-medium text-foreground flex items-center gap-2">
                          <span className={`px-2 py-1 rounded text-xs font-bold ${
                            entry.operation?.toUpperCase() === 'BUY'
                              ? 'bg-emerald-100 text-emerald-800'
                              : entry.operation?.toUpperCase() === 'SELL'
                              ? 'bg-red-100 text-red-800'
                              : entry.operation?.toUpperCase() === 'CLOSE'
                              ? 'bg-blue-100 text-blue-800'
                              : entry.operation?.toUpperCase() === 'HOLD'
                              ? 'bg-gray-200 text-gray-800'
                              : 'bg-orange-100 text-orange-800'
                          }`}>
                            {(entry.operation || 'UNKNOWN').toUpperCase()}
                          </span>
                          {entry.symbol && (
                            <span className="font-semibold">{entry.symbol}</span>
                          )}
                          <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                            entry.signal_trigger_id
                              ? 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400'
                              : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400'
                          }`}>
                            {entry.signal_trigger_id
                              ? t('feed.signalPoolTrigger', 'Signal Pool')
                              : t('feed.scheduledTrigger', 'Scheduled')}
                          </span>
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {isExpanded ? entry.reason : `${entry.reason.slice(0, 160)}${entry.reason.length > 160 ? '…' : ''}`}
                        </div>
                        {renderTimeframeRow(entry)}
                        {isExpanded && (
                          <div className="space-y-2 pt-3">
                            {entry.prompt_template_name && (
                              <div className="flex items-center gap-2 text-xs text-muted-foreground pb-1">
                                <span className="font-medium">{t('feed.promptTemplate', 'Prompt Template')}:</span>
                                <span className="px-2 py-0.5 rounded bg-muted text-foreground font-medium">{entry.prompt_template_name}</span>
                              </div>
                            )}
                            {(() => {
                              const snapshots = getSnapshotData(entry)
                              const isLoadingEntry = loadingSnapshots.has(entry.id)
                              return [{
                                label: t('feed.userPrompt', 'USER PROMPT'),
                                section: 'prompt' as const,
                                content: snapshots.prompt_snapshot,
                                empty: t('feed.noPrompt', 'No prompt available'),
                              }, {
                                label: t('feed.chainOfThought', 'CHAIN OF THOUGHT'),
                                section: 'reasoning' as const,
                                content: snapshots.reasoning_snapshot,
                                empty: t('feed.noReasoning', 'No reasoning available'),
                              }, {
                                label: t('feed.tradingDecisions', 'TRADING DECISIONS'),
                                section: 'decision' as const,
                                content: snapshots.decision_snapshot,
                                empty: t('feed.noDecision', 'No decision payload available'),
                              }].map(({ label, section, content, empty }) => {
                              const open = isSectionExpanded(entry.id, section)
                              const displayContent = content?.trim()
                              // 截断超大内容防止浏览器卡死
                              const MAX_PREVIEW = 5000
                              const fullViewKey = `${entry.id}-${section}`
                              const isFullView = fullViewSections.has(fullViewKey)
                              const isOversized = (displayContent?.length ?? 0) > MAX_PREVIEW
                              const renderContent = isOversized && !isFullView ? displayContent!.slice(0, MAX_PREVIEW) : displayContent
                              const copied = isSectionCopied(entry.id, section)
                              const showLoading = isLoadingEntry && !displayContent
                              
                              return (
                                <div key={section} className="border border-border/60 rounded-md bg-background/60">
                                  <button
                                    type="button"
                                    className="flex w-full items-center justify-between px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground"
                                    onClick={(event) => {
                                      event.stopPropagation()
                                      toggleSection(entry.id, section)
                                    }}
                                  >
                                    <span className="flex items-center gap-2">
                                      <span className="text-xs">{open ? '▼' : '▶'}</span>
                                      {label}
                                    </span>
                                    <span className="text-[10px] text-muted-foreground/80">{open ? t('feed.hideDetails', 'Hide details') : t('feed.showDetails', 'Show details')}</span>
                                  </button>
                                  {open && (
                                    <div
                                      className="border-t border-border/40 bg-muted/40 px-3 py-3 text-xs text-muted-foreground"
                                      onClick={(event) => event.stopPropagation()}
                                    >
                                      {showLoading ? (
                                        <div className="flex items-center gap-2 text-muted-foreground/70">
                                          <Loader2 className="w-3 h-3 animate-spin" />
                                          <span>{t('feed.loading', 'Loading...')}</span>
                                        </div>
                                      ) : renderContent ? (
                                        <>
                                          {isOversized && !isFullView && (
                                            <div className="mb-2 px-2 py-1.5 rounded bg-amber-500/10 border border-amber-500/20 text-[10px] text-amber-600 dark:text-amber-400">
                                              内容已截断至 {MAX_PREVIEW.toLocaleString()} 字符，共 {(displayContent?.length ?? 0).toLocaleString()} 字符
                                            </div>
                                          )}
                                          <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-foreground/90">
                                            {renderContent}
                                          </pre>
                                          <div className="mt-3 flex justify-end gap-2">
                                            {isOversized && (
                                              <button
                                                type="button"
                                                onClick={(e) => {
                                                  e.stopPropagation()
                                                  setFullViewSections((prev) => {
                                                    const next = new Set(prev)
                                                    if (next.has(fullViewKey)) {
                                                      next.delete(fullViewKey)
                                                    } else {
                                                      next.add(fullViewKey)
                                                    }
                                                    return next
                                                  })
                                                }}
                                                className="px-3 py-1.5 text-[10px] font-medium rounded transition-all bg-muted/60 text-muted-foreground hover:bg-muted hover:text-foreground border border-border/60"
                                              >
                                                {isFullView ? '收起' : '显示全部'}
                                              </button>
                                            )}
                                            <button
                                              type="button"
                                              onClick={(e) => {
                                                e.stopPropagation()
                                                if (renderContent) {
                                                  handleCopySection(entry.id, section, renderContent)
                                                }
                                              }}
                                              className={`px-3 py-1.5 text-[10px] font-medium rounded transition-all ${
                                                copied
                                                  ? 'bg-emerald-500/20 text-emerald-600 border border-emerald-500/30'
                                                  : 'bg-muted/60 text-muted-foreground hover:bg-muted hover:text-foreground border border-border/60'
                                              }`}
                                            >
                                              {copied ? `✓ ${t('feed.copied', 'Copied')}` : t('feed.copy', 'Copy')}
                                            </button>
                                          </div>
                                        </>
                                      ) : (
                                        <span className="text-muted-foreground/70">{empty}</span>
                                      )}
                                    </div>
                                  )}
                                </div>
                              )
                            })
                            })()}
                          </div>
                        )}
                        <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground uppercase tracking-wide">
                          <span>{t('feed.prevPortion', 'Prev Portion')}: <span className="font-semibold text-foreground">{(entry.prev_portion * 100).toFixed(1)}%</span></span>
                          <span>{t('feed.targetPortion', 'Target Portion')}: <span className="font-semibold text-foreground">{(entry.target_portion * 100).toFixed(1)}%</span></span>
                          <span>{t('feed.totalBalance', 'Total Balance')}: <span className="font-semibold text-foreground">
                            <FlipNumber value={entry.total_balance} prefix="$" decimals={2} />
                          </span></span>
                          <span>{t('feed.executed', 'Executed')}: <span className={`font-semibold ${entry.executed ? 'text-emerald-600' : 'text-amber-600'}`}>{entry.executed ? 'YES' : 'NO'}</span></span>
                        </div>
                        <div className="mt-2 text-[11px] text-primary underline">
                          {isExpanded ? t('feed.clickCollapse', 'Click to collapse') : t('feed.clickExpand', 'Click to expand')}
                        </div>
                        </button>
                      </HighlightWrapper>
                    )
                  })}

                  {/* Load More Button */}
                  {hasMoreModelChat && (
                    <div className="flex justify-center pt-4">
                      <Button
                        onClick={loadMoreModelChat}
                        disabled={isLoadingMoreModelChat}
                        variant="outline"
                        size="sm"
                        className="text-xs"
                      >
                        {isLoadingMoreModelChat ? (
                          <>
                            <Loader2 className="w-3 h-3 mr-2 animate-spin" />
                            {t('feed.loading', 'Loading...')}
                          </>
                        ) : (
                          t('feed.loadMore', 'Load More History')
                        )}
                      </Button>
                    </div>
                  )}

                  {!hasMoreModelChat && modelChat.length > 0 && (
                    <div className="flex justify-center pt-4 text-xs text-muted-foreground">
                      {t('feed.allLoaded', 'All history loaded')}
                    </div>
                  )}
                  </>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
