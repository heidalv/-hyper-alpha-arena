/**
 * 策略学习仪表盘
 * 
 * 显示策略记忆、提示词进化历史、市场状态表现、关键教训
 */

import { useState, useEffect, useCallback }from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Brain, BookOpen, TrendingUp, TrendingDown, RefreshCw,
  Loader2, AlertTriangle, CheckCircle2, Zap, Lightbulb,
  BarChart3, Clock, GitBranch
} from 'lucide-react';
import { toast } from 'react-hot-toast';
import { fmtShortDateTime } from '@/lib/utils';

interface LearningDashboardData {
  memory: {
    total_trades: number;
    win_rate: number;
    sharpe_ratio: number;
    max_drawdown: number;
    performance_by_regime: Record<string, { trades: number; wins: number; total_pnl: number; win_rate?: number }>;
    successful_patterns: any[];
    failed_patterns: any[];
    key_lessons: any[];
  } | null;
  prompt_evolution: Array<{
    id: number;
    training_metrics: any;
    created_at: string | null;
  }>;
  recent_trades: Array<{
    id: number;
    symbol: string;
    side: string;
    entry_price: number;
    exit_price: number | null;
    pnl_pct: number | null;
    status: string;
    decision_quality_score: number | null;
    opened_at: string | null;
    closed_at: string | null;
  }>;
}

