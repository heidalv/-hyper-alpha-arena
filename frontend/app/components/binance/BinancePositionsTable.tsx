/**
 * Binance Positions Table Component
 * Displays open positions (futures only)
 */

import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Table } from '@/components/ui/table';
import { getBinancePositions, closeBinancePosition } from '@/lib/binanceApi';
import type { BinancePosition } from '@/lib/types/binance';
import { getRefreshInterval } from '@/config/refresh';
import toast from 'react-hot-toast';
import { X } from 'lucide-react';
import { formatPrice, formatPercentage, formatSize } from '@/lib/priceFormat';

interface BinancePositionsTableProps {
  accountId: number;
  enabled: boolean;
  marketType: string;
  autoRefresh?: boolean; // 是否启用自动刷新，默认true
}

export default function BinancePositionsTable({ accountId, enabled, marketType, autoRefresh = true }: BinancePositionsTableProps) {
  const [positions, setPositions] = useState<BinancePosition[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [closingSymbol, setClosingSymbol] = useState<string | null>(null);

  // Extract base symbol from trading pair (e.g., "VIRTUAL/USDT:USDT" -> "VIRTUAL")
  const extractBaseSymbol = (symbol: string): string => {
    return symbol.split('/')[0].toUpperCase();
  };

  const loadPositions = async () => {
    if (!enabled || marketType !== 'futures') return;

    try {
      setLoading(true);
      setError(null);
      // ⚡ 关键修复：使用 forceRefresh=true 获取实时数据
      const data = await getBinancePositions(accountId, true);
      setPositions(data.positions || []);
    } catch (err: any) {
      console.error('Failed to load positions:', err);
      setError(err.message || 'Failed to load positions');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadPositions();

    // 自动刷新：使用统一配置（30秒）
    if (autoRefresh) {
      const interval = setInterval(() => {
        loadPositions();
      }, getRefreshInterval('binance_positions'));

      return () => clearInterval(interval);
    }
  }, [accountId, enabled, marketType, autoRefresh]);

  const handleClosePosition = async (symbol: string) => {
    if (closingSymbol) return; // Prevent double clicks

    try {
      setClosingSymbol(symbol);
      await closeBinancePosition(accountId, symbol);
      toast.success(`成功平仓 ${symbol}`);
      // Reload positions after closing
      await loadPositions();
    } catch (error: any) {
      console.error('Failed to close position:', error);
      toast.error(error.message || `平仓失败: ${symbol}`);
    } finally {
      setClosingSymbol(null);
    }
  };

  if (!enabled || marketType !== 'futures') {
    return (
      <Card>
        <CardHeader>
          <CardTitle>持仓列表</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">
            {marketType === 'spot' 
              ? '现货交易无持仓' 
              : '启用币安合约交易以查看持仓'}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>持仓列表</CardTitle>
        <Button onClick={loadPositions} disabled={loading} size="sm" variant="outline">
          {loading ? '加载中...' : '刷新'}
        </Button>
      </CardHeader>
      <CardContent>
        {error && (
          <div className="p-3 rounded-md bg-red-50 text-red-900 text-sm mb-4">
            {error}
          </div>
        )}

        {loading && positions.length === 0 && (
          <div className="text-center py-8 text-muted-foreground">
            正在加载持仓...
          </div>
        )}

        {!loading && positions.length === 0 && (
          <div className="text-center py-8 text-muted-foreground">
            暂无持仓
          </div>
        )}

        {positions.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b">
                <tr className="text-left">
                  <th className="pb-2 font-semibold">币种</th>
                  <th className="pb-2 font-semibold">方向</th>
                  <th className="pb-2 font-semibold text-right">数量</th>
                  <th className="pb-2 font-semibold text-right">开仓价</th>
                  <th className="pb-2 font-semibold text-right">标记价</th>
                  <th className="pb-2 font-semibold text-right">未实现盈亏</th>
                  <th className="pb-2 font-semibold text-right">杠杆</th>
                  <th className="pb-2 font-semibold text-center">操作</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((position, idx) => (
                  <tr key={idx} className="border-b last:border-b-0">
                    <td className="py-3 font-medium">{position.symbol}</td>
                    <td className="py-3">
                      <span className={`font-semibold ${
                        position.side === 'long' ? 'text-green-600' : 'text-red-600'
                      }`}>
                        {position.side === 'long' ? '多头' : '空头'}
                      </span>
                    </td>
                    <td className="py-3 text-right">{formatSize(position.size, extractBaseSymbol(position.symbol))}</td>
                    <td className="py-3 text-right">{formatPrice(position.entry_price, extractBaseSymbol(position.symbol))}</td>
                    <td className="py-3 text-right">{formatPrice(position.mark_price, extractBaseSymbol(position.symbol))}</td>
                    <td className="py-3 text-right">
                      <span className={`font-semibold ${
                        position.unrealized_pnl >= 0 ? 'text-green-600' : 'text-red-600'
                      }`}>
                        {formatPercentage(position.unrealized_pnl)}
                      </span>
                    </td>
                    <td className="py-3 text-right">{position.leverage}x</td>
                    <td className="py-3 text-center">
                      <Button
                        onClick={() => handleClosePosition(position.symbol)}
                        disabled={closingSymbol === position.symbol}
                        size="sm"
                        variant="destructive"
                        className="h-7 px-2"
                      >
                        {closingSymbol === position.symbol ? (
                          <span className="text-xs">平仓中...</span>
                        ) : (
                          <X className="w-3 h-3" />
                        )}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
