/**
 * BinancePage - Main page for Binance trading configuration and management
 */

import { useState } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { 
  BinanceConfigPanel, 
  BinanceBalanceCard, 
  BinancePositionsTable,
  BinanceManualTrading 
} from '@/components/binance';
import type { BinanceConfig } from '@/lib/types/binance';

interface BinancePageProps {
  accountId: number;
}

export default function BinancePage({ accountId }: BinancePageProps) {
  const [config, setConfig] = useState<BinanceConfig | null>(null);

  return (
    <div className="container mx-auto p-6 space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold">币安交易</h1>
        <p className="text-muted-foreground">
          配置和管理您的币安交易账户
        </p>
      </div>

      <Tabs defaultValue="configuration" className="w-full">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="configuration">配置</TabsTrigger>
          <TabsTrigger value="balance" disabled={!config?.configured}>
            余额和持仓
          </TabsTrigger>
          <TabsTrigger value="trading" disabled={!config?.configured || !config?.enabled}>
            交易
          </TabsTrigger>
        </TabsList>

        <TabsContent value="configuration" className="space-y-4">
          <BinanceConfigPanel
            accountId={accountId}
            onConfigChange={(newConfig) => setConfig(newConfig)}
          />
        </TabsContent>

        <TabsContent value="balance" className="space-y-4">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <BinanceBalanceCard
              accountId={accountId}
              enabled={config?.enabled || false}
            />
            <BinancePositionsTable
              accountId={accountId}
              enabled={config?.enabled || false}
              marketType={config?.market_type || config?.marketType || 'futures'}
            />
          </div>
        </TabsContent>

        <TabsContent value="trading" className="space-y-4">
          <BinanceManualTrading
            accountId={accountId}
            enabled={config?.enabled || false}
            marketType={config?.market_type || config?.marketType || 'futures'}
          />
        </TabsContent>
      </Tabs>

      <div className="mt-8 p-4 bg-muted/50 rounded-lg">
        <h3 className="font-semibold mb-2">快速链接</h3>
        <ul className="space-y-1 text-sm text-muted-foreground">
          <li>
            • <a href="https://www.binance.com/zh-CN/my/settings/api-management" target="_blank" rel="noopener noreferrer" className="hover:underline">
              创建币安API密钥
            </a>
          </li>
          <li>
            • <a href="https://testnet.binancefuture.com" target="_blank" rel="noopener noreferrer" className="hover:underline">
              币安合约测试网
            </a>
          </li>
          <li>
            • <a href="https://www.binance.com/zh-CN/support/faq" target="_blank" rel="noopener noreferrer" className="hover:underline">
              币安支持与常见问题
            </a>
          </li>
        </ul>
      </div>
    </div>
  );
}
