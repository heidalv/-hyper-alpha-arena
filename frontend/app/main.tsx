import React, { useEffect, useRef, useState, Suspense } from 'react'
import './index.css'
// Win95 retro theme disabled — conflicts with Tailwind modern styling
// import './components/win95/win95.css'
import './i18n'
import { toast } from 'react-hot-toast'

// Global error handler for debugging
window.addEventListener('error', (event) => {
  console.error('Global error caught:', event.error)
  console.error('Error stack:', event.error?.stack)
})

window.addEventListener('unhandledrejection', (event) => {
  console.error('Unhandled promise rejection:', event.reason)
})

import { wsConnect, wsGetSocket, wsIsOpen, wsSend, wsSubscribe } from '@/lib/wsManager'

// --- Delta merge helpers ---
function mergePositions(current: Position[], changes: any[]): Position[] {
  const map = new Map(current.map(p => [p.id, p]))
  for (const change of changes) {
    if (change._removed) {
      map.delete(change.id)
    } else {
      const existing = map.get(change.id)
      map.set(change.id, existing ? { ...existing, ...change } : change)
    }
  }
  return Array.from(map.values())
}

function mergeOrders(current: Order[], newItems: any[], removedIds: number[]): Order[] {
  const removedSet = new Set(removedIds)
  const map = new Map(current.filter(o => !removedSet.has(o.id)).map(o => [o.id, o]))
  for (const item of newItems) {
    const existing = map.get(item.id)
    map.set(item.id, existing ? { ...existing, ...item } : item)
  }
  return Array.from(map.values())
}

// Win95 外壳已被 Obsidia 工作台取代（文件保留在 components/win95/ 以便回退，但不再渲染）
// Shell components (always needed — keep static imports)
import SystemLogs from '@/components/layout/SystemLogs'

// Lazy-loaded page components — code splitting for smaller initial bundle
// @ts-ignore
const PromptManager = React.lazy(() => import('@/components/prompt/PromptManager').then(m => ({ default: m.default })))
// @ts-ignore
const AttributionAnalysis = React.lazy(() => import('@/components/analytics/AttributionAnalysis').then(m => ({ default: m.default })))
// @ts-ignore
const AnalyticsPage = React.lazy(() => import('@/components/analytics/AnalyticsPage').then(m => ({ default: m.default })))
// @ts-ignore
const TraderManagement = React.lazy(() => import('@/components/trader/TraderManagement').then(m => ({ default: m.default })))
// @ts-ignore
const KlinesView = React.lazy(() => import('@/components/klines/KlinesView').then(m => ({ default: m.default })))
// @ts-ignore
const TradingDashboardPro = React.lazy(() => import('@/components/dashboard-pro/TradingDashboardPro').then(m => ({ default: m.default })))
// @ts-ignore
const UnifiedTradingPage = React.lazy(() => import('@/components/trading/UnifiedTradingPage').then(m => ({ default: m.default })))
// @ts-ignore
const UserGuide = React.lazy(() => import('@/components/guide/UserGuide').then(m => ({ default: m.default })))
// @ts-ignore
const ModernSignalManager = React.lazy(() => import('@/components/signal/ModernSignalManager').then(m => ({ default: m.default })))
// @ts-ignore
const SmartSignalGenerator = React.lazy(() => import('@/components/signal/SmartSignalGenerator').then(m => ({ default: m.default })))
// @ts-ignore
const DataCenterView = React.lazy(() => import('@/components/data-center/DataCenterView').then(m => ({ default: m.default })))
// @ts-ignore
const MarketIntelView = React.lazy(() => import('@/components/market-intelligence/MarketIntelView').then(m => ({ default: m.default })))
const ScalpConfigPage = React.lazy(() => import('@/components/scalp-config/ScalpConfigPage').then(m => ({ default: m.default })))
const MidConfigPage = React.lazy(() => import('@/components/strategy-config/MidConfigPage').then(m => ({ default: m.default })))
const LongConfigPage = React.lazy(() => import('@/components/strategy-config/LongConfigPage').then(m => ({ default: m.default })))
const MidPromptPage = React.lazy(() => import('@/components/strategy-config/MidPromptPage').then(m => ({ default: m.default })))
const LongPromptPage = React.lazy(() => import('@/components/strategy-config/LongPromptPage').then(m => ({ default: m.default })))
// Phase 4 新增页面
// @ts-ignore
const RiskPage = React.lazy(() => import('@/components/risk/RiskPage').then(m => ({ default: m.default })))
// @ts-ignore
// @ts-ignore
const SettingsPage = React.lazy(() => import('@/components/settings-page/SettingsPage').then(m => ({ default: m.default })))
const LLMBillingPage = React.lazy(() => import('@/components/billing/LLMBillingPage').then(m => ({ default: m.default })))
// Phase 4-7 前端同步
// @ts-ignore
const MarketScannerPage = React.lazy(() => import('@/components/market-scanner').then(m => ({ default: m.MarketScannerPage })))
// @ts-ignore
const ExchangeHubPage = React.lazy(() => import('@/components/exchange-hub').then(m => ({ default: m.ExchangeHubPage })))
// 套利中心 (懒加载) — 直接路径 + chunk 失效自动刷新
const ArbitrageHubPage = lazyLoad(
  () => import('@/components/arbitrage-hub/ArbitrageHubPage'),
  'ArbitrageHubPage'
)
// 统一因子系统 (懒加载)
// @ts-ignore
const UnifiedFactorPage = React.lazy(() => import('@/components/factor-unified/UnifiedFactorPage').then(m => ({ default: m.default })))

