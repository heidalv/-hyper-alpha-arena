/**
 * 策略回测优化面板
 * 
 * 功能：一键优化、进度显示、每轮回测结果对比、AI建议展示
 */

import { useState, useEffect, useCallback }from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  BarChart3, Play, Loader2, CheckCircle2, XCircle,
  TrendingUp, Target, AlertTriangle, RefreshCw, Zap
} from 'lucide-react';
import { toast } from 'react-hot-toast';

interface OptLog {
  id: number;
  iteration: number;
  sharpe: number | null;
  win_rate: number | null;
  max_drawdown: number | null;
  total_return: number | null;
  profit_factor: number | null;
  passed: boolean;
  ai_suggestions: any;
  parameter_changes: any;
  status: string;
  created_at: string | null;
}

export default function BacktestOptimizePanel({ strategyId }: { strategyId: string }) {
  const [history, setHistory] = useState<OptLog[]>([]);
  const [optimizing, setOptimizing] = useState(false);
  const [optimizeResult, setOptimizeResult] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const loadHistory = useCallback(async () => {
    try {
      const res = await fetch(`/api/ai-strategies/${strategyId}/optimization-history?limit=20`);
      if (res.ok) setHistory(await res.json());
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [strategyId]);

  useEffect(() => { loadHistory(); }, [loadHistory]);

  const handleOptimize = async () => {
    setOptimizing(true);
    setOptimizeResult(null);
    const toastId = toast.loading('正在执行自主回测优化...');
    try {
      const res = await fetch(`/api/ai-strategies/${strategyId}/optimize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_iterations: 5, min_sharpe: 1.0, min_win_rate: 0.50, max_drawdown: 0.20 }),
        signal: AbortSignal.timeout(120000),
      });
      const data = await res.json();
      setOptimizeResult(data);
      if (data.passed) {
        toast.success(`优化成功! 共 ${data.iterations} 轮迭代`, { id: toastId });
      } else if (data.error) {
        toast.error(`优化失败: ${data.error}`, { id: toastId });
      } else {
        toast.error(`${data.iterations} 轮迭代后未达标`, { id: toastId });
      }
      loadHistory();
    } catch (e: any) {
      toast.error(e?.name === 'AbortError' ? '优化超时' : '优化失败', { id: toastId });
    } finally {
      setOptimizing(false);
    }
  };

  const metricCell = (val: number | null, good: (v: number) => boolean, fmt: (v: number) => string) => {
    if (val === null || val === undefined) return <span className="text-gray-400">-</span>;
    return <span className={good(val) ? 'text-green-600 font-medium' : 'text-red-600'}>{fmt(val)}</span>;
  };

  return (
    <div className="space-y-4">
      {/* 操作区 */}
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-base">
              <Target className="w-4 h-4 text-indigo-500" />
              自主回测优化
            </CardTitle>
            <Button
              onClick={handleOptimize}
              disabled={optimizing}
              size="sm"
              className="bg-indigo-600 hover:bg-indigo-700"
            >
              {optimizing ? (
                <><Loader2 className="w-4 h-4 mr-1.5 animate-spin" />优化中...</>
              ) : (
                <><Play className="w-4 h-4 mr-1.5" />开始优化</>
              )}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-4 gap-3 text-center text-xs">
            <div className="bg-muted/50 rounded p-2">
              <div className="text-muted-foreground">目标夏普</div>
              <div className="font-bold">≥ 1.0</div>
            </div>
            <div className="bg-muted/50 rounded p-2">
              <div className="text-muted-foreground">目标胜率</div>
              <div className="font-bold">≥ 50%</div>
            </div>
            <div className="bg-muted/50 rounded p-2">
              <div className="text-muted-foreground">目标回撤</div>
              <div className="font-bold">≤ 20%</div>
            </div>
            <div className="bg-muted/50 rounded p-2">
              <div className="text-muted-foreground">目标盈亏比</div>
              <div className="font-bold">≥ 1.2</div>
            </div>
          </div>

          {/* 实时结果 */}
          {optimizeResult && !optimizeResult.error && (
            <div className={`mt-4 rounded-lg p-3 ${optimizeResult.passed ? 'bg-green-50 dark:bg-green-950/20' : 'bg-yellow-50 dark:bg-yellow-950/20'}`}>
              <div className="flex items-center gap-2 mb-2">
                {optimizeResult.passed ? (
                  <><CheckCircle2 className="w-4 h-4 text-green-600" /><span className="text-sm font-medium text-green-700">优化成功</span></>
                ) : (
                  <><AlertTriangle className="w-4 h-4 text-yellow-600" /><span className="text-sm font-medium text-yellow-700">未达标</span></>
                )}
                <span className="text-xs text-muted-foreground">共 {optimizeResult.iterations} 轮迭代</span>
              </div>
              {optimizeResult.results?.length > 0 && (() => {
                const last = optimizeResult.results[optimizeResult.results.length - 1];
                return (
                  <div className="grid grid-cols-4 gap-2 text-xs">
                    <div>夏普: <span className="font-bold">{last.sharpe?.toFixed(2)}</span></div>
                    <div>胜率: <span className="font-bold">{((last.win_rate || 0) * 100).toFixed(1)}%</span></div>
                    <div>回撤: <span className="font-bold">{((last.max_drawdown || 0) * 100).toFixed(1)}%</span></div>
                    <div>盈亏比: <span className="font-bold">{last.profit_factor?.toFixed(2)}</span></div>
                  </div>
                );
              })()}
            </div>
          )}
        </CardContent>
      </Card>

      {/* 历史记录 */}
      {history.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              <BarChart3 className="w-4 h-4" />
              优化历史
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-muted-foreground border-b">
                    <th className="text-left py-1.5 pr-2">#</th>
                    <th className="text-right py-1.5 px-2">夏普</th>
                    <th className="text-right py-1.5 px-2">胜率</th>
                    <th className="text-right py-1.5 px-2">回撤</th>
                    <th className="text-right py-1.5 px-2">收益</th>
                    <th className="text-right py-1.5 px-2">盈亏比</th>
                    <th className="text-center py-1.5 px-2">结果</th>
                    <th className="text-left py-1.5 pl-2">AI建议</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map(log => (
                    <tr key={log.id} className="border-b border-dashed hover:bg-muted/30">
                      <td className="py-1.5 pr-2 font-mono">{log.iteration}</td>
                      <td className="text-right px-2">{metricCell(log.sharpe, v => v >= 1.0, v => v.toFixed(2))}</td>
                      <td className="text-right px-2">{metricCell(log.win_rate, v => v >= 0.5, v => `${(v*100).toFixed(0)}%`)}</td>
                      <td className="text-right px-2">{metricCell(log.max_drawdown, v => v <= 0.2, v => `${(v*100).toFixed(1)}%`)}</td>
                      <td className="text-right px-2">{metricCell(log.total_return, v => v > 0, v => `${(v*100).toFixed(1)}%`)}</td>
                      <td className="text-right px-2">{metricCell(log.profit_factor, v => v >= 1.2, v => v.toFixed(2))}</td>
                      <td className="text-center px-2">
                        {log.passed ? (
                          <CheckCircle2 className="w-3.5 h-3.5 text-green-500 mx-auto" />
                        ) : (
                          <XCircle className="w-3.5 h-3.5 text-red-400 mx-auto" />
                        )}
                      </td>
                      <td className="pl-2 text-muted-foreground max-w-[200px] truncate">
                        {log.ai_suggestions?.analysis || '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
