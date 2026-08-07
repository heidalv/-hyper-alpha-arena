/**
 * Binance Manual Trading Component
 * Allows users to manually place orders
 */

import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { placeBinanceOrder, getBinanceBalance } from '@/lib/binanceApi';
import type { BinanceOrderRequest, BinanceBalance } from '@/lib/types/binance';
import { useTradingPairs, FALLBACK_TRADING_PAIRS } from '@/hooks/useTradingPairs';

interface BinanceManualTradingProps {
  accountId: number;
  enabled: boolean;
  marketType: string;
}

export default function BinanceManualTrading({ accountId, enabled, marketType }: BinanceManualTradingProps) {
  const { symbols: configuredPairs } = useTradingPairs()
  const symbols = configuredPairs.length > 0 ? configuredPairs : FALLBACK_TRADING_PAIRS

  const [symbol, setSymbol] = useState('BTC');
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [amount, setAmount] = useState('');
  const [orderType, setOrderType] = useState<'market' | 'limit'>('market');
  const [price, setPrice] = useState('');
  const [leverage, setLeverage] = useState('10');
  const [reduceOnly, setReduceOnly] = useState(false);
  // 阶段 3.2: 执行算法（MARKET/TWAP/POV/FUNDING_IS/SOR）
  const [algo, setAlgo] = useState('MARKET');
  const [twapSlices, setTwapSlices] = useState('5');
  
  const [balance, setBalance] = useState<BinanceBalance | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  useEffect(() => {
    if (enabled) {
      loadBalance();
    }
  }, [accountId, enabled]);

  const loadBalance = async () => {
    try {
      const data = await getBinanceBalance(accountId);
      setBalance(data);
    } catch (err) {
      console.error('Failed to load balance:', err);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!amount || parseFloat(amount) <= 0) {
      setMessage({ type: 'error', text: '请输入有效的数量' });
      return;
    }

    if (orderType === 'limit' && (!price || parseFloat(price) <= 0)) {
      setMessage({ type: 'error', text: '请为限价单输入有效的价格' });
      return;
    }

    try {
      setSubmitting(true);
      setMessage(null);

      const orderRequest: BinanceOrderRequest = {
        symbol: `${symbol}/USDT`,
        side,
        amount: parseFloat(amount),
        order_type: orderType,
        algo,
        algo_config: algo === 'TWAP' ? { twap_slices: parseInt(twapSlices) || 5 } : undefined,
      };

      if (orderType === 'limit') {
        orderRequest.price = parseFloat(price);
      }

      if (marketType === 'futures') {
        orderRequest.leverage = parseInt(leverage);
        orderRequest.reduce_only = reduceOnly;
      }

      const result = await placeBinanceOrder(accountId, orderRequest);

      if (result.status === 'success') {
        setMessage({ 
          type: 'success', 
          text: `订单下单成功！订单ID: ${result.order_id}` 
        });
        // Reset form
        setAmount('');
        setPrice('');
        // Reload balance
        loadBalance();
      } else {
        setMessage({ 
          type: 'error', 
          text: result.error || '订单失败' 
        });
      }
    } catch (err: any) {
      setMessage({ 
        type: 'error', 
        text: err.message || '下单失败' 
      });
    } finally {
      setSubmitting(false);
    }
  };

  if (!enabled) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>手动交易</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">
            启用币安交易以访问手动交易功能
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {/* Balance Info */}
      <Card className="lg:col-span-1">
        <CardHeader>
          <CardTitle className="text-sm">账户余额</CardTitle>
        </CardHeader>
        <CardContent>
          {balance ? (
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">可用:</span>
                <span className="font-semibold">{balance.available_balance.toFixed(2)} USDT</span>
              </div>
              {marketType === 'futures' && balance.margin_used !== undefined && (
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">已用保证金:</span>
                  <span className="text-orange-600">{balance.margin_used.toFixed(2)} USDT</span>
                </div>
              )}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">加载中...</p>
          )}
        </CardContent>
      </Card>

      {/* Order Form */}
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>下单</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Messages */}
            {message && (
              <div className={`p-3 rounded-md text-sm ${
                message.type === 'error' 
                  ? 'bg-red-50 text-red-900' 
                  : 'bg-green-50 text-green-900'
              }`}>
                {message.text}
              </div>
            )}

            <div className="grid grid-cols-2 gap-4">
              {/* Symbol */}
              <div className="space-y-2">
                <Label>交易对</Label>
                <Select value={symbol} onValueChange={setSymbol}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {symbols.map(sym => (
                      <SelectItem key={sym} value={sym}>{sym}/USDT</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Side */}
              <div className="space-y-2">
                <Label>方向</Label>
                <div className="grid grid-cols-2 gap-2">
                  <Button
                    type="button"
                    variant={side === 'buy' ? 'default' : 'outline'}
                    className={side === 'buy' ? 'bg-green-600 hover:bg-green-700' : ''}
                    onClick={() => setSide('buy')}
                  >
                    买入 / 做多
                  </Button>
                  <Button
                    type="button"
                    variant={side === 'sell' ? 'default' : 'outline'}
                    className={side === 'sell' ? 'bg-red-600 hover:bg-red-700' : ''}
                    onClick={() => setSide('sell')}
                  >
                    卖出 / 做空
                  </Button>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              {/* Order Type */}
              <div className="space-y-2">
                <Label>订单类型</Label>
                <Select value={orderType} onValueChange={(v: any) => setOrderType(v)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="market">市价单</SelectItem>
                    <SelectItem value="limit">限价单</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {/* Amount */}
              <div className="space-y-2">
                <Label>数量</Label>
                <Input
                  type="number"
                  step="0.000001"
                  placeholder="0.001"
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                />
              </div>
            </div>

            {/* Limit Price */}
            {orderType === 'limit' && (
              <div className="space-y-2">
                <Label>限价 (USDT)</Label>
                <Input
                  type="number"
                  step="0.01"
                  placeholder="输入价格"
                  value={price}
                  onChange={(e) => setPrice(e.target.value)}
                />
              </div>
            )}

            {/* 执行算法（阶段 3.2: OrderAlgo 切片下单） */}
            <div className="space-y-2">
              <Label>执行算法</Label>
              <Select value={algo} onValueChange={setAlgo}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="MARKET">市价直下（默认）</SelectItem>
                  <SelectItem value="TWAP">TWAP 时间加权</SelectItem>
                  <SelectItem value="POV">POV 参与率</SelectItem>
                  <SelectItem value="FUNDING_IS">FUNDING_IS 资金费率</SelectItem>
                  <SelectItem value="SOR">SOR 智能路由</SelectItem>
                </SelectContent>
              </Select>
              {algo === 'TWAP' && (
                <Input
                  type="number"
                  min="2"
                  max="20"
                  placeholder="切片数 (默认 5)"
                  value={twapSlices}
                  onChange={(e) => setTwapSlices(e.target.value)}
                />
              )}
              <p className="text-xs text-muted-foreground">
                {algo === 'MARKET' && '* 市价单将立即以当前市场价格全部成交'}
                {algo === 'TWAP' && `* 按 ${parseInt(twapSlices) || 5} 个切片在约 10 秒内分时执行`}
                {algo === 'POV' && '* 按参与率切片执行（无成交量数据时自动降级 TWAP）'}
                {algo === 'FUNDING_IS' && '* 按资金费率周期切入（无费率时按 0 处理）'}
                {algo === 'SOR' && '* 智能路由（单一交易所时降级为单笔市价）'}
              </p>
            </div>

            {/* Futures Options */}
            {marketType === 'futures' && (
              <>
                <div className="space-y-2">
                  <Label>杠杆</Label>
                  <Input
                    type="number"
                    min="1"
                    max="125"
                    value={leverage}
                    onChange={(e) => setLeverage(e.target.value)}
                  />
                </div>

                <div className="flex items-center space-x-2">
                  <input
                    type="checkbox"
                    id="reduceOnly"
                    checked={reduceOnly}
                    onChange={(e) => setReduceOnly(e.target.checked)}
                    className="rounded"
                  />
                  <Label htmlFor="reduceOnly" className="cursor-pointer">
                    只减仓（仅平仓）
                  </Label>
                </div>
              </>
            )}

            {/* Submit Button */}
            <Button
              type="submit"
              className="w-full"
              disabled={submitting}
            >
              {submitting ? '下单中...' : `${side === 'buy' ? '买入' : '卖出'}订单`}
            </Button>

            <p className="text-xs text-muted-foreground">
              * 市价单将立即以当前市场价格执行
              {marketType === 'futures' && ' | 请谨慎使用杠杆'}
            </p>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
