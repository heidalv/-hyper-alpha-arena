/**
 * Unified Wallet Configuration Panel
 *
 * Displays the appropriate wallet configuration based on the exchange
 * assigned to the selected trader (via `exchange` prop).
 * Supports Hyperliquid (private key), CCXT exchanges (API Key/Secret),
 * and Asterdex (coming soon).
 */

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Wallet } from 'lucide-react'
import BinanceWalletConfig from './BinanceWalletConfig'
import { EXCHANGE_NAMES } from '@/components/trader/ExchangeConstants'

interface UnifiedWalletConfigPanelProps {
  accountId: number
  accountName: string
  exchange: string
  onWalletConfigured?: () => void
}

export default function UnifiedWalletConfigPanel({
  accountId,
  accountName,
  exchange,
  onWalletConfigured
}: UnifiedWalletConfigPanelProps) {
  const exchangeName = EXCHANGE_NAMES[exchange] || exchange

  return (
    <div className="space-y-3">
      {/* Exchange indicator */}
      <div className="flex items-center gap-2 pb-2 border-b">
        <Wallet className="h-4 w-4 text-muted-foreground" />
        <span className="text-sm font-medium">钱包配置</span>
        <Badge variant="outline" className="text-xs">
          {exchangeName}
        </Badge>
      </div>

      {/* Dynamic wallet config based on exchange — Hyperliquid 已统一到「交易所配置」 */}
      {exchange === 'hyperliquid' && (
        <div className="rounded-lg border border-dashed p-6 text-center space-y-3 bg-muted/20">
          <Wallet className="w-8 h-8 mx-auto text-blue-600 opacity-80" />
          <div>
            <p className="text-sm font-medium">Hyperliquid 钱包已移至「交易所配置」</p>
            <p className="text-xs text-muted-foreground mt-1">
              请在侧边栏打开「交易所配置」，选择交易员后配置 Testnet / Mainnet 私钥。
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => { window.location.hash = 'exchange-config' }}
          >
            前往交易所配置
          </Button>
        </div>
      )}

      {(exchange === 'binance' || exchange === 'bybit' || exchange === 'okx' || exchange === 'gateio') && (
        <BinanceWalletConfig
          accountId={accountId}
          onConfigChange={onWalletConfigured}
        />
      )}

      {exchange === 'asterdex' && (
        <div className="p-4 border rounded-lg bg-muted/50">
          <div className="text-center text-sm text-muted-foreground">
            <p className="mb-2">Aster DEX 即将推出</p>
            <p className="text-xs">Aster DEX 钱包配置正在开发中，敬请期待。</p>
          </div>
        </div>
      )}
    </div>
  )
}
