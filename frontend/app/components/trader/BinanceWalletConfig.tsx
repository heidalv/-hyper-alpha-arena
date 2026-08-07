/**
 * Binance Wallet Configuration Component
 * 
 * A simplified version of BinanceConfigPanel for use within the unified wallet config.
 */

import { useState, useEffect } from 'react'
import toast from 'react-hot-toast'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Wallet, Eye, EyeOff, CheckCircle, RefreshCw, Settings, Trash2 } from 'lucide-react'
import { getBinanceConfig, setupBinanceAccount, enableBinanceTrading, disableBinanceTrading, deleteBinanceConfig } from '@/lib/binanceApi'
import type { BinanceConfig, BinanceSetupRequest } from '@/lib/types/binance'
import { useTranslation } from 'react-i18next'

interface BinanceWalletConfigProps {
  accountId: number
  onConfigChange?: () => void
}

export default function BinanceWalletConfig({
  accountId,
  onConfigChange
}: BinanceWalletConfigProps) {
  useTranslation()
  const [config, setConfig] = useState<BinanceConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [editing, setEditing] = useState(false)
  const [showSecret, setShowSecret] = useState(false)

  // Form state
  const [apiKey, setApiKey] = useState('')
  const [apiSecret, setApiSecret] = useState('')
  const [marketType, setMarketType] = useState<'spot' | 'futures'>('futures')
  const [testnet, setTestnet] = useState(false)
  const [maxLeverage, setMaxLeverage] = useState(20)

  useEffect(() => {
    loadConfig()
  }, [accountId])

  const loadConfig = async () => {
    try {
      setLoading(true)
      const data = await getBinanceConfig(accountId)
      setConfig(data)
      setMarketType(data.market_type || data.marketType || 'futures')
      setTestnet(data.testnet || false)
      setMaxLeverage(data.max_leverage || data.maxLeverage || 20)
    } catch (error) {
      console.error('Failed to load Binance config:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleSetup = async () => {
    if (!apiKey || !apiSecret) {
      toast.error('请输入API密钥和API密钥')
      return
    }

    try {
      setSubmitting(true)
      
      // 合约模式强制使用主网
      const actualTestnet = marketType === 'futures' ? false : testnet

      const setupData: BinanceSetupRequest = {
        api_key: apiKey,
        api_secret: apiSecret,
        market_type: marketType,
        testnet: actualTestnet,
        max_leverage: maxLeverage,
      }

      const result = await setupBinanceAccount(accountId, setupData)

      if (result.success) {
        toast.success('币安账户配置成功！')
        setApiKey('')
        setApiSecret('')
        setEditing(false)
        await loadConfig()
        onConfigChange?.()
      } else {
        toast.error(result.message || '设置失败')
      }
    } catch (error: any) {
      console.error('Setup error:', error)
      toast.error(error.message || '币安账户设置失败')
    } finally {
      setSubmitting(false)
    }
  }

  const handleToggleEnabled = async () => {
    if (!config) return

    try {
      setSubmitting(true)
      if (config.enabled) {
        await disableBinanceTrading(accountId)
        toast.success('币安交易已禁用')
      } else {
        await enableBinanceTrading(accountId)
        toast.success('币安交易已启用')
      }
      await loadConfig()
      onConfigChange?.()
    } catch (error: any) {
      toast.error(error.message || '切换交易状态失败')
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async () => {
    if (!confirm('确定要删除币安钱包配置吗？此操作无法撤销。')) {
      return
    }

    try {
      setSubmitting(true)
      await deleteBinanceConfig(accountId)
      toast.success('币安钱包配置已删除')
      setConfig(null)
      setEditing(false)
      onConfigChange?.()
    } catch (error: any) {
      console.error('Delete error:', error)
      toast.error(error.message || '删除配置失败')
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) {
    return (
      <div className="p-4 border rounded-lg">
        <div className="flex items-center justify-center py-8">
          <RefreshCw className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* Configured status display */}
      {config?.configured && !editing ? (
        <div className="p-4 border rounded-lg space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Wallet className="h-4 w-4 text-muted-foreground" />
              <Badge variant={testnet ? 'default' : 'destructive'} className="text-xs">
                {testnet ? 'TESTNET' : 'MAINNET'}
              </Badge>
              <Badge variant="outline" className="text-xs">
                {marketType === 'spot' ? '现货' : '合约'}
              </Badge>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setEditing(true)}
              >
                <Settings className="h-3 w-3 mr-1" />
                配置
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={handleDelete}
                disabled={submitting}
              >
                <Trash2 className="h-3 w-3" />
              </Button>
            </div>
          </div>

          {/* Status info */}
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div>
              <div className="text-muted-foreground">状态</div>
              <div className="font-medium flex items-center gap-1">
                {config.enabled ? (
                  <>
                    <CheckCircle className="h-3 w-3 text-green-600" />
                    已启用
                  </>
                ) : (
                  <>⏸️ 已暂停</>
                )}
              </div>
            </div>
            <div>
              <div className="text-muted-foreground">最大杠杆</div>
              <div className="font-medium">{config.max_leverage || config.maxLeverage}x</div>
            </div>
            <div>
              <div className="text-muted-foreground">网络</div>
              <div className="font-medium">{config.testnet ? '测试网' : '主网'}</div>
            </div>
          </div>

          {/* Toggle trading */}
          <div className="flex items-center justify-between pt-2 border-t">
            <span className="text-sm">启用交易</span>
            <Switch
              checked={config.enabled}
              onCheckedChange={handleToggleEnabled}
              disabled={submitting}
            />
          </div>
        </div>
      ) : (
        /* Configuration form */
        <div className="p-4 border rounded-lg space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Wallet className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm font-medium">币安配置</span>
            </div>
            {config?.configured && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setEditing(false)}
              >
                取消
              </Button>
            )}
          </div>

          {!config?.configured && (
            <div className="p-2 bg-yellow-50 border border-yellow-200 rounded text-xs">
              <p className="text-yellow-800">
                ⚠️ 未配置币安账户。请设置您的API凭证。
              </p>
            </div>
          )}

          {/* API Key */}
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">API 密钥</label>
            <Input
              type="text"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="输入您的币安API密钥"
              className="h-8 text-xs"
            />
          </div>

          {/* API Secret */}
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">API 密钥</label>
            <div className="flex gap-2">
              <Input
                type={showSecret ? 'text' : 'password'}
                value={apiSecret}
                onChange={(e) => setApiSecret(e.target.value)}
                placeholder="输入您的币安API密钥"
                className="h-8 text-xs"
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setShowSecret(!showSecret)}
                className="h-8 px-2"
              >
                {showSecret ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
              </Button>
            </div>
          </div>

          {/* Market Type */}
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">市场类型</label>
              <Select value={marketType} onValueChange={(v: any) => setMarketType(v)}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="spot">现货</SelectItem>
                  <SelectItem value="futures">合约</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {marketType === 'futures' && (
              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">最大杠杆</label>
                <Input
                  type="number"
                  min={1}
                  max={125}
                  value={maxLeverage}
                  onChange={(e) => setMaxLeverage(parseInt(e.target.value) || 20)}
                  className="h-8 text-xs"
                />
              </div>
            )}
          </div>

          {/* Testnet toggle */}
          <div className="flex items-center justify-between">
            <div className="flex flex-col gap-1">
              <span className="text-xs text-muted-foreground">使用测试网</span>
              {marketType === 'futures' && (
                <span className="text-xs text-yellow-600">
                  ⚠️ 合约测试网已废弃，仅主网可用
                </span>
              )}
            </div>
            <Switch
              checked={testnet}
              onCheckedChange={setTestnet}
              disabled={marketType === 'futures'}
            />
          </div>

          {/* Submit */}
          <Button
            onClick={handleSetup}
            disabled={submitting || !apiKey || !apiSecret}
            size="sm"
            className="w-full h-8 text-xs"
          >
            {submitting ? (
              <>
                <RefreshCw className="mr-2 h-3 w-3 animate-spin" />
                设置中...
              </>
            ) : (
              config?.configured ? '更新配置' : '设置币安账户'
            )}
          </Button>

          {/* Security tip */}
          <p className="text-xs text-muted-foreground">
            🔒 您的API凭证在存储前会被加密。请确保您的API密钥已启用交易权限。
          </p>
        </div>
      )}
    </div>
  )
}
