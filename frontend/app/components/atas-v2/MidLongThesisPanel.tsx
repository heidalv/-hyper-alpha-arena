/**

 * 长线 MLTO 研判面板 — 展示 thesis 账本与开单就绪度

 */

import { useState, useEffect, useCallback, useMemo } from 'react';

import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';

import { Badge } from '../ui/badge';

import { Brain, TrendingUp, TrendingDown, Minus, RefreshCw, ChevronDown, ChevronUp } from 'lucide-react';



interface ThesisItem {

  thesis_id: string;

  symbol: string;

  tier: string;

  direction: string;

  thesis_summary: string;

  llm_conviction: number;

  hub_adjusted: number;

  open_readiness: number;

  review_count: number;

  tranche_stage: number;

  updated_at?: string | null;

  invalidation?: Record<string, unknown>;

  pending?: boolean;

  gate_status?: {

    can_open?: boolean;

    summary?: string;

    checks?: Array<{ key: string; ok: boolean; label: string }>;

  };

}



interface MltoMetrics {

  thesis_hit_rate?: number | null;

  premature_open_rate?: number | null;

  thesis_drift_resets?: number;

  sample_count?: number;

}



interface MidLongThesisPanelProps {

  sessionId: string;

  refreshSec?: number;

  /** 市场概览中的币种，用于展示 mid/long 占位行 */

  watchSymbols?: string[];

  /** 默认折叠，避免挤压决策日志 */

  defaultCollapsed?: boolean;

}



const tierLabel = (tier: string) => (tier === 'long' ? '长线' : '中线');



const dirBadge = (dir: string) => {

  const d = (dir || 'neutral').toLowerCase();

  if (d === 'long') {

    return (

      <Badge className="bg-green-600 gap-1">

        <TrendingUp className="w-3 h-3" /> 多

      </Badge>

    );

  }

  if (d === 'short') {

    return (

      <Badge className="bg-red-600 gap-1">

        <TrendingDown className="w-3 h-3" /> 空

      </Badge>

    );

  }

  return (

    <Badge variant="secondary" className="gap-1">

      <Minus className="w-3 h-3" /> 中性

    </Badge>

  );

};



const readinessColor = (v: number) => {

  if (v >= 78) return 'text-green-600';

  if (v >= 60) return 'text-amber-600';

  return 'text-muted-foreground';

};



