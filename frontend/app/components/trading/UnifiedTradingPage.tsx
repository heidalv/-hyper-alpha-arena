/**
 * UnifiedTradingPage - 统一手动交易页面
 *
 * 根据当前选择的交易所显示对应的手动交易界面：
 * - Hyperliquid: 显示 HyperliquidPage
 * - Binance: 显示 BinanceManualTradingPage
 */
import { useTranslation } from 'react-i18next'
import { useExchange } from '@/contexts/ExchangeContext'
import { HyperliquidPage } from '@/components/hyperliquid'
import BinanceManualTradingPage from './BinanceManualTradingPage'
import { Badge } from '@/components/ui/badge'
import { Wallet } from 'lucide-react'

interface UnifiedTradingPageProps {
  accountId: number
}

export default function UnifiedTradingPage({ accountId }: UnifiedTradingPageProps) {
  useTranslation()
  const { currentExchange, exchanges } = useExchange()
  const currentExchangeInfo = exchanges.find(ex => ex.id === currentExchange)

  if (currentExchange === 'binance') {
    return <BinanceManualTradingPage />
  }

  if (currentExchange === 'aster') {
    return (
      <div className="container mx-auto p-6">
        <div className="mb-6">
          <h1 className="text-3xl font-bold flex items-center gap-3">
            手动交易
            <Badge variant="outline" className="text-sm font-normal">
              {currentExchangeInfo?.displayName || 'Aster DEX'}
            </Badge>
          </h1>
          <p className="text-gray-600 mt-1">手动交易操作</p>
        </div>
        <div className="flex items-center justify-center h-64 bg-muted/50 rounded-lg">
          <div className="text-center text-muted-foreground">
            <Wallet className="h-12 w-12 mx-auto mb-2 opacity-50" />
            <p className="text-lg font-medium">Aster DEX 即将推出</p>
            <p className="text-sm">手动交易功能正在开发中，敬请期待</p>
          </div>
        </div>
      </div>
    )
  }

  // Default: Hyperliquid
  return <HyperliquidPage />
}