export default function LearningDashboard({ strategyId }: { strategyId: string }) {
  const [data, setData] = useState<LearningDashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [reviewing, setReviewing] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const res = await fetch(`/api/ai-strategies/${strategyId}/learning-dashboard`);
      if (res.ok) setData(await res.json());
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [strategyId]);

  useEffect(() => { loadData(); }, [loadData]);

  const handleReview = async () => {
    setReviewing(true);
    const toastId = toast.loading('正在执行学习复盘...');
    try {
      const res = await fetch(`/api/ai-strategies/${strategyId}/learn?days=7`, { method: 'POST' });
      const result = await res.json();
      if (result.error) {
        toast.error(`复盘失败: ${result.error}`, { id: toastId });
      } else {
        toast.success(
          `复盘完成! 分析了 ${result.total_trades_analyzed} 笔交易, 提取 ${result.lessons_extracted} 条教训`,
          { id: toastId }
        );
        loadData();
      }
    } catch { toast.error('复盘失败', { id: toastId }); }
    finally { setReviewing(false); }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="w-5 h-5 animate-spin text-purple-500 mr-2" />
        <span className="text-sm text-muted-foreground">加载学习数据...</span>
      </div>
    );
  }

  const memory = data?.memory;

  return (
    <div className="space-y-4">
      {/* 操作栏 */}
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-base">
              <Brain className="w-4 h-4 text-purple-500" />
              策略自学习
            </CardTitle>
            <Button
              onClick={handleReview}
              disabled={reviewing}
              size="sm"
              variant="outline"
            >
              {reviewing ? (
                <><Loader2 className="w-4 h-4 mr-1.5 animate-spin" />复盘中...</>
              ) : (
                <><Lightbulb className="w-4 h-4 mr-1.5" />立即复盘</>
              )}
            </Button>
          </div>
        </CardHeader>
      </Card>

      {/* 记忆摘要 */}
      {memory && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Card>
            <CardContent className="p-3 text-center">
              <div className="text-[10px] text-muted-foreground">总交易</div>
              <div className="text-xl font-bold">{memory.total_trades}</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-3 text-center">
              <div className="text-[10px] text-muted-foreground">胜率</div>
              <div className={`text-xl font-bold ${memory.win_rate >= 0.5 ? 'text-green-600' : 'text-red-600'}`}>
                {(memory.win_rate * 100).toFixed(1)}%
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-3 text-center">
              <div className="text-[10px] text-muted-foreground">夏普</div>
              <div className={`text-xl font-bold ${memory.sharpe_ratio >= 1 ? 'text-green-600' : ''}`}>
                {memory.sharpe_ratio.toFixed(2)}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-3 text-center">
              <div className="text-[10px] text-muted-foreground">回撤</div>
              <div className="text-xl font-bold text-red-600">{(memory.max_drawdown * 100).toFixed(1)}%</div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* 市场状态表现热力图 */}
      {memory?.performance_by_regime && Object.keys(memory.performance_by_regime).length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              <BarChart3 className="w-4 h-4" />
              按市场状态表现
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {Object.entries(memory.performance_by_regime).map(([regime, perf]) => {
                const wr = perf.trades > 0 ? (perf.wins / perf.trades) : 0;
                const pnlColor = perf.total_pnl >= 0 ? 'text-green-600' : 'text-red-600';
                return (
                  <div key={regime} className={`border rounded-lg p-3 ${wr >= 0.5 ? 'bg-green-50/50 dark:bg-green-950/10' : 'bg-red-50/50 dark:bg-red-950/10'}`}>
                    <div className="text-xs font-medium mb-1 capitalize">{regime}</div>
                    <div className="flex justify-between text-sm">
                      <span>{perf.trades} 笔</span>
                      <span className={wr >= 0.5 ? 'text-green-600 font-medium' : 'text-red-600'}>
                        WR {(wr * 100).toFixed(0)}%
                      </span>
                    </div>
                    <div className={`text-xs mt-1 ${pnlColor}`}>
                      PnL: {perf.total_pnl >= 0 ? '+' : ''}{(perf.total_pnl * 100).toFixed(2)}%
                    </div>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* 关键教训 */}
      {memory?.key_lessons && memory.key_lessons.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-yellow-500" />
              关键教训
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {memory.key_lessons.map((lesson: any, i: number) => (
              <div key={i} className="flex items-start gap-2 text-sm bg-yellow-50 dark:bg-yellow-950/20 rounded p-2.5">
                <Badge variant="outline" className={`text-[10px] shrink-0 ${
                  lesson.severity === 'high' ? 'border-red-300 text-red-700' : 'border-yellow-300 text-yellow-700'
                }`}>
                  {lesson.severity === 'high' ? '严重' : '注意'}
                </Badge>
                <span className="text-gray-700 dark:text-gray-300">{lesson.message}</span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* 提示词进化历史 */}
      {data?.prompt_evolution && data.prompt_evolution.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              <GitBranch className="w-4 h-4 text-blue-500" />
              提示词进化历史
              <span className="text-xs font-normal text-muted-foreground">共 {data.prompt_evolution.length} 次进化</span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {data.prompt_evolution.map((evo, i) => {
                let metrics: any = {};
                try {
                  metrics = typeof evo.training_metrics === 'string' ? JSON.parse(evo.training_metrics) : evo.training_metrics || {};
                } catch { /* ignore */ }
                return (
                  <div key={evo.id} className="flex items-center gap-3 text-xs bg-muted/40 rounded p-2">
                    <span className="text-muted-foreground font-mono">v{data.prompt_evolution.length - i}</span>
                    <span className="text-muted-foreground">{fmtShortDateTime(evo.created_at)}</span>
                    <span className="flex-1 truncate text-muted-foreground">
                      {metrics.evolution_context?.slice(0, 80) || '初始版本'}
                    </span>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* 最近交易 */}
      {data?.recent_trades && data.recent_trades.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              <BookOpen className="w-4 h-4" />
              最近交易记录
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="max-h-[240px] overflow-y-auto space-y-1">
              {data.recent_trades.map(t => (
                <div key={t.id} className="flex items-center gap-2 text-xs py-1.5 px-2 rounded hover:bg-muted/50">
                  <Badge variant="outline" className={`text-[10px] ${t.side === 'buy' || t.side === 'long' ? 'text-green-700' : 'text-red-700'}`}>
                    {t.side === 'buy' || t.side === 'long' ? '多' : '空'}
                  </Badge>
                  <span className="w-12">{t.symbol}</span>
                  <span className="text-muted-foreground w-20">${t.entry_price?.toLocaleString()}</span>
                  {t.pnl_pct !== null && t.pnl_pct !== undefined ? (
                    <span className={`font-medium w-16 text-right ${t.pnl_pct >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {t.pnl_pct >= 0 ? '+' : ''}{(t.pnl_pct * 100).toFixed(2)}%
                    </span>
                  ) : (
                    <span className="w-16 text-right text-muted-foreground">{t.status}</span>
                  )}
                  {t.decision_quality_score !== null && (
                    <span className="text-muted-foreground">Q:{t.decision_quality_score}</span>
                  )}
                  <span className="ml-auto text-muted-foreground">{fmtShortDateTime(t.opened_at)}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* 空状态 */}
      {!memory && (!data?.recent_trades || data.recent_trades.length === 0) && (
        <Card className="border-dashed">
          <CardContent className="py-8 text-center text-muted-foreground">
            <Brain className="w-8 h-8 mx-auto mb-3 text-gray-400" />
            <p className="text-sm">暂无学习数据</p>
            <p className="text-xs mt-1">当策略执行交易后，系统将自动积累学习数据</p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
