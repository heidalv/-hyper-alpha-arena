/**
 * BinanceManualTradingPage - 币安手动交易页面
 *
 * 提供币安手动下单功能
 */
import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { getAccounts } from '@/lib/api'
import { getBinanceConfig } from '@/lib/binanceApi'
import { BinanceManualTrading, BinanceBalanceCard, BinancePositionsTable } from '@/components/binance'
import type { BinanceConfig } from '@/lib/types/binance'

interface AccountOption {
  id: number
  name: string
  config: BinanceConfig | null
}

export default function BinanceManualTradingPage() {
  useTranslation()
  const [accounts, setAccounts] = useState<AccountOption[]>([])
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    loadAccounts()
  }, [])

  const loadAccounts = async () => {
    try {
      setLoading(true)
      const accountList = await getAccounts()
      
      const accountsWithConfig = await Promise.all(
        accountList.map(async (account: any) => {
          try {
            const config = await getBinanceConfig(account.id)
            return { id: account.id, name: account.name, config }
          } catch (error) {
            console.warn(`Failed to load config for account ${account.id}:`, error)
            return { id: account.id, name: account.name, config: null }
          }
        })
      )
      
      const configuredAccounts = accountsWithConfig.filter(a => a.config?.configured)
      setAccounts(configuredAccounts)
      
      if (configuredAccounts.length > 0 && !selectedAccountId) {
        setSelectedAccountId(configuredAccounts[0].id)
      }
    } catch (error) {
      console.error('Failed to load accounts:', error)
      // 不阻塞页面加载，显示空列表
    } finally {
      setLoading(false)
    }
  }

  const selectedAccount = accounts.find(a => a.id === selectedAccountId)

  if (loading) {
    return (
      <div className="container mx-auto p-6">
        <div className="flex items-center justify-center h-64">
          <div className="text-muted-foreground">加载中...</div>
        </div>
      </div>
    )
  }

  return (
    <div className="container mx-auto p-6 h-full overflow-y-auto">
      <div className="mb-6">
        <h1 className="text-3xl font-bold flex items-center gap-3">
          手动交易
          <Badge variant="outline" className="text-sm font-normal">
            Binance
          </Badge>
        </h1>
        <p className="text-gray-600 mt-1">手动交易操作</p>
      </div>

      {/* Account Selector */}
      {accounts.length > 0 && (
        <div className="mb-6">
          <label className="text-sm font-medium mb-2 block">选择交易账户</label>
          <Select
            value={selectedAccountId?.toString() || ''}
            onValueChange={(v) => setSelectedAccountId(parseInt(v))}
          >
            <SelectTrigger className="w-[300px]">
              <SelectValue placeholder="选择账户" />
            </SelectTrigger>
            <SelectContent>
              {accounts.map((account) => (
                <SelectItem key={account.id} value={account.id.toString()}>
                  {account.name}
                  {account.config?.testnet && ' (测试网)'}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {accounts.length === 0 && (
        <div className="flex items-center justify-center h-64 bg-muted/50 rounded-lg">
          <div className="text-center text-muted-foreground">
            <p className="text-lg font-medium">未配置币安账户</p>
            <p className="text-sm">请先在 AI 交易员管理中配置币安钱包</p>
          </div>
        </div>
      )}

      {selectedAccount && selectedAccount.config?.configured && (
        <div className="space-y-6">
          {/* Balance and Positions */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-1">
              <BinanceBalanceCard
                accountId={selectedAccountId!}
                enabled={selectedAccount.config.enabled}
              />
            </div>
            <div className="lg:col-span-2">
              <BinancePositionsTable
                accountId={selectedAccountId!}
                enabled={selectedAccount.config.enabled}
                marketType={selectedAccount.config.market_type || selectedAccount.config.marketType || 'futures'}
              />
            </div>
          </div>

          {/* Manual Trading Form */}
          <BinanceManualTrading
            accountId={selectedAccountId!}
            enabled={selectedAccount.config.enabled}
            marketType={selectedAccount.config.market_type || selectedAccount.config.marketType || 'futures'}
          />
        </div>
      )}
    </div>
  )
}