export function MidLongThesisPanel({

  sessionId,

  refreshSec = 30,

  watchSymbols = [],

  defaultCollapsed = true,

}: MidLongThesisPanelProps) {

  const [theses, setTheses] = useState<ThesisItem[]>([]);

  const [metrics, setMetrics] = useState<MltoMetrics>({});

  const [loading, setLoading] = useState(false);

  const [expanded, setExpanded] = useState<string | null>(null);

  const [collapsed, setCollapsed] = useState(defaultCollapsed);

  const [timeline, setTimeline] = useState<Record<string, Array<{ event_id: string; layer: string; source: string; signal: string; summary: string }>>>({});



  const symbolQuery = useMemo(

    () => [...new Set(watchSymbols.map(s => s.replace(/USDT$/i, '').trim()).filter(Boolean))].join(','),

    [watchSymbols],

  );



  const fetchSummary = useCallback(async () => {

    if (!sessionId) return;

    setLoading(true);

    try {

      const q = symbolQuery ? `?symbols=${encodeURIComponent(symbolQuery)}` : '';

      const res = await fetch(`/api/mlto/sessions/${encodeURIComponent(sessionId)}/thesis/summary${q}`);

      if (res.ok) {

        const data = await res.json();

        setTheses(data.theses || []);

        setMetrics(data.metrics || {});

      }

    } catch (e) {

      console.error('[MLTO] summary fetch fail', e);

    } finally {

      setLoading(false);

    }

  }, [sessionId, symbolQuery]);



  const fetchDetail = useCallback(async (symbol: string, tier: string) => {

    if (!sessionId) return;

    const key = `${symbol}:${tier}`;

    try {

      const q = new URLSearchParams({ symbol, tier });

      const res = await fetch(

        `/api/mlto/sessions/${encodeURIComponent(sessionId)}/thesis?${q.toString()}`

      );

      if (res.ok) {

        const data = await res.json();

        setTimeline(prev => ({ ...prev, [key]: data.memory_events || [] }));

      }

    } catch (e) {

      console.error('[MLTO] detail fetch fail', e);

    }

  }, [sessionId]);



  useEffect(() => {

    fetchSummary();

    const id = setInterval(fetchSummary, refreshSec * 1000);

    return () => clearInterval(id);

  }, [fetchSummary, refreshSec]);



  const toggleExpand = (t: ThesisItem) => {

    if (t.pending) return;

    const key = `${t.symbol}:${t.tier}`;

    if (expanded === key) {

      setExpanded(null);

      return;

    }

    setExpanded(key);

    if (!timeline[key]) {

      void fetchDetail(t.symbol, t.tier);

    }

  };



  const sorted = [...theses].sort((a, b) => {

    const ta = a.tier === 'long' ? 1 : 0;

    const tb = b.tier === 'long' ? 1 : 0;

    if (ta !== tb) return ta - tb;

    return a.symbol.localeCompare(b.symbol);

  });



  const activeCount = sorted.filter(t => !t.pending).length;

  const maxReady = sorted.reduce((m, t) => Math.max(m, t.open_readiness || 0), 0);



  return (

    <Card className="min-w-0 w-full shrink-0">

      <CardHeader

        className="py-2 px-4 flex flex-row items-center justify-between cursor-pointer select-none"

        onClick={() => setCollapsed(v => !v)}

      >

        <CardTitle className="text-sm flex items-center gap-2">

          <Brain className="w-4 h-4 text-purple-500" />

          长线研判 (MLTO)

          <span className="text-[10px] font-normal text-muted-foreground">

            {activeCount > 0 ? `${activeCount} 条 · 最高就绪 ${maxReady}%` : `${sorted.length} 项监控`}

          </span>

        </CardTitle>

        <div className="flex items-center gap-1">

          <button

            type="button"

            onClick={(e) => { e.stopPropagation(); fetchSummary(); }}

            className="text-muted-foreground hover:text-foreground p-1"

            title="刷新"

          >

            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />

          </button>

          {collapsed ? <ChevronDown className="w-4 h-4 text-muted-foreground" /> : <ChevronUp className="w-4 h-4 text-muted-foreground" />}

        </div>

      </CardHeader>

      {!collapsed && (

        <CardContent className="pt-0 px-4 pb-3 space-y-2 max-h-[min(50vh,360px)] overflow-y-auto">

          {metrics.sample_count != null && metrics.sample_count > 0 && (

            <div className="text-[10px] text-muted-foreground flex gap-3 pb-1 border-b">

              {metrics.thesis_hit_rate != null && (

                <span>命中率 {(metrics.thesis_hit_rate * 100).toFixed(0)}%</span>

              )}

              <span>样本 {metrics.sample_count}</span>

              {(metrics.thesis_drift_resets ?? 0) > 0 && (

                <span>Regime 重置 {metrics.thesis_drift_resets}</span>

              )}

            </div>

          )}



          {sorted.length === 0 ? (

            <div className="text-center py-4 text-xs text-muted-foreground">

              {loading ? '加载研判数据…' : '暂无监控币种（需市场扫描后显示 mid/long 占位）'}

            </div>

          ) : (

            sorted.map(t => {

              const key = `${t.symbol}:${t.tier}`;

              const isOpen = expanded === key;

              const events = timeline[key] || [];

              return (

                <div

                  key={t.thesis_id || key}

                  className={`border rounded-md p-2 text-xs space-y-1 ${t.pending ? 'opacity-70' : 'cursor-pointer hover:bg-muted/30'}`}

                  onClick={() => toggleExpand(t)}

                >

                  <div className="flex items-center gap-2 flex-wrap">

                    <span className="font-semibold">{t.symbol}</span>

                    <Badge variant="outline" className="text-[10px]">

                      {tierLabel(t.tier)}

                    </Badge>

                    {t.pending ? (

                      <Badge variant="secondary" className="text-[10px]">待 tick</Badge>

                    ) : dirBadge(t.direction)}

                    {!t.pending && (

                      <>

                        <span className={`font-mono tabular-nums ${readinessColor(t.open_readiness)}`}>

                          就绪 {t.open_readiness}%

                        </span>

                        <span className="text-muted-foreground font-mono">

                          Hub {Number(t.hub_adjusted || 0).toFixed(2)}

                        </span>

                        <span className="text-muted-foreground">复核 ×{t.review_count}</span>

                      </>

                    )}

                  </div>

                  {!t.pending && (

                    <div className="space-y-1">

                      <div className="flex items-center gap-2">

                        <span className="text-[10px] text-muted-foreground w-12">就绪度</span>

                        <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">

                          <div

                            className="h-full rounded-full"

                            style={{

                              width: `${Math.min(100, t.open_readiness)}%`,

                              backgroundColor: t.open_readiness >= 72 ? '#16a34a' : t.open_readiness >= 55 ? '#d97706' : '#94a3b8',

                            }}

                          />

                        </div>

                        <span className="text-[10px] font-mono w-8">{t.llm_conviction}%</span>

                      </div>

                      {t.gate_status?.summary && (

                        <p className={`text-[10px] line-clamp-2 ${t.gate_status.can_open ? 'text-green-600' : 'text-amber-600'}`}>

                          {t.gate_status.summary}

                        </p>

                      )}

                    </div>

                  )}

                  {t.thesis_summary ? (

                    <p className="text-muted-foreground leading-relaxed line-clamp-2">

                      {t.thesis_summary}

                    </p>

                  ) : (

                    <p className="text-muted-foreground italic text-[10px]">

                      {t.pending

                        ? '等待编排器调度 mid/long 决策…'

                        : t.review_count > 0

                          ? '证据已摄入，LLM 摘要生成中…'

                          : '等待首轮 thesis_update…'}

                    </p>

                  )}

                  {isOpen && events.length > 0 && (

                    <div className="mt-2 pt-2 border-t space-y-1 max-h-28 overflow-y-auto">

                      <div className="text-[10px] font-medium text-muted-foreground">证据时间线</div>

                      {events.map(ev => (

                        <div key={ev.event_id} className="flex gap-2 text-[10px]">

                          <span className="text-purple-600 shrink-0 w-16">{ev.source}</span>

                          <span className="text-muted-foreground shrink-0 w-14">{ev.layer}</span>

                          <span className="flex-1 truncate" title={ev.summary}>

                            {ev.summary}

                          </span>

                        </div>

                      ))}

                    </div>

                  )}

                </div>

              );

            })

          )}

        </CardContent>

      )}

    </Card>

  );

}



export default MidLongThesisPanel;

