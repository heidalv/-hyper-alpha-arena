/**
 * 持仓追踪面板
 * 
 * 实时PnL、动态TP/SL可视化、健康度指示、手动干预按钮
 */

import { useState, useEffect, useCallback }from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Shield, TrendingUp, TrendingDown, RefreshCw, Loader2,
  AlertTriangle, XCircle, Heart, ArrowUpDown, Clock
} from 'lucide-react';
import { toast } from 'react-hot-toast';
import { parseUTC } from '@/lib/utils';

interface TrackedPosition {
  strategy_id: string;
  trade_id: number | null;
  symbol: string;
  side: string;
  entry_price: number;
  current_price: number;
  quantity: number;
  leverage: number;
  stop_loss_price: number;
  take_profit_price: number;
  original_sl: number;
  original_tp: number;
  trailing_active: boolean;
  highest_price: number;
  lowest_price: number;
  pnl_pct: number;
  opened_at: string;
  last_check_at: string;
  health_score: number;
  alerts: string[];
}

function localTime(iso: string) {
  if (!iso) return '-';
  const d = parseUTC(iso);
  if (!d || isNaN(d.getTime())) return iso;
  return d.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
  });
}

function healthColor(score: number) {
  if (score >= 0.7) return 'text-green-500';
  if (score >= 0.4) return 'text-yellow-500';
  return 'text-red-500';
}

function healthBg(score: number) {
  if (score >= 0.7) return 'bg-green-50 dark:bg-green-950/20 border-green-200 dark:border-green-800';
  if (score >= 0.4) return 'bg-yellow-50 dark:bg-yellow-950/20 border-yellow-200 dark:border-yellow-800';
  return 'bg-red-50 dark:bg-red-950/20 border-red-200 dark:border-red-800';
}

export default function PositionTrackerPanel({ strategyId }: { strategyId?: string }) {
  const [positions, setPositions] = useState<TrackedPosition[]>([]);
  const [loading, setLoading] = useState(true);

  const loadPositions = useCallback(async () => {
    try {
      const res = await fetch('/api/ai-strategies/position-tracker/all');
      if (res.ok) {
        const data: TrackedPosition[] = await res.json();
        setPositions(strategyId ? data.filter(p => p.strategy_id === strategyId) : data);
      }
    } catch (e) {
      console.error('Load positions error:', e);
    } finally {
      setLoading(false);
    }
  }, [strategyId]);

  useEffect(() => {
    loadPositions();
    const timer = setInterval(loadPositions, 8000);
    return () => clearInterval(timer);
  }, [loadPositions]);

  const handleForceClose = async (pos: TrackedPosition) => {
    if (!confirm(`确定要停止追踪 ${pos.symbol} 吗？你需要手动在交易所平仓。`)) return;
    try {
      const res = await fetch(`/api/ai-strategies/position-tracker/${pos.strategy_id}/${pos.symbol}/close`, { method: 'POST' });
      if (res.ok) {
        toast.success(`已停止追踪 ${pos.symbol}`);
        loadPositions();
      }
    } catch { toast.error('操作失败'); }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="w-5 h-5 animate-spin text-blue-500 mr-2" />
        <span className="text-sm text-muted-foreground">加载持仓追踪数据...</span>
      </div>
    );
  }

  if (positions.length === 0) {
    return (
      <Card className="border-dashed">
        <CardContent className="py-8 text-center text-muted-foreground">
          <ArrowUpDown className="w-8 h-8 mx-auto mb-3 text-gray-400" />
          <p className="text-sm">暂无追踪中的持仓</p>
          <p className="text-xs mt-1">当AI策略执行下单后，持仓将自动注册到追踪系统</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-3">
      {positions.map(pos => {
        const pnlColor = pos.pnl_pct >= 0 ? 'text-green-600' : 'text-red-600';
        const pnlBg = pos.pnl_pct >= 0 ? 'bg-green-50 dark:bg-green-950/20' : 'bg-red-50 dark:bg-red-950/20';
        return (
          <Card key={`${pos.strategy_id}:${pos.symbol}`} className={`border ${healthBg(pos.health_score)}`}>
            <CardContent className="p-4">
              {/* 头部 */}
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Badge className={pos.side === 'buy'
                    ? 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-400'
                    : 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-400'
                  }>
                    {pos.side === 'buy' ? '做多' : '做空'} {pos.symbol}
                  </Badge>
                  {pos.leverage > 1 && <span className="text-xs text-muted-foreground">{pos.leverage}x</span>}
                  {pos.trailing_active && (
                    <Badge variant="outline" className="text-[10px] text-purple-600">
                      <Shield className="w-3 h-3 mr-0.5" />移动止损
                    </Badge>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <span className={`flex items-center gap-1 text-xs ${healthColor(pos.health_score)}`}>
                    <Heart className="w-3 h-3" />{(pos.health_score * 100).toFixed(0)}%
                  </span>
                  <Button variant="ghost" size="sm" className="h-7 text-red-500 hover:text-red-700" onClick={() => handleForceClose(pos)}>
                    <XCircle className="w-4 h-4" />
                  </Button>
                </div>
              </div>

              {/* 指标网格 */}
              <div className="grid grid-cols-5 gap-3 text-center">
                <div>
                  <div className="text-[10px] text-muted-foreground">入场</div>
                  <div className="text-sm font-mono">${pos.entry_price.toLocaleString()}</div>
                </div>
                <div>
                  <div className="text-[10px] text-muted-foreground">当前</div>
                  <div className="text-sm font-mono font-bold">${pos.current_price.toLocaleString()}</div>
                </div>
                <div className={pnlBg + ' rounded p-1'}>
                  <div className="text-[10px] text-muted-foreground">PnL</div>
                  <div className={`text-sm font-bold ${pnlColor}`}>
                    {pos.pnl_pct >= 0 ? '+' : ''}{(pos.pnl_pct * 100).toFixed(2)}%
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-muted-foreground">止损</div>
                  <div className="text-sm font-mono text-red-600">
                    {pos.stop_loss_price > 0 ? `$${pos.stop_loss_price.toLocaleString()}` : '-'}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] text-muted-foreground">止盈</div>
                  <div className="text-sm font-mono text-green-600">
                    {pos.take_profit_price > 0 ? `$${pos.take_profit_price.toLocaleString()}` : '-'}
                  </div>
                </div>
              </div>

              {/* 警告信息 */}
              {pos.alerts.length > 0 && (
                <div className="mt-3 space-y-1">
                  {pos.alerts.map((alert, i) => (
                    <div key={i} className="flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-950/20 rounded px-2 py-1">
                      <AlertTriangle className="w-3 h-3 shrink-0" />
                      {alert}
                    </div>
                  ))}
                </div>
              )}

              {/* 底部信息 */}
              <div className="flex items-center justify-between mt-3 text-[10px] text-muted-foreground">
                <span><Clock className="w-3 h-3 inline mr-0.5" />开仓: {localTime(pos.opened_at)}</span>
                <span>更新: {localTime(pos.last_check_at)}</span>
              </div>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
