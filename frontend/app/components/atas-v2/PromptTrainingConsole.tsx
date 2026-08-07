/**
 * AI 自学习系统 (Self-Learning System)
 *
 * 全局视图：
 * - 所有策略的学习状态总览
 * - 综合胜率、夏普、回撤等核心指标
 * - 市场状态表现矩阵（跨策略）
 * - 关键教训汇总
 * - 提示词进化历史
 * - 一键批量复盘
 */

import { useState, useEffect, useCallback }from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Brain, Lightbulb, RefreshCw, Loader2,
  TrendingUp, TrendingDown, AlertTriangle,
  BarChart3, BookOpen, GitBranch, Target,
  CheckCircle2, XCircle, Zap, GraduationCap,
  Play, ArrowRight
} from 'lucide-react';
import { toast } from 'react-hot-toast';
import { fmtShortDateTime, parseUTC } from '@/lib/utils';

interface StrategyBasic {
  id: number;
  strategy_id: string;
  name: string;
  status: string;
  learning_enabled: boolean;
  primary_symbol: string;
  timeframe: string;
}

interface LearningData {
  memory: {
    total_trades: number;
    win_rate: number;
    sharpe_ratio: number;
    max_drawdown: number;
    performance_by_regime: Record<string, { trades: number; wins: number; total_pnl: number }>;
    key_lessons: any[];
  } | null;
  prompt_evolution: Array<{ id: number; training_metrics: any; created_at: string | null }>;
  recent_trades: Array<{
    id: number; symbol: string; side: string; entry_price: number;
    pnl_pct: number | null; status: string; decision_quality_score: number | null;
    opened_at: string | null;
  }>;
}

interface StrategyLearning {
  strategy: StrategyBasic;
  data: LearningData | null;
  loading: boolean;
}