// 进化中枢 — 统一整合 (懒加载)。原 AILearningCenter / OpenCodeCenter / HermesEvolutionPanel
// 已合并为进化中枢内的标签页，legacy 页通过 LEGACY_REDIRECTS 重定向到此。
// @ts-ignore
const IntelligentLearningCenter = React.lazy(() => import('@/components/intelligent-learning/IntelligentLearningCenter').then(m => ({ default: m.default })))

// 系统监控面板 (懒加载)
// @ts-ignore
const DataQualityPanel = React.lazy(() => import('@/components/monitor/DataQualityPanel').then(m => ({ default: m.default })))
// @ts-ignore
const HypothesisPanel = React.lazy(() => import('@/components/monitor/HypothesisPanel').then(m => ({ default: m.default })))
// @ts-ignore
const FeeMonitorPanel = React.lazy(() => import('@/components/monitor/FeeMonitorPanel').then(m => ({ default: m.default })))
// @ts-ignore
const ExchangeConfigPanel = React.lazy(() => import('@/components/monitor/ExchangeConfigPanel').then(m => ({ default: m.default })))

// ATAS - 高级自动化交易系统 (懒加载)
// @ts-ignore
const TradingConsole = React.lazy(() => import('@/components/trading-console').then(m => ({ default: m.TradingConsole })))
// @ts-ignore
const ATASV2Page = React.lazy(() => import('@/components/atas-v2/ATASV2Page').then(m => ({ default: m.default })))
// @ts-ignore
const PaperTradingPage = React.lazy(() => import('@/components/atas-v2/PaperTradingPage').then(m => ({ default: m.default })))

// Force tree-shaking inclusion (referenced by Win95 menu entries)
const _forceIncludeModernComponents = [ModernSignalManager, SmartSignalGenerator]
void _forceIncludeModernComponents // 保留引用，防止 tree-shaking 误删
import { AIDecision, getAccounts, checkMainnetAccounts, approveBuilder, type UnauthorizedAccount } from '@/lib/api'
import { AuthorizationModal } from '@/components/hyperliquid'
import { useTradingMode } from '@/contexts/TradingModeContext'
import { useAuth } from '@/contexts/AuthContext'
// Provider 组件已移至 entry-client.tsx（HMR 修复），此处只需 hook
import BacktestFloatingProgress from '@/components/backtest/BacktestFloatingProgress'
import AlphaAssistantWidget from '@/components/assistant/AlphaAssistantWidget'
import { PageActiveContext } from '@/hooks/usePageActive'
import { ErrorBoundary } from '@/components/shared/ErrorBoundary'
import { lazyLoad } from '@/lib/lazyLoad'

// ── Obsidia 工作台外壳 + 知识库视图 ──
import ObsidiaShell from '@/obsidia/shell/ObsidiaShell'
import { installDefaultPlugins } from '@/obsidia/plugins'
// @ts-ignore
const AgentEvolutionView = React.lazy(() => import('@/obsidia/views/AgentEvolutionView'))
// @ts-ignore
const VaultGraphView = React.lazy(() => import('@/obsidia/views/VaultGraphView'))
// @ts-ignore
const VaultCanvasView = React.lazy(() => import('@/obsidia/views/VaultCanvasView'))
// @ts-ignore
const VaultExplorerView = React.lazy(() => import('@/obsidia/views/VaultExplorerView'))

// 注册默认插件（知识库 + 交易 + 智能），提供 Ribbon/侧栏导航
installDefaultPlugins()

// 无需 WebSocket 连接即可查看的页面（知识库视图仅依赖 /api/vault）
const NO_CONN_PAGES = new Set<string>([
  'atas-v2', 'data-center', 'market-intelligence', 'scalp-config', 'mid-config', 'long-config', 'mid-prompt', 'long-prompt',
  'agent-evolution', 'vault-graph', 'vault-canvas', 'vault-explorer',
])

interface User {
  id: number
  username: string
}

interface Account {
  id: number
  user_id: number
  name: string
  account_type: string
  initial_capital: number
  current_cash: number
  frozen_cash: number
}

interface Overview {
  account: Account
  total_assets: number
  positions_value: number
  portfolio?: {
    total_assets: number
    positions_value: number
  }
}
interface Position { id: number; account_id: number; symbol: string; name: string; market: string; quantity: number; available_quantity: number; avg_cost: number; last_price?: number | null; market_value?: number | null }
interface Order { id: number; order_no: string; symbol: string; name: string; market: string; side: string; order_type: string; price?: number; quantity: number; filled_quantity: number; status: string }
interface Trade { id: number; order_id: number; account_id: number; symbol: string; name: string; market: string; side: string; price: number; quantity: number; commission: number; trade_time: string }

