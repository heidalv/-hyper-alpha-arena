/**
 * Binance Balance Card Component
 * Displays account balance information
 */

import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { getBinanceBalance } from '@/lib/binanceApi';
import type { BinanceBalance } from '@/lib/types/binance';
import { getRefreshInterval } from '@/config/refresh';

interface BinanceBalanceCardProps {
  accountId: number;
  enabled: boolean;
  autoRefresh?: boolean; // 是否启用自动刷新，默认true
}

export default function BinanceBalanceCard({ accountId, enabled, autoRefresh = true }: BinanceBalanceCardProps) {
  const [balance, setBalance] = useState<BinanceBalance | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadBalance = async () => {
    if (!enabled) return;

    try {
      setLoading(true);
      setError(null);
      const data = await getBinanceBalance(accountId);
      setBalance(data);
    } catch (err: any) {
      console.error('Failed to load balance:', err);
      setError(err.message || 'Failed to load balance');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadBalance();

    // 自动刷新：使用统一配置（30秒）
    if (autoRefresh) {
      const interval = setInterval(() => {
        loadBalance();
      }, getRefreshInterval('binance_balance'));

      return () => clearInterval(interval);
    }
  }, [accountId, enabled, autoRefresh]);

  if (!enabled) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>账户余额</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">
            启用币安交易以查看余额
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>账户余额</CardTitle>
        <Button onClick={loadBalance} disabled={loading} size="sm" variant="outline">
          {loading ? '加载中...' : '刷新'}
        </Button>
      </CardHeader>
      <CardContent>
        {error && (
          <div className="p-3 rounded-md bg-red-50 text-red-900 text-sm mb-4">
            {error}
          </div>
        )}

        {loading && !balance && (
          <div className="text-center py-8 text-muted-foreground">
            正在加载余额...
          </div>
        )}

        {balance && (
          <div className="space-y-3">
            <div className="flex justify-between items-center">
              <span className="text-sm text-muted-foreground">总余额</span>
              <span className="font-semibold text-lg">
                {balance.total_balance.toFixed(2)} {balance.currency}
              </span>
            </div>

            <div className="flex justify-between items-center">
              <span className="text-sm text-muted-foreground">可用余额</span>
              <span className="text-green-600 font-medium">
                {balance.available_balance.toFixed(2)} {balance.currency}
              </span>
            </div>

            {balance.margin_used !== undefined && (
              <div className="flex justify-between items-center">
                <span className="text-sm text-muted-foreground">已用保证金</span>
                <span className="text-orange-600 font-medium">
                  {balance.margin_used.toFixed(2)} {balance.currency}
                </span>
              </div>
            )}

            {balance.frozen_balance !== undefined && balance.frozen_balance > 0 && (
              <div className="flex justify-between items-center">
                <span className="text-sm text-muted-foreground">冻结余额</span>
                <span className="text-gray-600 font-medium">
                  {balance.frozen_balance.toFixed(2)} {balance.currency}
                </span>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
