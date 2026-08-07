/**
 * 策略自主运行监控面板
 * 
 * 显示三周期分析状态、AI决策日志流、信号时间线
 */

import { useState, useEffect, useCallback }from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Activity, TrendingUp, TrendingDown, RefreshCw, Loader2,
  Clock, BarChart3, Zap, Eye, ArrowUpRight, ArrowDownRight,
  Minus, Radio, CircleDot
} from 'lucide-react';
import { parseUTC } from '@/lib/utils';

interface AnalysisView {
  trend: string;
  strength: number;
  volatility: string;
  signal: number;
  price: number;
  time: string;
}

interface AutonomousStatus {
  strategy_id: string;
  auto_mode: string;
  intervals: { short: number; mid: number; long: number };
  symbols: string[];
  last_analyses: Record<string, AnalysisView>;
  running: boolean;
}

interface AnalysisLog {
  id: number;
  analysis_type: string;
  symbol: string;
  market_cycle: string | null;
  volatility_regime: string | null;
  trend_direction: string | null;
  trend_strength: number | null;
  current_price: number | null;
  decision_made: string | null;
  decision_details: any;
  created_at: string | null;
}

const trendIcon = (d: string) => {
  if (d === 'bullish') return <TrendingUp className="w-4 h-4 text-green-500" />;
  if (d === 'bearish') return <TrendingDown className="w-4 h-4 text-red-500" />;
  return <Minus className="w-4 h-4 text-gray-400" />;
};

const signalBar = (v: number) => {
  const pct = Math.abs(v) * 100;
  const color = v > 0 ? 'bg-green-500' : v < 0 ? 'bg-red-500' : 'bg-gray-300';
  return (
    <div className="flex items-center gap-2 w-full">
      <div className="flex-1 h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full transition-all`} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
      <span className={`text-xs font-mono ${v > 0 ? 'text-green-600' : v < 0 ? 'text-red-600' : 'text-gray-500'}`}>
        {v > 0 ? '+' : ''}{v.toFixed(3)}
      </span>
    </div>
  );
};

const modeLabel: Record<string, { label: string; color: string }> = {
  full_auto: { label: '全自动', color: 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-400' },
  semi_auto: { label: '半自动', color: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-400' },
  signal_only: { label: '仅信号', color: 'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-400' },
};

const typeLabels: Record<string, string> = {
  short: '短线', mid: '波段', long: '长线', synthesize: '综合',
};

function localTime(utcStr: string | null) {
  if (!utcStr) return '-';
  const normalized = utcStr.trim().replace(' UTC', 'Z').replace(' ', 'T');
  const d = parseUTC(normalized);
  if (!d || isNaN(d.getTime())) return utcStr;
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

export default function StrategyMonitorPanel({ strategyId }: { strategyId: string }) {
  const [status, setStatus] = useState<AutonomousStatus | null>(null);
  const [logs, setLogs] = useState<AnalysisLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const [statusRes, logsRes] = await Promise.all([
        fetch(`/api/ai-strategies/autonomous/${strategyId}/status`),
        fetch(`/api/ai-strategies/autonomous/${strategyId}/logs?limit=30`),
      ]);
      if (statusRes.ok) setStatus(await statusRes.json());
      else setStatus(null);
      if (logsRes.ok) setLogs(await logsRes.json());
    } catch (e) {
      console.error('Monitor load error:', e);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [strategyId]);

  useEffect(() => {
    loadData();
    const timer = setInterval(loadData, 8000);
    return () => clearInterval(timer);
  }, [loadData]);

  const handleRefresh = () => {
    setRefreshing(true);
    loadData();
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-6 h-6 animate-spin text-blue-500 mr-2" />
        <span className="text-sm text-muted-foreground">加载监控数据...</span>
      </div>
    );
  }

  if (!status) {
    return (
      <Card className="border-dashed">
        <CardContent className="py-8 text-center text-muted-foreground">
          <Radio className="w-8 h-8 mx-auto mb-3 text-gray-400" />
          <p className="text-sm">策略未在自主循环中运行</p>
          <p className="text-xs mt-1">请先激活策略，系统将自动注册到自主分析循环</p>
        </CardContent>
      </Card>
    );
  }

  const mode = modeLabel[status.auto_mode] || modeLabel.semi_auto;
  const analyses = status.last_analyses;

  return (
    <div className="space-y-4">
      {/* 运行状态头 */}
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-base">
              <CircleDot className="w-4 h-4 text-green-500 animate-pulse" />
              自主运行监控
            </CardTitle>
            <div className="flex items-center gap-2">
              <Badge className={mode.color}>{mode.label}</Badge>
              <Button variant="ghost" size="sm" onClick={handleRefresh} disabled={refreshing}>
                <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-3">
            {(['short', 'mid', 'long'] as const).map(type => {
              const a = analyses[type];
              const interval = status.intervals[type];
              return (
                <div key={type} className="bg-muted/50 rounded-lg p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-medium text-muted-foreground uppercase">{typeLabels[type]}</span>
                    <span className="text-[10px] text-muted-foreground">
                      <Clock className="w-3 h-3 inline mr-0.5" />{Math.floor(interval / 60)}min
                    </span>
                  </div>
                  {a ? (
                    <>
                      <div className="flex items-center gap-1.5">
                        {trendIcon(a.trend)}
                        <span className="text-sm font-medium capitalize">{a.trend}</span>
                        <span className="text-xs text-muted-foreground">({(a.strength * 100).toFixed(0)}%)</span>
                      </div>
                      {signalBar(a.signal)}
                      <div className="flex items-center justify-between text-[10px] text-muted-foreground">
                        <span>{a.volatility}</span>
                        <span>${a.price?.toLocaleString()}</span>
                      </div>
                      <div className="text-[10px] text-muted-foreground">{localTime(a.time)}</div>
                    </>
                  ) : (
                    <div className="text-xs text-muted-foreground py-2">等待首次分析...</div>
                  )}
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* 分析日志 */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <BarChart3 className="w-4 h-4 text-blue-500" />
            分析日志
            <span className="text-xs font-normal text-muted-foreground ml-1">最近 {logs.length} 条</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {logs.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4 text-center">暂无分析日志</p>
          ) : (
            <div className="max-h-[360px] overflow-y-auto space-y-1.5 pr-1">
              {logs.map(log => (
                <div key={log.id} className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-muted/50 text-xs transition-colors">
                  <Badge variant="outline" className="text-[10px] min-w-[40px] justify-center">
                    {typeLabels[log.analysis_type] || log.analysis_type}
                  </Badge>
                  <span className="text-muted-foreground w-12 shrink-0">{log.symbol}</span>
                  <span className="flex items-center gap-1 w-16 shrink-0">
                    {trendIcon(log.trend_direction || 'neutral')}
                    <span className="capitalize">{log.trend_direction || '-'}</span>
                  </span>
                  {log.current_price && log.current_price > 0 ? (
                    <span className="text-muted-foreground w-20 text-right shrink-0">${log.current_price.toLocaleString()}</span>
                  ) : <span className="w-20 shrink-0" />}
                  {log.decision_made === 'trade' ? (
                    <Badge className="bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400 text-[10px]">
                      <Zap className="w-3 h-3 mr-0.5" />交易
                    </Badge>
                  ) : log.decision_made === 'hold' ? (
                    <Badge variant="outline" className="text-[10px]">
                      <Eye className="w-3 h-3 mr-0.5" />观望
                    </Badge>
                  ) : null}
                  <span className="ml-auto text-muted-foreground text-[10px]">
                    {localTime(log.created_at)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
