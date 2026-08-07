/**
 * 全市场实盘数据中台 — 主页面
 * 4个Tab：多所盘口 / 全市场OI费率 / 鲸鱼资金流 / 数据源配置
 * 顶部：数据健康度仪表盘
 */
import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Card, CardContent } from '../ui/card';
import { Badge } from '../ui/badge';
import { Activity, Database, Waves, Settings, RefreshCw, Loader2, TrendingUp, TrendingDown } from 'lucide-react';
import { IntelCard, deriveStatus } from './IntelCard';
import { fetchOverview, fetchDataHealth, fetchSourcesConfig, fetchWatchlist, type OverviewResponse, type DataHealth, type SourcesConfig, type WatchlistResponse } from '@/lib/marketIntelApi';

export default function MarketIntelView() {
  const [tab, setTab] = useState<'orderbook' | 'oifunding' | 'whale' | 'config'>('orderbook');
  const [watchlist, setWatchlist] = useState<WatchlistResponse | null>(null);
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [health, setHealth] = useState<DataHealth | null>(null);
  const [sourcesConfig, setSourcesConfig] = useState<SourcesConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval>>(undefined);
  const watchTimerRef = useRef<ReturnType<typeof setInterval>>(undefined);

  // symbols 从 watchlist 派生（绝不用默认列表兜底，没有就是没有）
  const symbols = watchlist?.symbols ?? [];
  const symbolSources = useMemo<Record<string, string[]>>(() => {
    const m: Record<string, string[]> = {};
    for (const d of watchlist?.details ?? []) m[d.symbol] = d.sources;
    return m;
  }, [watchlist]);

  // 低频刷新交易对列表（交易对变更不频繁，60s）
  const loadWatchlist = useCallback(async () => {
    try {
      const wl = await fetchWatchlist();
      setWatchlist(wl);
    } catch (e: any) {
      // watchlist 失败不覆盖 overview 错误，静默（symbols 保持空，前端显示空态提示）
    }
  }, []);

  // 高频刷新 overview + health（10s）
  const loadData = useCallback(async () => {
    if (symbols.length === 0) {
      setLoading(false);
      return;
    }
    let ovError: string | null = null;
    // 独立容错：一个请求失败不影响另一个
    try {
      const ov = await fetchOverview(symbols);
      setOverview(ov);
    } catch (e: any) {
      ovError = e.message || '概览加载失败';
      setError(ovError);
    }
    try {
      const hl = await fetchDataHealth();
      setHealth(hl);
    } catch (e: any) {
      // health 失败不覆盖 overview 的错误
      if (!ovError) setError(e.message || '健康度加载失败');
    }
    if (tab === 'config' && !sourcesConfig) {
      try {
        const sc = await fetchSourcesConfig();
        setSourcesConfig(sc);
      } catch (e: any) { /* 静默 */ }
    }
    setLoading(false);
  }, [symbols, tab, sourcesConfig]);

  // 先加载 watchlist（拿 symbols）
  useEffect(() => {
    loadWatchlist();
    watchTimerRef.current = setInterval(loadWatchlist, 60000); // 60s 低频
    return () => clearInterval(watchTimerRef.current);
  }, [loadWatchlist]);

  // symbols 变化时加载 overview（依赖 watchlist 先返回）
  useEffect(() => {
    if (symbols.length === 0) {
      setLoading(false);
      return;
    }
    loadData();
    timerRef.current = setInterval(loadData, 10000); // 10s 高频
    return () => clearInterval(timerRef.current);
  }, [loadData]);

  const activeVenueCount = health ? Object.values({ ...(health.orderbook_venues || {}), ...(health.market_venues || {}) }).filter((v: any) => v.healthy).length : 0;
  const totalVenueCount = health ? Object.keys({ ...(health.orderbook_venues || {}), ...(health.market_venues || {}) }).length : 0;

  const tabs = [
    { key: 'orderbook' as const, label: '多所盘口深度', icon: Activity },
    { key: 'oifunding' as const, label: '全市场OI/费率', icon: Database },
    { key: 'whale' as const, label: '鲸鱼/资金流', icon: Waves },
    { key: 'config' as const, label: '数据源配置', icon: Settings },
  ];

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden">
      {/* 顶部健康度 */}
      <div className="flex-shrink-0 px-4 pt-3 pb-2 border-b">
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold">📊 数据健康度</span>
            <Badge variant={(health?.overall_score ?? 0) >= 0.7 ? 'default' : (health?.overall_score ?? 0) >= 0.4 ? 'secondary' : 'destructive'} className="text-sm">
              {health ? `${Math.round((health.overall_score ?? 0) * 100)}%` : '—'}
            </Badge>
          </div>
          <span className="text-sm text-muted-foreground">数据源: {activeVenueCount}/{totalVenueCount} 在线</span>
          {watchlist && watchlist.counts.total > 0 && (
            <span className="text-sm text-muted-foreground">
              监控 {watchlist.counts.total} 币
              <span className="text-xs ml-1">
                (配置{watchlist.counts.user} | 运行{watchlist.counts.active} | 自动{watchlist.counts.auto})
              </span>
            </span>
          )}
          {health?.orderbook_venues && Object.entries(health.orderbook_venues).map(([v, info]) => (
            <span key={v} className={`text-xs px-1.5 py-0.5 rounded ${info.healthy ? 'text-green-500 bg-green-500/10' : 'text-red-500 bg-red-500/10'}`}>
              {v} {info.healthy ? '✅' : '❌'}
            </span>
          ))}
          <button onClick={loadData} className="ml-auto p-1 hover:bg-muted rounded" title="刷新">
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
        {error && <div className="text-xs text-red-500 mt-1">⚠️ {error}</div>}
      </div>

      {/* Tab 导航 */}
      <div className="flex-shrink-0 flex gap-1 px-4 py-2 border-b">
        {tabs.map(t => {
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors ${
                tab === t.key ? 'bg-primary text-primary-foreground' : 'hover:bg-muted text-muted-foreground'
              }`}
            >
              <Icon className="w-4 h-4" />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-auto p-4">
        {loading && !overview ? (
          <div className="flex items-center justify-center h-32">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        ) : symbols.length === 0 && tab !== 'config' ? (
          <div className="flex flex-col items-center justify-center h-40 text-center text-muted-foreground">
            <Database className="w-8 h-8 mb-2 opacity-40" />
            <p className="text-sm">暂无监控的交易对</p>
            <p className="text-xs mt-1">请在「设置」中添加常用交易对，或启动 AI 策略 / 自动选币</p>
          </div>
        ) : tab === 'orderbook' ? (
          <OrderbookTab overview={overview} symbols={symbols} symbolSources={symbolSources} />
        ) : tab === 'oifunding' ? (
          <OIFundingTab overview={overview} symbols={symbols} symbolSources={symbolSources} />
        ) : tab === 'whale' ? (
          <WhaleFlowTab overview={overview} symbols={symbols} symbolSources={symbolSources} />
        ) : (
          <SourcesConfigTab config={sourcesConfig} onRefresh={loadData} />
        )}
      </div>
    </div>
  );
}

// ════════════════════════════════════════
// 来源标签（配置/运行中/自动）
// ════════════════════════════════════════
const SOURCE_LABELS: Record<string, { label: string; cls: string }> = {
  user: { label: '配置', cls: 'text-blue-500 bg-blue-500/10' },
  active: { label: '运行中', cls: 'text-purple-500 bg-purple-500/10' },
  auto: { label: '自动', cls: 'text-orange-500 bg-orange-500/10' },
};
function SourceBadges({ sources }: { sources?: string[] }) {
  if (!sources || sources.length === 0) return null;
  return (
    <span className="flex gap-0.5">
      {sources.map(s => {
        const cfg = SOURCE_LABELS[s];
        if (!cfg) return null;
        return <span key={s} className={`text-[9px] px-1 rounded ${cfg.cls}`}>{cfg.label}</span>;
      })}
    </span>
  );
}

// ════════════════════════════════════════
// Tab 1: 多所盘口深度
// ════════════════════════════════════════
function OrderbookTab({ overview, symbols, symbolSources }: { overview: OverviewResponse | null; symbols: string[]; symbolSources: Record<string, string[]> }) {
  if (!overview) return null;
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
      {symbols.map(sym => {
        const d = overview.symbols[sym];
        if (!d) return null;
        const ob = d.orderbook;
        const status = deriveStatus(ob.available);
        return (
          <IntelCard key={sym} title={`${sym} 聚合盘口`} source={ob.active_venues > 0 ? `${ob.active_venues}所` : undefined} status={status}>
            {symbolSources[sym] && <div className="-mt-1 mb-1"><SourceBadges sources={symbolSources[sym]} /></div>}
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-green-500">买{ob.best_bid ? `$${ob.best_bid.toLocaleString()}` : '—'}</span>
                <span className="text-red-500">卖{ob.best_ask ? `$${ob.best_ask.toLocaleString()}` : '—'}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">买卖失衡:</span>
                {ob.global_imbalance !== null ? (
                  <Badge variant="outline" className={ob.global_imbalance > 0.1 ? 'text-green-500' : ob.global_imbalance < -0.1 ? 'text-red-500' : ''}>
                    {ob.global_imbalance > 0 ? <TrendingUp className="w-3 h-3 mr-0.5" /> : <TrendingDown className="w-3 h-3 mr-0.5" />}
                    {ob.global_imbalance > 0 ? '+' : ''}{ob.global_imbalance.toFixed(4)}
                  </Badge>
                ) : <span className="text-xs text-muted-foreground">—</span>}
              </div>
              {ob.cross_venue_spread !== null && (
                <div className="text-xs text-muted-foreground">跨所价差: ${ob.cross_venue_spread.toFixed(2)}</div>
              )}
              {/* 各所明细 */}
              <div className="space-y-1 pt-1 border-t">
                {Object.entries(ob.venues || {}).map(([venue, info]) => (
                  <div key={venue} className={`flex justify-between text-xs ${info.available ? '' : 'text-red-500 opacity-60'}`}>
                    <span>{venue}</span>
                    {info.available ? (
                      <span>{info.best_bid ? `$${info.best_bid.toLocaleString()}` : '—'} / {info.best_ask ? `$${info.best_ask.toLocaleString()}` : '—'}</span>
                    ) : (
                      <span>❌ 缺失</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </IntelCard>
        );
      })}
    </div>
  );
}

// ════════════════════════════════════════
// Tab 2: 全市场OI/费率
// ════════════════════════════════════════
function OIFundingTab({ overview, symbols, symbolSources }: { overview: OverviewResponse | null; symbols: string[]; symbolSources: Record<string, string[]> }) {
  if (!overview) return null;
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
      {symbols.map(sym => {
        const d = overview.symbols[sym];
        if (!d) return null;
        const mk = d.market;
        const der = d.derivatives;
        const status = deriveStatus(mk.available);
        return (
          <IntelCard key={sym} title={`${sym} 全市场OI/费率`} source={mk.active_venues > 0 ? `${mk.active_venues}所` : undefined} status={status}>
            <div className="space-y-2">
              {symbolSources[sym] && <div><SourceBadges sources={symbolSources[sym]} /></div>}
              {mk.total_oi && (
                <div className="text-sm"><span className="text-muted-foreground">总OI:</span> <strong>{mk.total_oi.toLocaleString()}</strong></div>
              )}
              {/* 各所费率 */}
              <div className="space-y-1">
                <span className="text-xs text-muted-foreground">各所费率:</span>
                {Object.entries(mk.funding_rates || {}).map(([venue, rate]) => (
                  <div key={venue} className="flex justify-between text-xs">
                    <span>{venue}</span>
                    {rate !== null ? (
                      <span className={rate > 0.0001 ? 'text-red-500' : rate < 0 ? 'text-green-500' : ''}>
                        {(rate * 100).toFixed(4)}%
                      </span>
                    ) : <span className="text-red-500">—</span>}
                  </div>
                ))}
              </div>
              {/* 套利空间 */}
              {mk.funding_arbitrage !== null && (
                <div className={`text-xs px-2 py-1 rounded ${mk.funding_arbitrage > 0.0003 ? 'bg-yellow-500/10 text-yellow-500' : 'text-muted-foreground'}`}>
                  ⚡ 费率套利: {(mk.funding_arbitrage * 100).toFixed(5)}%
                  {mk.funding_arbitrage > 0.0003 && ' (可套利)'}
                </div>
              )}
              {/* 衍生品信号 */}
              {der.available && (
                <div className="pt-1 border-t text-xs space-y-0.5">
                  <div>信号: <strong>{der.signal}</strong> (强度 {der.signal_strength})</div>
                  {der.liquidation_short && <div>清算: 多${(der.liquidation_long || 0).toLocaleString()} / 空${der.liquidation_short.toLocaleString()}</div>}
                  {der.long_short_ratio && <div>多空比: {der.long_short_ratio.toFixed(3)}</div>}
                  <div className="text-muted-foreground">数据源: {der.data_sources}</div>
                </div>
              )}
            </div>
          </IntelCard>
        );
      })}
    </div>
  );
}

// ════════════════════════════════════════
// Tab 3: 鲸鱼/资金流
// ════════════════════════════════════════
function WhaleFlowTab({ overview, symbols, symbolSources }: { overview: OverviewResponse | null; symbols: string[]; symbolSources: Record<string, string[]> }) {
  if (!overview) return null;
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
      {symbols.map(sym => {
        const d = overview.symbols[sym];
        if (!d) return null;
        const wh = d.whale;
        const status = deriveStatus(wh.available);
        const dir = wh.direction ?? 0;
        const venues = wh.venues || {};
        const venueCount = Object.keys(venues).filter(v => venues[v].available).length;
        return (
          <IntelCard key={sym} title={`${sym} 鲸鱼/资金流`} source={wh.available ? `${venueCount}所大单` : undefined} status={status} missingReason={!wh.available ? '近100笔无大单(>$50K)' : undefined}>
            <div className="space-y-2">
              {symbolSources[sym] && <div><SourceBadges sources={symbolSources[sym]} /></div>}
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">方向:</span>
                {wh.direction !== null ? (
                  <Badge variant="outline" className={dir > 0.1 ? 'text-green-500' : dir < -0.1 ? 'text-red-500' : ''}>
                    {dir > 0.1 ? '🟢 净买入' : dir < -0.1 ? '🔴 净卖出' : '⚪ 中性'} ({dir.toFixed(2)})
                  </Badge>
                ) : <span className="text-xs text-muted-foreground">—</span>}
              </div>
              {wh.total_usd && wh.total_usd > 0 && (
                <>
                  <div className="text-sm">大单总额: <strong>${(wh.total_usd / 1000000).toFixed(2)}M</strong>
                    {wh.whale_count != null && <span className="text-xs text-muted-foreground ml-1">({wh.whale_count}笔)</span>}
                  </div>
                  {wh.net_usd != null && wh.net_usd !== 0 && (
                    <div className={`text-xs ${wh.net_usd > 0 ? 'text-green-500' : 'text-red-500'}`}>
                      净额: {wh.net_usd > 0 ? '+' : ''}${(wh.net_usd / 1000).toFixed(1)}K
                    </div>
                  )}
                </>
              )}
              {wh.confidence !== null && wh.confidence > 0 && (
                <div className="text-xs text-muted-foreground">置信度: {(wh.confidence * 100).toFixed(0)}%</div>
              )}
              {/* 各所大单明细 */}
              {venueCount > 0 && (
                <div className="space-y-1 pt-1 border-t">
                  {Object.entries(venues).map(([venue, info]) => (
                    <div key={venue} className={`flex justify-between text-xs ${info.available ? '' : 'text-muted-foreground opacity-50'}`}>
                      <span>{venue}</span>
                      {info.available ? (
                        <span>
                          {info.whale_buy_usd ? `买$${(info.whale_buy_usd / 1000).toFixed(0)}K` : '—'} / {info.whale_sell_usd ? `卖$${(info.whale_sell_usd / 1000).toFixed(0)}K` : '—'}
                          {info.count ? ` (${info.count})` : ''}
                        </span>
                      ) : (
                        <span>无大单</span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </IntelCard>
        );
      })}
    </div>
  );
}

// ════════════════════════════════════════
// Tab 4: 数据源配置
// ════════════════════════════════════════
function SourcesConfigTab({ config, onRefresh }: { config: SourcesConfig | null; onRefresh: () => void }) {
  if (!config) return <Loader2 className="w-6 h-6 animate-spin mx-auto" />;
  return (
    <div className="space-y-4 max-w-3xl">
      <Card>
        <CardContent className="pt-4">
          <h3 className="text-sm font-medium mb-3">交易所接入</h3>
          <div className="space-y-1">
            {Object.entries(config.venues || {}).map(([id, v]) => {
              const health = config.venue_health?.[id];
              return (
                <div key={id} className="flex items-center justify-between py-1.5 border-b last:border-0">
                  <span className="text-sm">{v.name}</span>
                  <div className="flex items-center gap-3 text-xs">
                    <span className={v.api_key_configured ? 'text-green-500' : 'text-muted-foreground'}>
                      {v.api_key_configured ? '✅ Key已配' : v.public_api ? '公共API' : '❌ 未配Key'}
                    </span>
                    {health && (
                      <span className={health.healthy ? 'text-green-500' : 'text-red-500'}>
                        {health.healthy ? '✅ 在线' : `❌ 失败${health.fail_count}次`}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="pt-4">
          <h3 className="text-sm font-medium mb-3">聚合数据源</h3>
          <div className="space-y-1">
            {Object.entries(config.aggregate_sources || {}).map(([id, s]) => (
              <div key={id} className="flex items-center justify-between py-1.5 border-b last:border-0">
                <span className="text-sm">{s.name}</span>
                <span className={`text-xs ${s.api_key_configured ? 'text-green-500' : 'text-muted-foreground'}`}>
                  {s.api_key_configured ? '✅ Key已配' : '⏸ 未配置'}
                </span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
      <button onClick={onRefresh} className="text-xs text-muted-foreground hover:text-foreground">
        <RefreshCw className="w-3 h-3 inline mr-1" />刷新状态
      </button>
    </div>
  );
}