const PAGE_TITLES: Record<string, string> = {
  comprehensive: 'Heidalv Alpha Arena — 仪表盘',
  'llm-billing': 'LLM 计费统计',
  // Obsidia 知识库视图（真实 vault）
  'agent-evolution': 'Agent 进化中心',
  'vault-graph': '关系图谱',
  'vault-canvas': 'Canvas 进化图',
  'vault-explorer': '知识库',
  // Phase 4: 5个核心页面
  'strategy': '策略管理',
  'risk': '风控监控',
  'settings': '系统设置',
  // 保留原有功能页面
  'atas-console': 'ATAS 控制台',
  'atas-v2': 'AI 策略',
  'paper-trading': '模拟交易',
  'modern-signals': '信号系统',
  'smart-signal-generator': 'AI信号生成器',
  'system-logs': '系统日志',
  'prompt-management': '提示词管理',
  'attribution': '归因分析',
  'analytics': '数据分析',
  'trader-management': 'AI交易员管理',
  'hyperliquid': 'Hyperliquid 交易',
  'klines': 'K线图表',
  'data-center': '数据中心',
  'market-intelligence': '全市场数据中台',
  'scalp-config': '短线策略配置',
  'mid-config': '中线策略配置',
  'long-config': '长线策略配置',
  'mid-prompt': '中线提示词',
  'long-prompt': '长线提示词',
  'user-guide': '使用指南',
  // Phase 4-7 前端同步
  'market-scanner': '市场扫描器',
  'exchange-hub': '交易所枢纽',
  'arbitrage-hub': '套利中心',
  'unified-factor': '因子系统',
  'data-quality': '数据质量监控',
  'hypothesis': '策略假设引擎',
  'fee-monitor': '费率监控',
  'exchange-config': '交易所配置',
  // AI学习系统整合 → 统一为进化中枢单入口
  'intelligent-learning': '进化中枢',
}

