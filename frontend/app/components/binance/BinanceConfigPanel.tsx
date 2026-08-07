/**
 * Binance Configuration Panel Component
 * 
 * Allows users to configure Binance API credentials and trading settings
 */

import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { getBinanceConfig, setupBinanceAccount, enableBinanceTrading, disableBinanceTrading } from '@/lib/binanceApi';
import type { BinanceConfig, BinanceSetupRequest } from '@/lib/types/binance';

interface BinanceConfigPanelProps {
  accountId: number;
  onConfigChange?: (config: BinanceConfig) => void;
}

export default function BinanceConfigPanel({ accountId, onConfigChange }: BinanceConfigPanelProps) {
  const [config, setConfig] = useState<BinanceConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Form state
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [marketType, setMarketType] = useState<'spot' | 'futures'>('futures');
  const [testnet, setTestnet] = useState(false);
  const [maxLeverage, setMaxLeverage] = useState(20);

  useEffect(() => {
    loadConfig();
  }, [accountId]);

  const loadConfig = async () => {
    try {
      setLoading(true);
      const data = await getBinanceConfig(accountId);
      setConfig(data);
      setMarketType(data.market_type || data.marketType || 'futures');
      setTestnet(data.testnet || false);
      setMaxLeverage(data.max_leverage || data.maxLeverage || 20);
      onConfigChange?.(data);
    } catch (error) {
      console.error('Failed to load config:', error);
      setMessage({ type: 'error', text: '加载配置失败' });
    } finally {
      setLoading(false);
    }
  };

  const handleSetup = async () => {
    // 首次配置必须提供API密钥
    if (!config?.configured && (!apiKey || !apiSecret)) {
      setMessage({ type: 'error', text: '请输入API密钥和API密钥' });
      return;
    }

    // 更新配置时，如果只提供了一个，则提示错误
    if (config?.configured && ((apiKey && !apiSecret) || (!apiKey && apiSecret))) {
      setMessage({ type: 'error', text: 'API密钥和API密钥必须同时更新或同时为空' });
      return;
    }

    try {
      setSubmitting(true);
      setMessage(null);

      const setupData: BinanceSetupRequest = {
        market_type: marketType,
        testnet: testnet,
        max_leverage: maxLeverage,
      };

      // 只在提供了API密钥时才添加到请求中
      if (apiKey && apiSecret) {
        setupData.api_key = apiKey;
        setupData.api_secret = apiSecret;
      }

      const result = await setupBinanceAccount(accountId, setupData);
      
      if (result.success) {
        setMessage({ type: 'success', text: result.message || '配置成功！' });
        setApiKey('');
        setApiSecret('');
        await loadConfig();
      } else {
        setMessage({ type: 'error', text: result.message || '设置失败' });
      }
    } catch (error: any) {
      console.error('Setup error:', error);
      setMessage({ type: 'error', text: error.message || '币安账户设置失败' });
    } finally {
      setSubmitting(false);
    }
  };

  const handleToggleEnabled = async () => {
    if (!config) return;

    try {
      setSubmitting(true);
      if (config.enabled) {
        await disableBinanceTrading(accountId);
      } else {
        await enableBinanceTrading(accountId);
      }
      await loadConfig();
      setMessage({
        type: 'success',
        text: config.enabled ? '币安交易已禁用' : '币安交易已启用',
      });
    } catch (error: any) {
      setMessage({ type: 'error', text: error.message || '切换交易状态失败' });
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-64">
          <div className="text-center">加载中...</div>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {/* Status Card */}
      {config?.configured && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span>币安状态</span>
              <div className="flex items-center gap-2">
                <span className="text-sm font-normal">
                  {config.enabled ? '✓ 已启用' : '✗ 已禁用'}
                </span>
              </div>
            </CardTitle>
            <CardDescription>
              市场类型: <strong>{(config.market_type || config.marketType) === 'spot' ? '现货' : '合约'}</strong> | 
              网络: <strong>{config.testnet ? '测试网' : '主网'}</strong> |
              最大杠杆: <strong>{config.max_leverage || config.maxLeverage}x</strong>
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Switch
                  checked={config.enabled}
                  onCheckedChange={handleToggleEnabled}
                  disabled={submitting}
                />
                <Label>启用币安交易</Label>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Configuration Card */}
      <Card>
        <CardHeader>
          <CardTitle>币安配置</CardTitle>
          <CardDescription>
            {config?.configured
              ? '更新您的币安API凭证和设置'
              : '设置您的币安API凭证以开始交易'}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Messages */}
          {message && (
            <div className={`p-3 rounded-md ${message.type === 'error' ? 'bg-red-50 text-red-900' : 'bg-green-50 text-green-900'}`}>
              {message.text}
            </div>
          )}

          {/* API Credentials */}
          {config?.configured && (
            <div className="p-3 rounded-md bg-gray-50 text-sm">
              <p className="text-muted-foreground">
                ✓ API密钥已配置（出于安全考虑不显示完整密钥）
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                如需更新，请在下方输入新的API凭证
              </p>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="apiKey">API密钥</Label>
            <Input
              id="apiKey"
              type="text"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={config?.configured ? "输入新的API密钥（留空保持不变）" : "输入您的币安API密钥"}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="apiSecret">API密钥</Label>
            <Input
              id="apiSecret"
              type="password"
              value={apiSecret}
              onChange={(e) => setApiSecret(e.target.value)}
              placeholder={config?.configured ? "输入新的API密钥（留空保持不变）" : "输入您的币安API密钥"}
            />
          </div>

          {/* Market Type */}
          <div className="space-y-2">
            <Label htmlFor="marketType">市场类型</Label>
            <Select value={marketType} onValueChange={(value: any) => setMarketType(value)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="spot">现货交易</SelectItem>
                <SelectItem value="futures">合约交易</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Testnet Switch */}
          <div className="flex items-center justify-between">
            <Label htmlFor="testnet">使用测试网</Label>
            <Switch
              id="testnet"
              checked={testnet}
              onCheckedChange={setTestnet}
            />
          </div>

          {/* Max Leverage (for futures) */}
          {marketType === 'futures' && (
            <div className="space-y-2">
              <Label htmlFor="maxLeverage">最大杠杆</Label>
              <Input
                id="maxLeverage"
                type="number"
                min={1}
                max={125}
                value={maxLeverage}
                onChange={(e) => setMaxLeverage(parseInt(e.target.value) || 20)}
              />
              <p className="text-xs text-muted-foreground">
                设置最大允许杠杆（合约交易支持1-125倍）
              </p>
            </div>
          )}

          {/* Info Message */}
          <div className="p-3 rounded-md bg-blue-50 text-blue-900 text-sm">
            <strong>安全提示：</strong>您的API凭证在存储前会被加密。
            请确保您的API密钥已启用交易权限。
          </div>

          {/* Submit Button */}
          <Button
            onClick={handleSetup}
            disabled={submitting || (!config?.configured && (!apiKey || !apiSecret))}
            className="w-full"
          >
            {submitting ? '设置中...' : (config?.configured ? '更新配置' : '设置币安账户')}
          </Button>
        </CardContent>
      </Card>

      {/* Usage Tips */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">使用指南</CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground space-y-2">
         <p>1. 在币安账户设置中创建API密钥</p>
          <p>2. 启用"现货交易"或"合约交易"权限</p>
          <p>3. （可选）启用IP白名单以增强安全性</p>
          <p>4. 在上方输入您的API凭证并点击"设置"</p>
          <p>5. 切换"启用币安交易"开关以开始交易</p>
        </CardContent>
      </Card>
    </div>
  );
}
