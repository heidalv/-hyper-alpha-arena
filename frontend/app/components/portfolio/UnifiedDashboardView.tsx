/**
 * UnifiedDashboardView - 统一数据看板
 *
 * 根据当前选择的交易所显示对应的数据：
 * - Hyperliquid: 显示 HyperliquidView
 * - Binance: 显示 BinanceView (包含 Dashboard + Feed)
 */
import React from 'react'
import { useExchange } from '@/contexts/ExchangeContext'
import HyperliquidView from '@/components/hyperliquid/HyperliquidView'
import BinanceView from '@/components/binance/BinanceView'

interface UnifiedDashboardViewProps {
  wsRef?: React.MutableRefObject<WebSocket | null>
  refreshKey?: number
  onPageChange?: (page: string) => void
}

export default function UnifiedDashboardView({
  wsRef,
  refreshKey = 0,
  onPageChange
}: UnifiedDashboardViewProps) {
  const { currentExchange } = useExchange()

  if (currentExchange === 'binance') {
    return (
      <BinanceView
        wsRef={wsRef}
        refreshKey={refreshKey}
        onPageChange={onPageChange}
      />
    )
  }

  // Default to Hyperliquid view (also for aster when supported)
  return (
    <HyperliquidView
      wsRef={wsRef}
      refreshKey={refreshKey}
      onPageChange={onPageChange}
    />
  )
}
