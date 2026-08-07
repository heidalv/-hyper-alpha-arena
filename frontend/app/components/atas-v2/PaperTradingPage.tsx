/**
 * PaperTradingPage — 模拟交易独立页面
 *
 * 从 AI 策略中心的 Tab 中独立出来，作为侧边栏直接入口。
 */
import React, { Suspense } from 'react'
import { Brain } from 'lucide-react'

const PaperTradingPanel = React.lazy(() => import('./PaperTradingPanel'))

function TabLoading({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center py-24">
      <div className="text-center space-y-3">
        <Brain className="w-8 h-8 mx-auto opacity-30 animate-pulse" />
        <p className="text-sm text-muted-foreground">正在加载{label}...</p>
      </div>
    </div>
  )
}

export default function PaperTradingPage() {
  return (
    <div className="h-full w-full flex flex-col bg-background">
      <div className="flex-shrink-0 flex items-center gap-3 border-b bg-background/95 backdrop-blur px-6 py-3">
        <Brain className="w-5 h-5 text-purple-500" />
        <span className="font-semibold text-sm">模拟交易</span>
        <span className="text-xs text-muted-foreground ml-2">
          Paper Trading · 虚拟资金测试
        </span>
      </div>
      <div className="flex-1 min-h-0 overflow-auto">
        <Suspense fallback={<TabLoading label="模拟交易" />}>
          <div className="p-6">
            <PaperTradingPanel />
          </div>
        </Suspense>
      </div>
    </div>
  )
}