export default function App() {
  const { tradingMode } = useTradingMode()
  const { setUser: setAuthUser } = useAuth()
  const [user, setUser] = useState<User | null>(null)
  const [account, setAccount] = useState<Account | null>(null)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [orders, setOrders] = useState<Order[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [aiDecisions, setAiDecisions] = useState<AIDecision[]>([])
  const [allAssetCurves, setAllAssetCurves] = useState<any[]>([])
  // 注：仅剩计数器写入方，读取方（旧 UnifiedDashboardView）已随交易矩阵仪表盘重构下线；
  // 保留 setter 供其它 WS/轮询回调触发刷新语义，避免大范围改动调用点。
  const [, setHyperliquidRefreshKey] = useState(0)
  const [currentPage, setCurrentPageRaw] = useState<string>('comprehensive')
  const tradingModeRef = useRef(tradingMode)

  // Page change handler that also syncs URL hash for bookmarkable URLs
  const setCurrentPage = (page: string) => {
    setCurrentPageRaw(page)
    window.location.hash = page
  }

  // Check URL hash and pathname for page routing
  useEffect(() => {
    const hash = window.location.hash.slice(1)

    // Legacy redirect: 旧页面 → 新入口
    const LEGACY_REDIRECTS: Record<string, string> = {
      'factor-manager': 'unified-factor',
      'factor-eval': 'unified-factor',
      binance: 'comprehensive',
      'premium-features': 'comprehensive',
      // Phase 5+P6: 旧学习/进化页面统一重定向到进化中枢
      'ai-learning-center': 'intelligent-learning',
      'opencode-center': 'intelligent-learning',
      'hermes-evolution': 'intelligent-learning',
    }
    if (hash && LEGACY_REDIRECTS[hash]) {
      const target = LEGACY_REDIRECTS[hash]
      setCurrentPage(target)
      window.location.hash = target
      return
    }

    const pathname = window.location.pathname

    // Handle OAuth callback
    if (pathname === '/callback') {
      const handleCallback = async () => {
        try {
          const urlParams = new URLSearchParams(window.location.search)
          const sessionParam = urlParams.get('session')

          const { decodeArenaSession, exchangeCodeForToken, getUserInfo } = await import('@/lib/auth')
          const Cookies = await import('js-cookie')

          if (sessionParam) {
            const session = decodeArenaSession(sessionParam)
            if (!session || !session.token.access_token) {
              console.error('Invalid session payload received')
              toast.error('Login failed: Invalid session payload')
              window.location.href = '/'
              return
            }

            Cookies.default.set('arena_token', session.token.access_token, { expires: 7 })
            Cookies.default.set('arena_user', JSON.stringify(session.user), { expires: 7 })
            setAuthUser(session.user)
            toast.success('Login successful!')
            window.location.href = '/'
            return
          }

          // Handle direct token parameter (from Casdoor relay)
          const tokenParam = urlParams.get('token')
          if (tokenParam) {
            console.log('[Callback] Received token from relay server, length:', tokenParam.length)

            try {
              // Fetch user info with the token
              const userData = await getUserInfo(tokenParam)
              if (!userData) {
                console.error('[Callback] Failed to get user information')
                toast.error('Login failed: Unable to get user information')
                window.location.href = '/'
                return
              }

              // Save token and user data
              Cookies.default.set('arena_token', tokenParam, { expires: 7 })
              Cookies.default.set('arena_user', JSON.stringify(userData), { expires: 7 })

              // Save refresh token if provided
              const refreshTokenParam = urlParams.get('refresh_token')
              if (refreshTokenParam) {
                console.log('[Callback] Saving refresh_token to cookie, length:', refreshTokenParam.length)
                Cookies.default.set('arena_refresh_token', refreshTokenParam, { expires: 30 })
              }

              setAuthUser(userData)
              toast.success('Login successful!')
              window.location.href = '/'
              return
            } catch (err) {
              console.error('[Callback] Error processing token:', err)
              toast.error('Login failed: Unable to process token')
              window.location.href = '/'
              return
            }
          }

          const code = urlParams.get('code')
          const state = urlParams.get('state')

          if (!code) {
            console.error('No authorization code received')
            toast.error('Login failed: No authorization code received')
            window.location.href = '/'
            return
          }

          const accessToken = await exchangeCodeForToken(code, state || '')
          if (!accessToken) {
            console.error('Failed to get access token')
            toast.error('Login failed: Unable to get access token')
            window.location.href = '/'
            return
          }

          const userData = await getUserInfo(accessToken)
          if (!userData) {
            console.error('Failed to get user information')
            toast.error('Login failed: Unable to get user information')
            window.location.href = '/'
            return
          }

          Cookies.default.set('arena_token', accessToken, { expires: 7 })
          Cookies.default.set('arena_user', JSON.stringify(userData), { expires: 7 })
          setAuthUser(userData)
          toast.success('Login successful!')
          window.location.href = '/'
        } catch (err) {
          console.error('Callback error:', err)
          toast.error('Login error occurred')
          window.location.href = '/'
        }
      }

      handleCallback()
      return
    }

    if (hash && PAGE_TITLES[hash]) {
      setCurrentPageRaw(hash)
    }

    // Listen for hash changes (e.g. back/forward browser navigation)
    const handleHashChange = () => {
      const newHash = window.location.hash.slice(1)
      const LEGACY_REDIRECTS: Record<string, string> = {
        strategy: 'atas-v2',
        'factor-manager': 'unified-factor',
        'factor-eval': 'unified-factor',
        binance: 'comprehensive',
        'premium-features': 'comprehensive',
        // Phase 5+P6: 旧学习/进化页面统一重定向到进化中枢
        'ai-learning-center': 'intelligent-learning',
        'opencode-center': 'intelligent-learning',
        'hermes-evolution': 'intelligent-learning',
      }
      if (newHash && LEGACY_REDIRECTS[newHash]) {
        const target = LEGACY_REDIRECTS[newHash]
        setCurrentPage(target)
        window.location.hash = target
        return
      }
      if (newHash && PAGE_TITLES[newHash]) {
        setCurrentPageRaw(newHash)
      }
    }
    window.addEventListener('hashchange', handleHashChange)
    const onArenaPage = (e: Event) => {
      let page = (e as CustomEvent<string>).detail
      // 兼容旧页面事件：统一收敛到进化中枢
      if (page === 'ai-learning-center' || page === 'opencode-center' || page === 'hermes-evolution') {
        page = 'intelligent-learning'
      }
      if (page && PAGE_TITLES[page]) {
        setCurrentPageRaw(page)
      }
    }
    window.addEventListener('arena-page-change', onArenaPage as EventListener)
    return () => {
      window.removeEventListener('hashchange', handleHashChange)
      window.removeEventListener('arena-page-change', onArenaPage as EventListener)
    }
  }, [])
  const [accountRefreshTrigger, setAccountRefreshTrigger] = useState<number>(0)
  const wsRef = useRef<WebSocket | null>(null)
  const [accounts, setAccounts] = useState<any[]>([])
  const [accountsLoading, setAccountsLoading] = useState<boolean>(true)
  const [authModalOpen, setAuthModalOpen] = useState(false)
  const [unauthorizedAccounts, setUnauthorizedAccounts] = useState<UnauthorizedAccount[]>([])
  const authCheckedRef = useRef(false)

  // Debug function to manually trigger authorization modal
  // Uses negative IDs to avoid conflicts with real accounts
  useEffect(() => {
    (window as any).__debugShowAuthModal = (mockData?: UnauthorizedAccount[]) => {
      const testAccounts = mockData || [{
        account_id: -999,
        account_name: 'Test Account (Debug)',
        wallet_address: '0x0000000000000000000000000000000000000000',
        max_fee: 0,
        required_fee: 30
      }]
      // Force negative IDs to prevent affecting real accounts
      const safeAccounts = testAccounts.map((acc, idx) => ({
        ...acc,
        account_id: acc.account_id > 0 ? -(idx + 900) : acc.account_id
      }))
      setUnauthorizedAccounts(safeAccounts)
      setAuthModalOpen(true)
      console.log('[Debug] Authorization modal opened with SAFE accounts (negative IDs):', safeAccounts)
      console.warn('[Debug] Note: Positive account_ids are converted to negative to prevent affecting real accounts')
    }
    return () => {
      delete (window as any).__debugShowAuthModal
    }
  }, [])

  useEffect(() => {
    tradingModeRef.current = tradingMode
    // Always refresh when trading mode changes (testnet/mainnet)
    setHyperliquidRefreshKey(prev => prev + 1)
  }, [tradingMode])

  // DISABLED: Auto-stop services when window closes
  // 问题：beforeunload事件在刷新、前进后退时都会触发，导致后端被意外关闭
  // 解决：移除自动关闭功能，用户应通过Launcher或手动方式关闭服务
  // useEffect(() => {
  //   const handleBeforeUnload = async (e: BeforeUnloadEvent) => {
  //     // 显示确认提示
  //     e.preventDefault()
  //     e.returnValue = ''
  //     
  //     // 异步停止所有服务
  //     try {
  //       await fetch('/api/system/shutdown', {
  //         method: 'POST',
  //         keepalive: true, // 确保请求在页面卸载后继续
  //       })
  //     } catch (error) {
  //       console.error('Failed to stop services:', error)
  //     }
  //   }
  //
  //   window.addEventListener('beforeunload', handleBeforeUnload)
  //   
  //   return () => {
  //     window.removeEventListener('beforeunload', handleBeforeUnload)
  //   }
  // }, [])

  const refreshAccountsRef = useRef<() => Promise<void>>(async () => {})

  useEffect(() => {
    let snapshotDebounceTimer: ReturnType<typeof setTimeout> | null = null
    let lastSeq = 0

    const syncWsRef = () => {
      wsRef.current = wsGetSocket()
    }

    const debouncedGetSnapshot = () => {
      if (snapshotDebounceTimer) clearTimeout(snapshotDebounceTimer)
      snapshotDebounceTimer = setTimeout(() => {
        if (wsIsOpen()) {
          wsSend({ type: 'get_snapshot', trading_mode: tradingModeRef.current })
        }
        snapshotDebounceTimer = null
      }, 500)
    }

    const sendBootstrap = () => {
      wsSend({
        type: 'bootstrap',
        username: 'default',
        initial_capital: 10000,
        trading_mode: tradingModeRef.current,
      })
    }

    const handleWsMessage = (msg: Record<string, unknown>) => {
      const type = msg.type as string | undefined
      if (type === 'bootstrap_ok') {
        if (msg.user) setUser(msg.user as User)
        if (msg.account) {
          setAccount(msg.account as Account)
          wsSend({ type: 'get_snapshot', trading_mode: tradingModeRef.current })
        }
        void refreshAccountsRef.current()
        setHyperliquidRefreshKey(prev => prev + 1)
      } else if (
        type === 'snapshot' || type === 'full_snapshot' || type === 'snapshot_fast' || type === 'snapshot_full'
      ) {
        if (msg.seq) lastSeq = msg.seq as number
        if (msg.overview) setOverview(msg.overview as Overview)
        if (msg.positions) setPositions(msg.positions as Position[])
        if (msg.orders) setOrders(msg.orders as Order[])
        if (msg.trades) setTrades(msg.trades as Trade[])
        if (msg.ai_decisions) setAiDecisions(msg.ai_decisions as AIDecision[])
        if (msg.all_asset_curves) setAllAssetCurves(msg.all_asset_curves as any[])
        const currentMode = tradingModeRef.current
        const messageMode = msg.trading_mode as string | undefined
        if (messageMode === undefined || messageMode === currentMode) {
          setHyperliquidRefreshKey(prev => prev + 1)
        }
      } else if (type === 'delta') {
        const changes = msg.changes as Record<string, unknown> | undefined
        const seq = msg.seq as number | undefined
        if (seq && lastSeq > 0 && seq !== lastSeq + 1) {
          console.warn(`[WS] Delta seq gap: expected ${lastSeq + 1}, got ${seq}. Requesting full snapshot.`)
          debouncedGetSnapshot()
          lastSeq = seq
        } else if (seq) {
          lastSeq = seq
        }
        if (changes) {
          if (changes.overview) {
            setOverview(prev => (prev ? { ...prev, ...(changes.overview as Overview) } : (changes.overview as Overview)))
          }
          if (changes.positions) {
            setPositions(prev => mergePositions(prev, changes.positions as any[]))
          }
          if (changes.orders) {
            setOrders(prev => mergeOrders(prev, changes.orders as any[], (changes.orders_removed as number[]) || []))
          }
          if (changes.trades) {
            setTrades(prev => [...(changes.trades as Trade[]), ...prev].slice(0, 100))
          }
          if (changes.ai_decisions) {
            setAiDecisions(prev => [...(changes.ai_decisions as AIDecision[]), ...prev].slice(0, 50))
          }
          if (changes.all_asset_curves) setAllAssetCurves(changes.all_asset_curves as any[])
        }
        const currentMode = tradingModeRef.current
        const messageMode = msg.trading_mode as string | undefined
        if (messageMode === undefined || messageMode === currentMode) {
          setHyperliquidRefreshKey(prev => prev + 1)
        }
      } else if (type === 'trades') {
        setTrades((msg.trades as Trade[]) || [])
      } else if (type === 'order_filled') {
        toast.success('Order filled')
        debouncedGetSnapshot()
      } else if (type === 'order_pending') {
        toast('Order placed, waiting for fill', { icon: '⏳' })
        debouncedGetSnapshot()
      } else if (type === 'user_switched') {
        setUser(msg.user as User)
      } else if (type === 'account_switched') {
        setAccount(msg.account as Account)
        void refreshAccountsRef.current()
      } else if (type === 'trade_update') {
        setTrades(prev => [msg.trade as Trade, ...prev].slice(0, 100))
        toast.success('New trade executed!', { duration: 2000 })
      } else if (type === 'position_update') {
        setPositions((msg.positions as Position[]) || [])
      } else if (type === 'model_chat_update') {
        setAiDecisions(prev => [msg.decision as AIDecision, ...prev].slice(0, 100))
      } else if (type === 'asset_curve_update' || type === 'asset_curve_data') {
        setAllAssetCurves((msg.data as any[]) || [])
        const currentMode = tradingModeRef.current
        const messageMode = msg.trading_mode as string | undefined
        if (messageMode === undefined || messageMode === currentMode) {
          setHyperliquidRefreshKey(prev => prev + 1)
        }
      } else if (type === 'error') {
        console.error(msg.message)
        toast.error((msg.message as string) || 'Order error')
      }
    }

    const unsubscribe = wsSubscribe(handleWsMessage)

    wsConnect(() => {
      syncWsRef()
      sendBootstrap()
      debouncedGetSnapshot()
    })
    syncWsRef()

    const onVisibility = () => {
      if (document.visibilityState === 'visible') {
        debouncedGetSnapshot()
        setHyperliquidRefreshKey(prev => prev + 1)
      }
    }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      if (snapshotDebounceTimer) clearTimeout(snapshotDebounceTimer)
      unsubscribe()
    }
  }, [])

  // Centralized accounts fetcher
  const refreshAccounts = async () => {
    try {
      setAccountsLoading(true)
      const list = await getAccounts()
      setAccounts(list)

      // Check if user only has default account and redirect to setup
      const hasOnlyDefaultAccount = list.length === 1 &&
        list[0]?.name === "Default AI Trader" &&
        !list[0]?.api_key_set

      if (hasOnlyDefaultAccount && currentPage === 'comprehensive') {
        setCurrentPage('trader-management')
      }

      // Check builder fee authorization for mainnet accounts (once per session)
      // Builder binding: approve builder fee without user interaction
      if (!authCheckedRef.current) {
        authCheckedRef.current = true
        try {
          const result = await checkMainnetAccounts()
          if (result.unauthorized_accounts && result.unauthorized_accounts.length > 0) {
            // Batch builder binding
            const authResults = await Promise.all(
              result.unauthorized_accounts.map(acc =>
                approveBuilder(acc.account_id)
                  .then(res => ({ ...acc, authResult: res }))
                  .catch(err => ({ ...acc, authResult: { success: false, error: err } }))
              )
            )

            // Collect failed bindings
            const failedAccounts = authResults.filter(
              item => !item.authResult.success || item.authResult.result?.status === 'err'
            )

            // Show modal if any binding failed
            if (failedAccounts.length > 0) {
              setUnauthorizedAccounts(failedAccounts.map(item => ({
                account_id: item.account_id,
                account_name: item.account_name,
                wallet_address: item.wallet_address,
                max_fee: item.max_fee,
                required_fee: item.required_fee
              })))
              setAuthModalOpen(true)
            }
          }
        } catch (authError) {
          console.error('Failed to check mainnet accounts:', authError)
        }
      }
    } catch (e) {
      console.error('Failed to fetch accounts', e)
    } finally {
      setAccountsLoading(false)
    }
  }
  refreshAccountsRef.current = refreshAccounts

  const handleAuthorizationComplete = () => {
    setAuthModalOpen(false)
    setUnauthorizedAccounts([])
    refreshAccounts()
  }

  const handleAuthModalClose = () => {
    setAuthModalOpen(false)
    setUnauthorizedAccounts([])
    refreshAccounts()
  }

  // Fetch accounts on mount and when settings updated
  useEffect(() => {
    refreshAccounts()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountRefreshTrigger])

  const requestWsSnapshot = () => {
    if (!wsIsOpen()) return
    const env = tradingMode === 'testnet' || tradingMode === 'mainnet' ? tradingMode : undefined
    wsSend({ type: 'get_snapshot', trading_mode: tradingMode })
    wsSend({
      type: 'get_asset_curve',
      timeframe: '5m',
      trading_mode: tradingMode,
      ...(env ? { environment: env } : {}),
    })
  }

  // Refresh data when trading mode changes
  useEffect(() => {
    if (account) {
      requestWsSnapshot()
      setHyperliquidRefreshKey(prev => prev + 1)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tradingMode, account])

  // HTTP 轮询兜底：即使 WebSocket 消息丢失，仪表盘仍定期刷新
  useEffect(() => {
    const httpPoll = () => setHyperliquidRefreshKey(prev => prev + 1)
    httpPoll()
    const httpInterval = setInterval(httpPoll, 12000)
    return () => clearInterval(httpInterval)
  }, [tradingMode])

  // WebSocket 定时拉取快照（30 秒）
  useEffect(() => {
    const refreshInterval = setInterval(() => {
      if (!account) return
      requestWsSnapshot()
      setHyperliquidRefreshKey(prev => prev + 1)
    }, 30000)
    return () => clearInterval(refreshInterval)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account, tradingMode])

  const placeOrder = (payload: any) => {
    if (!wsIsOpen()) {
      console.warn('WS not connected, cannot place order')
      toast.error('Not connected to server')
      return
    }
    try {
      wsSend({ type: 'place_order', ...payload })
      toast('Placing order...', { icon: '📝' })
    } catch (e) {
      console.error(e)
      toast.error('Failed to send order')
    }
  }

  const switchUser = (username: string) => {
    if (!wsIsOpen()) {
      console.warn('WS not connected, cannot switch user')
      toast.error('Not connected to server')
      return
    }
    try {
      wsSend({ type: 'switch_user', username })
    } catch (e) {
      console.error(e)
      toast.error('Failed to switch user')
    }
  }

  const switchAccount = (accountId: number) => {
    if (!wsIsOpen()) {
      console.warn('WS not connected, cannot switch account')
      toast.error('Not connected to server')
      return
    }
    try {
      wsSend({ type: 'switch_account', account_id: accountId })
    } catch (e) {
      console.error(e)
      toast.error('Failed to switch AI trader')
    }
  }

  const handleAccountUpdated = () => {
    setAccountRefreshTrigger(prev => prev + 1)
    requestWsSnapshot()
    setHyperliquidRefreshKey(prev => prev + 1)
  }

  // Create minimal state if overview not yet loaded to avoid stuck loading screen
  const effectiveOverview = overview || {
    account: { id: 1, user_id: 1, name: 'Trading Account', account_type: 'AI', initial_capital: 0, current_cash: 0, frozen_cash: 0 },
    total_assets: 0,
    positions_value: 0
  }

  // ── Keep-Alive page registry ──
  // Track which pages have been visited so we keep them mounted (hidden)
  // instead of destroying/recreating them on every switch.
  // IMPORTANT: hooks must be before any conditional return (React Rules of Hooks)
  const [visitedPages, setVisitedPages] = useState<Set<string>>(() => new Set([currentPage]))

  useEffect(() => {
    setVisitedPages(prev => {
      if (prev.has(currentPage)) return prev
      const next = new Set(prev)
      next.add(currentPage)
      return next
    })
  }, [currentPage])

  // 其他页面需要WebSocket连接和完整的user/account数据（ATAS V2除外，它可以自行加载账户）
  if (!user || !account) {
    if (!NO_CONN_PAGES.has(currentPage)) {
      return (
        <div className="h-screen w-screen flex items-center justify-center bg-background text-foreground">
          <div className="text-center">
            <div className="text-lg text-muted-foreground mb-2">Connecting to trading server...</div>
            <div className="text-sm text-muted-foreground/60">正在建立连接，请稍候</div>
          </div>
        </div>
      )
    }
  }

  const refreshData = () => {
    requestWsSnapshot()
    setHyperliquidRefreshKey(prev => prev + 1)
  }

  /**
   * Wrap a page in a keep-alive container:
   * - Rendered once when first visited, then kept in DOM.
   * - Inactive pages get `display:none` (zero layout cost, preserves state).
   * - PageActiveContext tells child components whether to run polling.
   */
  const keepAlive = (pageKey: string, node: React.ReactNode) => {
    const isActive = currentPage === pageKey
    if (!isActive && !visitedPages.has(pageKey)) return null
    return (
      <div
        key={pageKey}
        className={isActive
          ? 'flex flex-col flex-1 min-h-0 overflow-hidden'
          : 'hidden'}
        data-page={pageKey}
      >
        <PageActiveContext.Provider value={isActive}>
          <ErrorBoundary>
            <Suspense fallback={<div className="flex items-center justify-center p-8 text-muted-foreground">加载中...</div>}>
              {node}
            </Suspense>
          </ErrorBoundary>
        </PageActiveContext.Provider>
      </div>
    )
  }

  const renderMainContent = () => {
    return (
      <main className="flex flex-1 flex-col min-h-0 min-w-0 overflow-hidden">

        {/* ── Obsidia 知识库视图（真实 vault）── */}
        {keepAlive('agent-evolution', <AgentEvolutionView onNavigate={setCurrentPage} />)}
        {keepAlive('vault-graph', <VaultGraphView onNavigate={setCurrentPage} />)}
        {keepAlive('vault-canvas', <VaultCanvasView onNavigate={setCurrentPage} />)}
        {keepAlive('vault-explorer', <VaultExplorerView onNavigate={setCurrentPage} />)}

        {keepAlive('comprehensive', (
          <div className="flex flex-col flex-1 min-h-0">
            <TradingDashboardPro onNavigate={setCurrentPage} />
          </div>
        ))}

        {keepAlive('system-logs', <SystemLogs />)}

        {keepAlive('prompt-management', <PromptManager />)}

        {keepAlive('modern-signals', (
          <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
            <ModernSignalManager />
          </div>
        ))}

        {keepAlive('smart-signal-generator', (
          <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
            <SmartSignalGenerator />
          </div>
        ))}

        {keepAlive('attribution', <AttributionAnalysis />)}

        {keepAlive('analytics', <AnalyticsPage accountId={account?.id} />)}

        {keepAlive('trader-management', <TraderManagement />)}

        {keepAlive('hyperliquid', <UnifiedTradingPage accountId={account?.id || 1} />)}

        {keepAlive('klines', (
          <KlinesView onAccountUpdated={handleAccountUpdated} />
        ))}

        {keepAlive('data-center', (
          <div className="flex-1 overflow-auto">
            <DataCenterView />
          </div>
        ))}

        {keepAlive('market-intelligence', (
          <MarketIntelView />
        ))}

        {keepAlive('scalp-config', (
          <ScalpConfigPage />
        ))}

        {keepAlive('mid-config', (
          <MidConfigPage />
        ))}

        {keepAlive('long-config', (
          <LongConfigPage />
        ))}

        {keepAlive('mid-prompt', (
          <MidPromptPage />
        ))}

        {keepAlive('long-prompt', (
          <LongPromptPage />
        ))}

        {keepAlive('risk', (
          <div className="flex-1 overflow-auto">
            <RiskPage />
          </div>
        ))}

        {keepAlive('settings', (
          <div className="flex-1 overflow-auto">
            <SettingsPage />
          </div>
        ))}

        {keepAlive('llm-billing', (
          <div className="flex-1 overflow-auto">
            <LLMBillingPage />
          </div>
        ))}

        {keepAlive('user-guide', <UserGuide />)}

        {/* Phase 4-7: 前端同步新页面 */}
        {keepAlive('market-scanner', (
          <div className='flex-1 overflow-auto'>
            <MarketScannerPage />
          </div>
        ))}

        {keepAlive('exchange-hub', (
          <div className='flex-1 overflow-auto'>
            <ExchangeHubPage />
          </div>
        ))}

        {keepAlive('arbitrage-hub', (
          <div className='flex-1 overflow-auto'>
            <ArbitrageHubPage />
          </div>
        ))}

        {keepAlive('unified-factor', (
          <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
            <UnifiedFactorPage />
          </div>
        ))}

        {keepAlive('intelligent-learning', (
          <div className="flex flex-col flex-1 min-h-0 overflow-y-auto">
            <IntelligentLearningCenter />
          </div>
        ))}

        {keepAlive('atas-console', (
          <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
            <TradingConsole />
          </div>
        ))}

        {keepAlive('atas-v2', (
          <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
            <ATASV2Page globalAccount={account} globalAccounts={accounts} />
          </div>
        ))}

        {keepAlive('paper-trading', (
          <div className="flex-1 overflow-auto">
            <PaperTradingPage />
          </div>
        ))}

        {keepAlive('data-quality', (
          <div className="flex-1 overflow-auto">
            <DataQualityPanel />
          </div>
        ))}

        {keepAlive('hypothesis', (
          <div className="flex-1 overflow-auto">
            <HypothesisPanel />
          </div>
        ))}

        {keepAlive('fee-monitor', (
          <div className="flex-1 overflow-auto">
            <FeeMonitorPanel />
          </div>
        ))}

        {keepAlive('exchange-config', (
          <div className="flex-1 overflow-auto">
            <ExchangeConfigPanel />
          </div>
        ))}
      </main>
    )
  }

  const statusRight = (
    <>
      <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--obs-green)', display: 'inline-block' }} />
        Hyperliquid 已连接
      </span>
      <span>系统运行中</span>
      <span>{new Date().toLocaleDateString('zh-CN')}</span>
    </>
  )

  return (
    <>
      {/* Obsidia 工作台外壳（替代 Win95）——保留全部 keepAlive/WS/全局状态 */}
      <ObsidiaShell
        currentPage={currentPage}
        onNavigate={setCurrentPage}
        titles={PAGE_TITLES}
        statusRight={statusRight}
      >
        {renderMainContent()}
      </ObsidiaShell>

      <AuthorizationModal
        isOpen={authModalOpen}
        onClose={handleAuthModalClose}
        unauthorizedAccounts={unauthorizedAccounts}
        onAuthorizationComplete={handleAuthorizationComplete}
      />
      
      {/* 全局回测悬浮进度窗口 */}
      <BacktestFloatingProgress />
      <AlphaAssistantWidget />
    </>
  )
}