export default function PromptTrainingConsole() {
  const [strategies, setStrategies] = useState<StrategyBasic[]>([]);
  const [learningMap, setLearningMap] = useState<Record<string, LearningData | null>>({});
  const [loading, setLoading] = useState(true);
  const [reviewingIds, setReviewingIds] = useState<Set<string>>(new Set());
  const [batchReviewing, setBatchReviewing] = useState(false);
  const [selectedSid, setSelectedSid] = useState<string | null>(null);

  const loadStrategies = useCallback(async () => {
    try {
      const res = await fetch('/api/ai-strategies', { signal: AbortSignal.timeout(5000) });
      if (!res.ok) return;
      const data = await res.json();
      const list: StrategyBasic[] = (Array.isArray(data) ? data : []).filter(
        (s: any) => s.status === 'active' || s.learning_enabled
      );
      setStrategies(list);
      return list;
    } catch {
      return [];
    }
  }, []);

  const loadLearning = useCallback(async (strategyId: string) => {
    try {
      const res = await fetch(`/api/ai-strategies/${strategyId}/learning-dashboard`, {
        signal: AbortSignal.timeout(5000),
      });
      if (res.ok) {
        const d = await res.json();
        setLearningMap(prev => ({ ...prev, [strategyId]: d }));
        return d;
      }
    } catch { /* ignore */ }
    setLearningMap(prev => ({ ...prev, [strategyId]: null }));
    return null;
  }, []);

  useEffect(() => {
    (async () => {
      setLoading(true);
      const list = await loadStrategies();
      if (list && list.length > 0) {
        await Promise.all(list.map(s => loadLearning(s.strategy_id)));
        setSelectedSid(list[0].strategy_id);
      }
      setLoading(false);
    })();
  }, []);

  const handleReview = async (strategyId: string) => {
    setReviewingIds(prev => new Set(prev).add(strategyId));
    const tid = toast.loading(`复盘 ${strategyId}...`);
    try {
      const res = await fetch(`/api/ai-strategies/${strategyId}/learn?days=7`, { method: 'POST' });
      const result = await res.json();
      if (result.error) {
        toast.error(`复盘失败: ${result.error}`, { id: tid });
      } else {
        toast.success(
          `完成! 分析 ${result.total_trades_analyzed} 笔, 提取 ${result.lessons_extracted} 条教训`,
          { id: tid }
        );
        await loadLearning(strategyId);
      }
    } catch { toast.error('复盘失败', { id: tid }); }
    finally {
      setReviewingIds(prev => { const n = new Set(prev); n.delete(strategyId); return n; });
    }
  };

  const handleBatchReview = async () => {
    setBatchReviewing(true);
    const active = strategies.filter(s => s.status === 'active');
    const tid = toast.loading(`批量复盘 ${active.length} 个策略...`);
    let ok = 0, fail = 0;
    for (const s of active) {
      try {
        const res = await fetch(`/api/ai-strategies/${s.strategy_id}/learn?days=7`, { method: 'POST' });
        const r = await res.json();
        if (!r.error) ok++; else fail++;
      } catch { fail++; }
    }
    toast.success(`批量复盘完成: ${ok}成功, ${fail}失败`, { id: tid });
    for (const s of active) await loadLearning(s.strategy_id);
    setBatchReviewing(false);
  };

  // ── 汇总计算 ──
  const allMemories = strategies
    .map(s => learningMap[s.strategy_id]?.memory)
    .filter(Boolean) as NonNullable<LearningData['memory']>[];

  const totalTrades = allMemories.reduce((s, m) => s + m.total_trades, 0);
  const avgWinRate = allMemories.length > 0
    ? allMemories.reduce((s, m) => s + m.win_rate * m.total_trades, 0) / Math.max(totalTrades, 1) : 0;
  const avgSharpe = allMemories.length > 0
    ? allMemories.reduce((s, m) => s + m.sharpe_ratio, 0) / allMemories.length : 0;
  const maxDD = allMemories.length > 0
    ? Math.max(...allMemories.map(m => m.max_drawdown)) : 0;

  // 合并市场状态表现
  const mergedRegime: Record<string, { trades: number; wins: number; total_pnl: number }> = {};
  for (const mem of allMemories) {
    for (const [regime, perf] of Object.entries(mem.performance_by_regime || {})) {
      if (!mergedRegime[regime]) mergedRegime[regime] = { trades: 0, wins: 0, total_pnl: 0 };
      mergedRegime[regime].trades += perf.trades;
      mergedRegime[regime].wins += perf.wins;
      mergedRegime[regime].total_pnl += perf.total_pnl;
    }
  }

  // 合并教训
  const allLessons = allMemories.flatMap(m => m.key_lessons || []).slice(0, 10);

  // 合并进化历史
  const allEvolutions = strategies.flatMap(s => {
    const d = learningMap[s.strategy_id];
    return (d?.prompt_evolution || []).map(e => ({ ...e, strategyName: s.name, strategyId: s.strategy_id }));
  }).sort((a, b) => {
    const ta = a.created_at ? (parseUTC(a.created_at)?.getTime() || 0) : 0;
    const tb = b.created_at ? (parseUTC(b.created_at)?.getTime() || 0) : 0;
    return tb - ta;
  }).slice(0, 20);

  // 合并最近交易
  const allTrades = strategies.flatMap(s => {
    const d = learningMap[s.strategy_id];
    return (d?.recent_trades || []).map(t => ({ ...t, strategyName: s.name }));
  }).sort((a, b) => {
    const ta = a.opened_at ? (parseUTC(a.opened_at)?.getTime() || 0) : 0;
    const tb = b.opened_at ? (parseUTC(b.opened_at)?.getTime() || 0) : 0;
    return tb - ta;
  }).slice(0, 30);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-6 h-6 animate-spin text-purple-500 mr-3" />
        <span className="text-muted-foreground">加载自学习数据...</span>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      {/* ===== 标题 ===== */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-purple-500 to-pink-600 flex items-center justify-center">
            <GraduationCap className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-bold">AI 自学习系统</h1>
            <p className="text-xs text-muted-foreground">
              全策略学习复盘 · 市场环境适应 · 提示词自进化 · 交易记忆沉淀
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline" size="sm"
            onClick={() => { loadStrategies().then(list => list && Promise.all(list.map(s => loadLearning(s.strategy_id)))); }}
          >
            <RefreshCw className="w-4 h-4 mr-1.5" />刷新
          </Button>
          <Button
            size="sm"
            onClick={handleBatchReview}
            disabled={batchReviewing || strategies.filter(s => s.status === 'active').length === 0}
            className="bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-700 hover:to-pink-700"
          >
            {batchReviewing
              ? <><Loader2 className="w-4 h-4 mr-1.5 animate-spin" />复盘中...</>
              : <><Play className="w-4 h-4 mr-1.5" />一键批量复盘</>
            }
          </Button>
        </div>
      </div>

      {/* ===== 全局指标 ===== */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Card>
          <CardContent className="p-4 text-center">
            <div className="text-[10px] text-muted-foreground mb-1">学习策略数</div>
            <div className="text-2xl font-bold">{strategies.length}</div>
            <div className="text-[10px] text-green-600">{strategies.filter(s => s.status === 'active').length} 活跃</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 text-center">
            <div className="text-[10px] text-muted-foreground mb-1">总交易笔数</div>
            <div className="text-2xl font-bold">{totalTrades}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 text-center">
            <div className="text-[10px] text-muted-foreground mb-1">综合胜率</div>
            <div className={`text-2xl font-bold ${avgWinRate >= 0.5 ? 'text-green-600' : avgWinRate > 0 ? 'text-yellow-600' : 'text-muted-foreground'}`}>
              {totalTrades > 0 ? `${(avgWinRate * 100).toFixed(1)}%` : '--'}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 text-center">
            <div className="text-[10px] text-muted-foreground mb-1">平均夏普</div>
            <div className={`text-2xl font-bold ${avgSharpe >= 1 ? 'text-green-600' : ''}`}>
              {allMemories.length > 0 ? avgSharpe.toFixed(2) : '--'}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 text-center">
            <div className="text-[10px] text-muted-foreground mb-1">最大回撤</div>
            <div className={`text-2xl font-bold ${maxDD > 0.1 ? 'text-red-600' : maxDD > 0 ? 'text-yellow-600' : 'text-muted-foreground'}`}>
              {maxDD > 0 ? `${(maxDD * 100).toFixed(1)}%` : '--'}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* ===== 策略学习状态列表 ===== */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base flex items-center gap-2">
            <Target className="w-4 h-4 text-blue-500" />
            各策略学习状态
          </CardTitle>
        </CardHeader>
        <CardContent>
          {strategies.length === 0 ? (
            <p className="text-center py-6 text-muted-foreground text-sm">暂无策略，请先在 AI 策略中心创建策略</p>
          ) : (
            <div className="space-y-2">
              {strategies.map(s => {
                const d = learningMap[s.strategy_id];
                const mem = d?.memory;
                const isReviewing = reviewingIds.has(s.strategy_id);
                const isSelected = selectedSid === s.strategy_id;
                return (
                  <div
                    key={s.strategy_id}
                    onClick={() => setSelectedSid(s.strategy_id)}
                    className={`flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-all ${
                      isSelected ? 'border-purple-400 bg-purple-50/50 dark:bg-purple-950/20 ring-1 ring-purple-300' : 'hover:bg-muted/50'
                    }`}
                  >
                    <div className={`w-2 h-2 rounded-full shrink-0 ${s.status === 'active' ? 'bg-green-500 animate-pulse' : 'bg-gray-300'}`} />
                    <div className="flex-1 min-w-0">
                      <div className="font-medium text-sm truncate">{s.name}</div>
                      <div className="text-[10px] text-muted-foreground">
                        {s.primary_symbol} · {s.timeframe} · {s.strategy_id}
                      </div>
                    </div>
                    <div className="flex items-center gap-3 text-xs shrink-0">
                      {mem ? (
                        <>
                          <span className="text-muted-foreground">{mem.total_trades}笔</span>
                          <span className={mem.win_rate >= 0.5 ? 'text-green-600 font-medium' : 'text-red-600'}>
                            WR {(mem.win_rate * 100).toFixed(0)}%
                          </span>
                          <span className={mem.sharpe_ratio >= 1 ? 'text-green-600' : 'text-muted-foreground'}>
                            SR {mem.sharpe_ratio.toFixed(2)}
                          </span>
                        </>
                      ) : (
                        <span className="text-muted-foreground">暂无数据</span>
                      )}
                      <Button
                        variant="ghost" size="sm"
                        className="h-7 px-2"
                        onClick={(e) => { e.stopPropagation(); handleReview(s.strategy_id); }}
                        disabled={isReviewing}
                      >
                        {isReviewing
                          ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          : <Lightbulb className="w-3.5 h-3.5" />
                        }
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* ===== 市场状态表现矩阵 ===== */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              <BarChart3 className="w-4 h-4 text-blue-500" />
              市场状态表现（全策略汇总）
            </CardTitle>
          </CardHeader>
          <CardContent>
            {Object.keys(mergedRegime).length === 0 ? (
              <p className="text-center py-6 text-muted-foreground text-sm">暂无足够交易数据</p>
            ) : (
              <div className="grid grid-cols-2 gap-3">
                {Object.entries(mergedRegime).map(([regime, perf]) => {
                  const wr = perf.trades > 0 ? perf.wins / perf.trades : 0;
                  const [cycle, vol] = regime.split('_');
                  return (
                    <div key={regime} className={`border rounded-lg p-3 ${
                      wr >= 0.5 ? 'bg-green-50/50 dark:bg-green-950/10 border-green-200 dark:border-green-800' :
                      'bg-red-50/50 dark:bg-red-950/10 border-red-200 dark:border-red-800'
                    }`}>
                      <div className="flex items-center gap-1.5 mb-1">
                        <Badge variant="outline" className="text-[9px] px-1.5">{cycle || regime}</Badge>
                        {vol && <Badge variant="secondary" className="text-[9px] px-1.5">{vol}</Badge>}
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">{perf.trades} 笔</span>
                        <span className={`font-medium ${wr >= 0.5 ? 'text-green-600' : 'text-red-600'}`}>
                          WR {(wr * 100).toFixed(0)}%
                        </span>
                      </div>
                      <div className={`text-xs mt-1 ${perf.total_pnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                        PnL: {perf.total_pnl >= 0 ? '+' : ''}{(perf.total_pnl * 100).toFixed(2)}%
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>

        {/* ===== 关键教训 ===== */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-yellow-500" />
              关键教训汇总
              {allLessons.length > 0 && (
                <span className="text-xs font-normal text-muted-foreground">最近 {allLessons.length} 条</span>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {allLessons.length === 0 ? (
              <p className="text-center py-6 text-muted-foreground text-sm">暂无教训记录，执行复盘后将自动提取</p>
            ) : (
              <div className="space-y-2 max-h-[280px] overflow-y-auto">
                {allLessons.map((lesson: any, i: number) => (
                  <div key={i} className="flex items-start gap-2 text-sm bg-yellow-50 dark:bg-yellow-950/20 rounded p-2.5">
                    {typeof lesson === 'object' && lesson.symbol ? (
                      <>
                        <Badge variant="outline" className="text-[9px] shrink-0">{lesson.symbol}</Badge>
                        <span className="text-muted-foreground">{lesson.side}</span>
                        <span className={`${(lesson.pnl_pct || 0) < 0 ? 'text-red-600' : 'text-green-600'} font-mono text-xs`}>
                          {((lesson.pnl_pct || 0) * 100).toFixed(1)}%
                        </span>
                        <span className="text-muted-foreground text-xs">{lesson.market_regime || ''}</span>
                      </>
                    ) : (
                      <span className="text-gray-700 dark:text-gray-300">{
                        typeof lesson === 'string' ? lesson : JSON.stringify(lesson)
                      }</span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* ===== 提示词进化历史 ===== */}
      {allEvolutions.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              <GitBranch className="w-4 h-4 text-purple-500" />
              提示词进化历史
              <span className="text-xs font-normal text-muted-foreground">共 {allEvolutions.length} 次进化</span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-1.5 max-h-[200px] overflow-y-auto">
              {allEvolutions.map((evo, i) => {
                let metrics: any = {};
                try { metrics = typeof evo.training_metrics === 'string' ? JSON.parse(evo.training_metrics) : evo.training_metrics || {}; } catch {}
                return (
                  <div key={`${evo.strategyId}-${evo.id}`} className="flex items-center gap-3 text-xs bg-muted/40 rounded p-2">
                    <Badge variant="outline" className="text-[9px]">{evo.strategyName?.slice(0, 12)}</Badge>
                    <span className="text-muted-foreground">{fmtShortDateTime(evo.created_at)}</span>
                    <span className="flex-1 truncate text-muted-foreground">
                      {metrics.evolution_context?.slice(0, 60) || '初始版本'}
                    </span>
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* ===== 全策略最近交易 ===== */}
      {allTrades.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              <BookOpen className="w-4 h-4" />
              全策略最近交易
              <span className="text-xs font-normal text-muted-foreground">{allTrades.length} 笔</span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="max-h-[300px] overflow-y-auto space-y-1">
              {allTrades.map((t, i) => (
                <div key={`${t.id}-${i}`} className="flex items-center gap-2 text-xs py-1.5 px-2 rounded hover:bg-muted/50">
                  <Badge variant="outline" className={`text-[9px] w-6 justify-center ${
                    t.side === 'buy' || t.side === 'long' ? 'text-green-700 border-green-300' : 'text-red-700 border-red-300'
                  }`}>
                    {t.side === 'buy' || t.side === 'long' ? '多' : '空'}
                  </Badge>
                  <span className="w-10 font-medium">{t.symbol}</span>
                  <span className="text-muted-foreground w-20 font-mono">${t.entry_price?.toLocaleString()}</span>
                  {t.pnl_pct !== null && t.pnl_pct !== undefined ? (
                    <span className={`font-medium w-16 text-right font-mono ${t.pnl_pct >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                      {t.pnl_pct >= 0 ? '+' : ''}{(t.pnl_pct * 100).toFixed(2)}%
                    </span>
                  ) : (
                    <span className="w-16 text-right text-muted-foreground">{t.status}</span>
                  )}
                  {t.decision_quality_score !== null && (
                    <span className="text-muted-foreground">Q:{t.decision_quality_score}</span>
                  )}
                  <span className="text-muted-foreground truncate max-w-[80px]">{(t as any).strategyName}</span>
                  {(t as any).execution_type === 'paper' && (
                    <Badge variant="outline" className="text-[8px] bg-amber-50 text-amber-600 border-amber-300 dark:bg-amber-950 dark:text-amber-400 px-1 py-0">
                      PAPER
                    </Badge>
                  )}
                  <span className="ml-auto text-muted-foreground">{fmtShortDateTime(t.opened_at)}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* 空状态 */}
      {strategies.length === 0 && (
        <Card className="border-dashed">
          <CardContent className="py-12 text-center">
            <GraduationCap className="w-12 h-12 mx-auto mb-4 text-gray-300" />
            <h3 className="text-lg font-semibold mb-2">自学习系统就绪</h3>
            <p className="text-sm text-muted-foreground mb-4">
              当 AI 策略执行交易后，系统将自动积累学习数据。<br />
              请先前往「AI 策略中心」创建并激活策略。
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
