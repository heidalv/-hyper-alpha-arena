/**
 * AI 策略中心 — 统一入口页面
 *
 * 合并原 ATAS V2 策略中心 + 策略管理页面，Tab 布局：
 *   Tab 0: 运行控制 — 全自动交易面板（会话管理、币种选择、策略状态）
 *   Tab 1: 策略列表 — AI 策略 CRUD、运行状态、性能指标
 *   Tab 2: 快速试单 — 试单 + 学习激活
 *   Tab 3: 情报中心 — 市场情绪、新闻、鲸鱼异动
 *   Tab 4: 回测进化 — 主动发起回测、遗传进化引擎
 *   Tab 5: 门禁配置 — 交易门禁参数可视化配置（hard/soft 分层，业界标准）
 */
import React, { Suspense } from 'react'
import { AccountSnapshotProvider } from '@/contexts/AccountSnapshotContext';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../ui/tabs';
import { Brain, Play, Bot, Eye, Dna, Zap, Shield } from 'lucide-react';

const FullAutoPanel = React.lazy(() => import('./FullAutoPanel'));
const AiStrategyList = React.lazy(() => import('./AiStrategyList'));
const IntelligenceCenter = React.lazy(() => import('./IntelligenceCenter'));
const BacktestEvolutionPanel = React.lazy(() => import('./BacktestEvolutionPanel'));
const QuickTrialPanel = React.lazy(() => import('./QuickTrialPanel'));
const TradingGatesPanel = React.lazy(() => import('../config/TradingGatesPanel'));

function TabLoading({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center py-24">
      <div className="text-center space-y-3">
        <Brain className="w-8 h-8 mx-auto opacity-30 animate-pulse" />
        <p className="text-sm text-muted-foreground">正在加载{label}...</p>
      </div>
    </div>
  );
}

interface ATASV2PageProps {
  globalAccount?: { id: number; name: string } | null;
  globalAccounts?: { id: number; name: string; current_cash: number }[];
}

export default function ATASV2Page({ globalAccount, globalAccounts }: ATASV2PageProps = {}) {
  const accountId = globalAccount?.id || null;
  const accounts = globalAccounts || [];

  return (
    <div className="h-full w-full flex flex-col bg-background">
      {/* 顶栏 */}
      <div className="flex-shrink-0 flex items-center gap-3 border-b bg-background/95 backdrop-blur px-6 py-3">
        <Brain className="w-5 h-5 text-purple-500" />
        <div>
          <span className="font-semibold text-sm">AI 策略中心</span>
          <span className="text-xs text-muted-foreground ml-2">运行控制 · 策略列表 · 快速试单 · 情报 · 回测进化 · 门禁配置</span>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex-1 min-h-0 overflow-hidden">
        <AccountSnapshotProvider accountId={accountId} accounts={accounts}>
          <Tabs defaultValue="auto" className="h-full flex flex-col">
            <TabsList className="flex-shrink-0 mx-6 mt-3 grid grid-cols-6 w-auto max-w-4xl h-9">
              <TabsTrigger value="auto" className="text-xs gap-1.5">
                <Play className="w-3.5 h-3.5" />
                运行控制
              </TabsTrigger>
              <TabsTrigger value="strategies" className="text-xs gap-1.5">
                <Bot className="w-3.5 h-3.5" />
                策略列表
              </TabsTrigger>
              <TabsTrigger value="quick-trial" className="text-xs gap-1.5">
                <Zap className="w-3.5 h-3.5" />
                快速试单
              </TabsTrigger>
              <TabsTrigger value="intelligence" className="text-xs gap-1.5">
                <Eye className="w-3.5 h-3.5" />
                情报中心
              </TabsTrigger>
              <TabsTrigger value="evolution" className="text-xs gap-1.5">
                <Dna className="w-3.5 h-3.5" />
                回测进化
              </TabsTrigger>
              <TabsTrigger value="gates" className="text-xs gap-1.5">
                <Shield className="w-3.5 h-3.5" />
                门禁配置
              </TabsTrigger>
            </TabsList>

            {/* Tab 0: 运行控制 — 全自动交易 */}
            <TabsContent value="auto" className="flex-1 min-h-0 overflow-auto m-0 p-0 mt-2">
              <Suspense fallback={<TabLoading label="运行控制" />}>
                <FullAutoPanel />
              </Suspense>
            </TabsContent>

            {/* Tab 1: 策略列表 */}
            <TabsContent value="strategies" className="flex-1 min-h-0 overflow-auto m-0 p-0 mt-2">
              <Suspense fallback={<TabLoading label="策略列表" />}>
                <AiStrategyList />
              </Suspense>
            </TabsContent>

            {/* Tab 2: 快速试单 + 学习激活 */}
            <TabsContent value="quick-trial" className="flex-1 min-h-0 overflow-auto m-0 p-0 mt-2">
              <Suspense fallback={<TabLoading label="快速试单" />}>
                <QuickTrialPanel />
              </Suspense>
            </TabsContent>

            {/* Tab 3: 情报中心 */}
            <TabsContent value="intelligence" className="flex-1 min-h-0 overflow-auto m-0 p-0 mt-2">
              <Suspense fallback={<TabLoading label="情报中心" />}>
                <IntelligenceCenter />
              </Suspense>
            </TabsContent>

            {/* Tab 3: 回测进化 */}
            <TabsContent value="evolution" className="flex-1 min-h-0 overflow-auto m-0 p-0 mt-2">
              <div className="mx-6 mt-2 mb-1 px-3 py-2 text-[11px] bg-blue-500/5 border border-blue-500/20 rounded text-blue-300/90">
                本面板用于 <span className="font-semibold">主动发起回测</span> 与 <span className="font-semibold">深度遗传进化</span>。
                <span className="opacity-80 ml-1">
                  想看全局冠军策略进化历史？请到 <span className="font-semibold">AI 学习中心 → 进化系统</span>。
                </span>
              </div>
              <Suspense fallback={<TabLoading label="回测进化" />}>
                <BacktestEvolutionPanel />
              </Suspense>
            </TabsContent>

            {/* Tab 5: 门禁配置 */}
            <TabsContent value="gates" className="flex-1 min-h-0 overflow-auto m-0 p-0 mt-2">
              <div className="mx-6 mt-2">
                <Suspense fallback={<TabLoading label="门禁配置" />}>
                  <TradingGatesPanel />
                </Suspense>
              </div>
            </TabsContent>
          </Tabs>
        </AccountSnapshotProvider>
      </div>
    </div>
  );
}
