/**
 * UnifiedFactorPage — 统一因子系统前端
 *
 * 合并原有 4 个分散的因子页面到统一的 Tab 布局：
 *   Tab 1: 因子总览 — 实时因子值 + 信号方向 + 合成信号
 *   Tab 2: 因子浏览 — 因子分类浏览 + 详情查看
 *   Tab 3: 云端同步 — 同步配置 + 云端因子管理 + 本地化
 *   Tab 4: 因子评估 — IC评估 + 质量报告 + 权重分析
 *   Tab 5: 统一信号 — 四源信号融合 + 共振检测 + 方向仪表盘
 */
import React, { Suspense, useState, useEffect, useCallback } from 'react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../ui/tabs'
import { Layers } from 'lucide-react'
import { useTradingPairs, FALLBACK_TRADING_PAIRS } from '@/hooks/useTradingPairs'

/* ---------- 懒加载子面板 ---------- */
const FactorOverviewPanel = React.lazy(() => import('./FactorOverviewPanel'))
const FactorBrowsePanel = React.lazy(() => import('./FactorBrowsePanel'))
const FactorCloudSyncPanel = React.lazy(() => import('./FactorCloudSyncPanel'))
const FactorEvalPanel = React.lazy(() => import('../monitor/FactorEvalPanel'))
const SignalUnifiedPanel = React.lazy(() => import('./SignalUnifiedPanel'))

function TabLoading({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center py-24">
      <div className="text-center space-y-3">
        <Layers className="w-8 h-8 mx-auto opacity-30 animate-pulse" />
        <p className="text-sm text-muted-foreground">正在加载{label}...</p>
      </div>
    </div>
  )
}

export default function UnifiedFactorPage() {
  const { symbols: configuredPairs } = useTradingPairs()
  const [userSymbols, setUserSymbols] = useState<string[]>(FALLBACK_TRADING_PAIRS)
  const [aiSymbols, setAiSymbols] = useState<string[]>([])
  const [selectedSymbol, setSelectedSymbol] = useState<string>('BTC')
  const [activeTab, setActiveTab] = useState<string>('overview')

  // 使用统一交易对 hook 更新用户配置的 symbols
  useEffect(() => {
    if (configuredPairs.length > 0) {
      setUserSymbols(configuredPairs)
      if (!configuredPairs.includes(selectedSymbol)) {
        setSelectedSymbol(configuredPairs[0])
      }
    }
  }, [configuredPairs.length > 0 ? configuredPairs.join(',') : ''])

  // 获取 AI 自主选币列表（优先持久化 API，不依赖内存调度器）
  useEffect(() => {
    const fetchAiSymbols = async () => {
      try {
        const res = await fetch('/api/auto-coin/active-symbols')
        if (res.ok) {
          const data = await res.json()
          if (Array.isArray(data.auto_symbols) && data.auto_symbols.length > 0) {
            setAiSymbols(data.auto_symbols)
            return
          }
        }
        // 兼容旧逻辑：从运行中会话 status 读取
        const sessionsRes = await fetch('/api/full-auto/sessions')
        const sessions = await sessionsRes.json()
        const active = Array.isArray(sessions)
          ? sessions.find((s: { status?: string }) => ['running', 'defensive', 'paused'].includes(s.status || ''))
            || sessions[0]
          : null
        if (!active?.session_id) return
        if (Array.isArray(active.auto_coin_symbols) && active.auto_coin_symbols.length > 0) {
          setAiSymbols(active.auto_coin_symbols)
          return
        }
        const statusRes = await fetch(`/api/auto-coin/${active.session_id}/status`)
        if (statusRes.ok) {
          const status = await statusRes.json()
          if (Array.isArray(status.auto_symbols) && status.auto_symbols.length > 0) {
            setAiSymbols(status.auto_symbols)
          }
        }
      } catch {
        // 非致命，AI 选币可能未启用
      }
    }
    fetchAiSymbols()
    const timer = setInterval(fetchAiSymbols, 60000)
    return () => clearInterval(timer)
  }, [])

  const allSymbols = [...userSymbols, ...aiSymbols.filter(s => !userSymbols.includes(s))]

  return (
    <div className="h-full w-full flex flex-col bg-background">
      {/* 顶栏 */}
      <div className="flex-shrink-0 flex items-center justify-between border-b bg-background/95 backdrop-blur px-6 py-3">
        <div className="flex items-center gap-3">
          <Layers className="w-5 h-5 text-purple-500" />
          <div>
            <span className="font-semibold text-sm">因子系统</span>
            <span className="text-xs text-muted-foreground ml-2">
              实时因子 · 因子浏览 · 云端同步 · IC评估 · 统一信号
            </span>
          </div>
        </div>
      </div>

      {/* 交易对平铺选择器：两行 — 用 span 避免 Win95 button !important 覆盖 */}
      <div className="flex-shrink-0 px-6 py-2 space-y-1.5 border-b">
        {/* 上行：用户配置 */}
        <div className="flex items-center gap-2">
          <span className="text-[10px] w-16 shrink-0" style={{ color: '#000' }}>自选币</span>
          <div className="flex items-center gap-1.5 flex-wrap">
            {userSymbols.map(s => {
              const active = selectedSymbol === s
              return (
                <span
                  key={s}
                  onClick={() => setSelectedSymbol(s)}
                  style={active
                    ? { backgroundColor: '#dc2626', color: '#fff', padding: '2px 10px', fontWeight: 700, fontSize: '12px', cursor: 'pointer', display: 'inline-block' }
                    : { color: '#000', padding: '2px 10px', fontSize: '11px', cursor: 'pointer', display: 'inline-block' }
                  }
                >
                  {s}
                </span>
              )
            })}
            {userSymbols.length === 0 && (
              <span className="text-[10px]" style={{ color: '#000' }}>暂无配置</span>
            )}
          </div>
        </div>
        {/* 下行：AI 自主选币 */}
        {aiSymbols.length > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-[10px] w-16 shrink-0" style={{ color: '#000' }}>AI选币</span>
            <div className="flex items-center gap-1.5 flex-wrap">
              {aiSymbols.map(s => {
                const active = selectedSymbol === s
                return (
                  <span
                    key={`ai-${s}`}
                    onClick={() => setSelectedSymbol(s)}
                    style={active
                      ? { backgroundColor: '#f97316', color: '#fff', padding: '2px 10px', fontWeight: 700, fontSize: '12px', cursor: 'pointer', display: 'inline-block' }
                      : { color: '#000', padding: '2px 10px', fontSize: '11px', cursor: 'pointer', display: 'inline-block' }
                    }
                  >
                    {s}
                  </span>
                )
              })}
            </div>
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex-1 min-h-0 overflow-hidden">
        <Tabs value={activeTab} onValueChange={setActiveTab} className="h-full flex flex-col">
          <TabsList className="flex-shrink-0 mx-6 mt-3 grid grid-cols-5 w-auto max-w-2xl h-9">
            <TabsTrigger value="overview" className="text-xs">因子总览</TabsTrigger>
            <TabsTrigger value="browse" className="text-xs">因子浏览</TabsTrigger>
            <TabsTrigger value="cloud" className="text-xs">云端同步</TabsTrigger>
            <TabsTrigger value="eval" className="text-xs">因子评估</TabsTrigger>
            <TabsTrigger value="signal" className="text-xs">统一信号</TabsTrigger>
          </TabsList>

          <div className="flex-1 min-h-0 overflow-auto mt-3">
            <TabsContent value="overview" className="h-full m-0 px-6 pb-6">
              <Suspense fallback={<TabLoading label="因子总览" />}>
                <FactorOverviewPanel symbol={selectedSymbol} />
              </Suspense>
            </TabsContent>

            <TabsContent value="browse" className="h-full m-0 px-6 pb-6">
              <Suspense fallback={<TabLoading label="因子浏览" />}>
                <FactorBrowsePanel symbol={selectedSymbol} />
              </Suspense>
            </TabsContent>

            <TabsContent value="cloud" className="h-full m-0 px-6 pb-6">
              <Suspense fallback={<TabLoading label="云端同步" />}>
                <FactorCloudSyncPanel />
              </Suspense>
            </TabsContent>

            <TabsContent value="eval" className="h-full m-0 px-6 pb-6">
              <Suspense fallback={<TabLoading label="因子评估" />}>
                <FactorEvalPanel symbols={allSymbols} />
              </Suspense>
            </TabsContent>

            <TabsContent value="signal" className="h-full m-0 px-6 pb-6">
              <Suspense fallback={<TabLoading label="统一信号" />}>
                <SignalUnifiedPanel symbol={selectedSymbol} />
              </Suspense>
            </TabsContent>
          </div>
        </Tabs>
      </div>
    </div>
  )
}
