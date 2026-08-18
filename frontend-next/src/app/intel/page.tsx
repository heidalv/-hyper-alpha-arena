"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  Globe, Activity, Database, Waves, RefreshCw, Loader2,
  TrendingUp, TrendingDown, LineChart, HeartPulse,
} from "lucide-react";
import { useMarketOverview, useMarketHealth, useWatchlist, useMarketOverviewAll } from "@/hooks/useTradingData";
import { cn } from "@/lib/utils";
import MarketOverviewTable from "@/components/market/MarketOverviewTable";
import { KlineChartPanel } from "@/components/market/KlineChartPanel";
// [2026-08-05 v6 9.3] 三链路健康卡：行情/K线/链上 + 告警列表
import DataQualityPanel from "@/components/monitor/DataQualityPanel";
// [2026-08-16] 数据中心体检：现有数据/缺失/采集器/回填/入库及时性
import { DataCenterOverviewPanel } from "@/components/monitor/DataCenterOverviewPanel";

type Tab = "overview" | "kline" | "orderbook" | "oi" | "whale" | "health" | "dc";

export default function IntelPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center h-40">
          <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
        </div>
      }
    >
      <IntelPageInner />
    </Suspense>
  );
}

function IntelPageInner() {
  const searchParams = useSearchParams();
  const readTab = (sp: URLSearchParams): Tab => {
    const t = (sp.get("tab") || "").toLowerCase();
    if (t === "kline" || t === "charts" || t === "chart" || t === "k线") return "kline";
    if (t === "orderbook" || t === "盘口") return "orderbook";
    if (t === "oi" || t === "费率") return "oi";
    if (t === "whale" || t === "鲸鱼") return "whale";
    if (t === "health" || t === "健康" || t === "数据健康") return "health";
    if (t === "dc" || t === "数据中心" || t === "数据中台" || t === "体检") return "dc";
    if (sp.get("symbol")) return "kline";
    return "overview";
  };

  const [tab, setTab] = useState<Tab>(() => readTab(searchParams));
  const [symbols] = useState(["BTC", "ETH", "SOL"]);
  const [exchange, setExchange] = useState("binance");
  const [klineReady, setKlineReady] = useState(() => readTab(searchParams) === "kline" || !!searchParams.get("symbol"));
  const [klineFocus, setKlineFocus] = useState<{
    symbol: string;
    exchange: string;
    key: number;
  } | null>(() => {
    const sym = searchParams.get("symbol");
    if (!sym) return null;
    return {
      symbol: sym,
      exchange: searchParams.get("exchange") || "binance",
      key: Date.now(),
    };
  });

  useEffect(() => {
    if (tab === "kline") setKlineReady(true);
  }, [tab]);

  useEffect(() => {
    const next = readTab(searchParams);
    setTab(next);
    const sym = searchParams.get("symbol");
    const ex = searchParams.get("exchange");
    if (!sym) return;
    setKlineFocus((prev) => {
      if (prev && prev.symbol === sym && prev.exchange === (ex || prev.exchange)) return prev;
      return { symbol: sym, exchange: ex || "binance", key: Date.now() };
    });
    setKlineReady(true);
  }, [searchParams]);

  const syncUrl = (nextTab: Tab, focus?: { symbol: string; exchange: string } | null) => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    url.searchParams.set("tab", nextTab);
    if (nextTab === "kline" && focus?.symbol) {
      url.searchParams.set("symbol", focus.symbol);
      url.searchParams.set("exchange", focus.exchange);
    } else if (nextTab !== "kline") {
      // 保留 symbol，方便再点回来；不强制清
    }
    window.history.replaceState({}, "", url.toString());
  };

  const openKline = (row: { symbol: string; exchange?: string }) => {
    const ex =
      row.exchange ||
      (exchange && exchange !== "all" ? exchange : "binance");
    const focus = { symbol: row.symbol, exchange: ex, key: Date.now() };
    setKlineFocus(focus);
    setKlineReady(true);
    setTab("kline");
    syncUrl("kline", focus);
  };

  const switchTab = (next: Tab) => {
    setTab(next);
    if (next === "kline") setKlineReady(true);
    syncUrl(next, klineFocus);
  };

  const { data: health, refetch: refetchHealth } = useMarketHealth();
  const { data: watchlist } = useWatchlist();
  const wlSymbols = watchlist?.symbols ?? symbols;
  const { data: overview, isLoading, refetch: refetchOverview } = useMarketOverview(wlSymbols);
  const { data: allMarket, isLoading: allLoading } = useMarketOverviewAll(exchange);

  const refetch = () => {
    refetchHealth();
    refetchOverview();
  };

  const score = health?.overall_score ?? 0;
  const venues = { ...(health?.orderbook_venues || {}), ...(health?.market_venues || {}) };
  const onlineCount = Object.values(venues).filter((v: any) => v.healthy).length;
  const totalCount = Object.keys(venues).length;

  const tabs: { key: Tab; label: string; icon: any }[] = [
    { key: "overview", label: "总览", icon: Globe },
    { key: "kline", label: "K 线", icon: LineChart },
    { key: "orderbook", label: "多所盘口", icon: Activity },
    { key: "oi", label: "OI/费率", icon: Database },
    { key: "whale", label: "鲸鱼/资金流", icon: Waves },
    { key: "dc", label: "数据中心", icon: Database },
    { key: "health", label: "数据健康", icon: HeartPulse },
  ];

  return (
    <div className="p-4 space-y-4">
      <PageHeader
        icon={<Globe className="w-4 h-4" />}
        title="全市场数据中台"
        subtitle="行情 · 盘口 · OI · 鲸鱼资金流 · 数据健康"
        refreshHint="行情 10s 轮询"
        breadcrumb={[{ label: "市场 & 分析" }, { label: "全市场数据中台" }]}
        badge={
          <Badge
            variant="secondary"
            className={cn(
              "text-xs",
              score >= 0.7
                ? "bg-profit/20 text-profit"
                : score >= 0.4
                  ? "bg-warning/20 text-warning"
                  : "bg-loss/20 text-loss"
            )}
          >
            健康 {(score * 100).toFixed(0)}%
          </Badge>
        }
        actions={
          <>
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">
                {onlineCount}/{totalCount} 在线
              </span>
            </div>
            {Object.entries(venues).map(([v, info]: [string, any]) => (
              <Badge
                key={v}
                variant="outline"
                className={cn(
                  "text-xs",
                  info.healthy ? "text-profit border-profit/30" : "text-loss border-loss/30"
                )}
              >
                {v}
              </Badge>
            ))}
            {health?.data_center && (
              <Badge
                variant="outline"
                className={cn(
                  "text-xs",
                  health.data_center.online ? "text-profit border-profit/30" : "text-loss border-loss/30"
                )}
              >
                数据中心
              </Badge>
            )}
            <Button variant="ghost" size="sm" onClick={refetch}>
              <RefreshCw className="w-3.5 h-3.5" />
            </Button>
          </>
        }
      />

      <div className="flex gap-1 border-b border-border">
        {tabs.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              onClick={() => switchTab(t.key)}
              className={cn(
                "flex items-center gap-1.5 px-3 py-2 text-sm border-b-2 transition-colors -mb-px",
                tab === t.key
                  ? "border-primary text-primary font-medium"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              )}
            >
              <Icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* K 线保活：首次进入后隐藏不卸载，避免 Tab 切换把图表画成 0 尺寸 */}
      {klineReady && (
        <div className={cn(tab === "kline" ? "block" : "hidden")}>
          <KlineChartPanel
            embedded
            visible={tab === "kline"}
            initialSymbol={klineFocus?.symbol}
            initialExchange={klineFocus?.exchange}
            focusKey={klineFocus?.key}
          />
        </div>
      )}

      {tab === "kline" ? null : tab === "health" ? (
        <Card className="glass">
          <div className="flex items-center justify-between gap-2 px-4 pb-3 border-b border-border/40">
            <div className="flex items-center gap-2">
              <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-cyan-400/10 text-cyan-300">
                <HeartPulse className="w-4 h-4" />
              </span>
              <span className="text-sm font-medium">数据健康</span>
              <Badge variant="secondary" className="text-xs">三链路</Badge>
            </div>
          </div>
          <div className="px-4">
            <DataQualityPanel />
          </div>
        </Card>
      ) : tab === "dc" ? (
        <Card className="glass">
          <div className="flex items-center justify-between gap-2 px-4 pb-3 border-b border-border/40">
            <div className="flex items-center gap-2">
              <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-cyan-400/10 text-cyan-300">
                <Database className="w-4 h-4" />
              </span>
              <span className="text-sm font-medium">数据中心</span>
              <Badge variant="secondary" className="text-xs">体检</Badge>
            </div>
          </div>
          <div className="px-4">
            <DataCenterOverviewPanel />
          </div>
        </Card>
      ) : tab === "overview" ? (
        <Card className="glass">
          <div className="flex items-center justify-between gap-2 px-4 pb-3 border-b border-border/40 flex-wrap">
            <div className="flex items-center gap-2">
              <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-cyan-400/10 text-cyan-300">
                <Activity className="w-4 h-4" />
              </span>
              <span className="text-sm font-medium">市场行情</span>
              <Badge variant="secondary" className="text-xs">按成交额排序</Badge>
            </div>
            <div className="flex items-center gap-1 flex-wrap">
              <span className="text-xs text-muted-foreground mr-1">交易所</span>
              {[
                { k: "asterdex", label: "Asterdex" },
                { k: "binance", label: "Binance" },
                { k: "okx", label: "OKX" },
                { k: "hyperliquid", label: "Hyperliquid" },
                { k: "all", label: "全部交易所" },
              ].map((e) => (
                <button
                  key={e.k}
                  onClick={() => setExchange(e.k)}
                  className={cn(
                    "px-2.5 py-1 text-xs rounded-full border transition-colors",
                    exchange === e.k
                      ? "border-primary bg-primary/10 text-primary font-medium"
                      : "border-border text-muted-foreground hover:text-foreground"
                  )}
                >
                  {e.label}
                </button>
              ))}
            </div>
          </div>
          <div className="px-4">
            <MarketOverviewTable
              rows={allMarket?.rows ?? []}
              loading={allLoading}
              fetchedAt={allMarket?.fetched_at}
              source={allMarket?.source}
              onSymbolClick={openKline}
            />
          </div>
          <div className="px-4 py-2 border-t border-border/30 text-xs text-muted-foreground">
            点击交易对可打开对应 K 线
          </div>
        </Card>
      ) : isLoading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
        </div>
      ) : !overview?.symbols ? (
        <div className="text-center py-12 text-muted-foreground text-sm">暂无数据</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {Object.entries(overview.symbols).map(([sym, d]: [string, any]) => {
            const ob = d.orderbook;
            const mk = d.market;
            const wh = d.whale;

            if (tab === "orderbook") {
              return (
                <Card key={sym} className="glass">
                  <div className="flex items-center justify-between gap-2 px-4 pb-3 border-b border-border/40">
                    <div className="flex items-center gap-2">
                      <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-cyan-400/10 text-cyan-300">
                        <Activity className="w-4 h-4" />
                      </span>
                      <span className="text-sm font-medium tabular-nums">{sym}</span>
                      <span className="text-xs text-muted-foreground">多所盘口</span>
                    </div>
                    <Badge
                      variant="secondary"
                      className={cn("text-xs", ob.available ? "text-profit" : "text-muted-foreground")}
                    >
                      {ob.active_venues ?? 0}所
                    </Badge>
                  </div>
                  <div className="px-4 space-y-2">
                    {d.dc?.available && (
                      <div className="flex justify-between text-xs py-1 mb-2 border-y border-border/30">
                        <span className="text-muted-foreground">数据中心最新价</span>
                        <span className="tabular-nums">
                          ${Number(d.dc.last_price).toLocaleString(undefined, { maximumFractionDigits: 2 })}
                          <span className="text-muted-foreground ml-1">
                            ·{" "}
                            {d.dc.age_sec < 60
                              ? `${Math.max(1, Math.floor(d.dc.age_sec))}s`
                              : `${Math.round(d.dc.age_sec / 60)}m`}
                            前
                          </span>
                        </span>
                      </div>
                    )}
                    {ob.available ? (
                      <div className="space-y-2">
                        <div className="flex justify-between text-sm">
                          <span className="text-profit tabular-nums">
                            买 {ob.best_bid ? `$${ob.best_bid.toLocaleString()}` : "—"}
                          </span>
                          <span className="text-loss tabular-nums">
                            卖 {ob.best_ask ? `$${ob.best_ask.toLocaleString()}` : "—"}
                          </span>
                        </div>
                        {ob.global_imbalance !== null && (
                          <div className="flex items-center gap-2">
                            <span className="text-xs text-muted-foreground">买卖失衡:</span>
                            <Badge
                              variant="outline"
                              className={cn(
                                ob.global_imbalance > 0.1
                                  ? "text-profit"
                                  : ob.global_imbalance < -0.1
                                    ? "text-loss"
                                    : ""
                              )}
                            >
                              {ob.global_imbalance > 0 ? (
                                <TrendingUp className="w-3 h-3 mr-0.5" />
                              ) : (
                                <TrendingDown className="w-3 h-3 mr-0.5" />
                              )}
                              {ob.global_imbalance > 0 ? "+" : ""}
                              {ob.global_imbalance.toFixed(4)}
                            </Badge>
                          </div>
                        )}
                        {ob.venues && Object.keys(ob.venues).length > 0 && (
                          <div className="space-y-0.5 pt-1 border-t border-border/30">
                            {Object.entries(ob.venues).map(([venue, info]: [string, any]) => (
                              <div
                                key={venue}
                                className={cn(
                                  "flex justify-between text-xs",
                                  !info.available && "opacity-40"
                                )}
                              >
                                <span>{venue}</span>
                                {info.available ? (
                                  <span className="tabular-nums">
                                    {info.best_bid ? `$${info.best_bid.toLocaleString()}` : "—"} /{" "}
                                    {info.best_ask ? `$${info.best_ask.toLocaleString()}` : "—"}
                                  </span>
                                ) : (
                                  <span className="text-muted-foreground">缺失</span>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="text-center py-3 text-muted-foreground text-xs">数据缺失</div>
                    )}
                  </div>
                </Card>
              );
            }

            if (tab === "oi") {
              return (
                <Card key={sym} className="glass">
                  <div className="flex items-center justify-between gap-2 px-4 pb-3 border-b border-border/40">
                    <div className="flex items-center gap-2">
                      <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-cyan-400/10 text-cyan-300">
                        <Database className="w-4 h-4" />
                      </span>
                      <span className="text-sm font-medium">{sym} OI/费率</span>
                    </div>
                    <Badge
                      variant="secondary"
                      className={cn("text-xs", mk.available ? "text-profit" : "text-muted-foreground")}
                    >
                      {mk.available ? "正常" : "缺失"}
                    </Badge>
                  </div>
                  <div className="px-4 space-y-2">
                    {mk.available ? (
                      <div className="space-y-2">
                        {mk.total_oi && (
                          <div className="text-sm">
                            <span className="text-muted-foreground">总OI:</span>{" "}
                            <strong className="tabular-nums">{mk.total_oi.toLocaleString()}</strong>
                          </div>
                        )}
                        {mk.funding_rates &&
                          Object.entries(mk.funding_rates).map(([venue, rate]: [string, any]) => (
                            <div key={venue} className="flex justify-between text-xs">
                              <span>{venue}</span>
                              {rate !== null ? (
                                <span
                                  className={cn(
                                    "tabular-nums",
                                    rate > 0.0001 ? "text-loss" : rate < 0 ? "text-profit" : ""
                                  )}
                                >
                                  {(rate * 100).toFixed(4)}%
                                </span>
                              ) : (
                                <span className="text-muted-foreground">—</span>
                              )}
                            </div>
                          ))}
                        {mk.funding_arbitrage !== null && Math.abs(mk.funding_arbitrage) > 0.0003 && (
                          <div className="text-xs px-2 py-1 rounded bg-warning/10 text-warning">
                            ⚡ 费率套利: {(mk.funding_arbitrage * 100).toFixed(5)}% (可套利)
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="text-center py-3 text-muted-foreground text-xs">数据缺失</div>
                    )}
                  </div>
                </Card>
              );
            }

            return (
              <Card key={sym} className="glass">
                <div className="flex items-center justify-between gap-2 px-4 pb-3 border-b border-border/40">
                  <div className="flex items-center gap-2">
                    <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-cyan-400/10 text-cyan-300">
                      <Waves className="w-4 h-4" />
                    </span>
                    <span className="text-sm font-medium">{sym} 鲸鱼/资金流</span>
                  </div>
                  <Badge
                    variant="secondary"
                    className={cn("text-xs", wh?.available ? "text-profit" : "text-muted-foreground")}
                  >
                    {wh?.available ? "正常" : "缺失"}
                  </Badge>
                </div>
                <div className="px-4 space-y-2">
                  {wh?.available ? (
                    <div className="space-y-2">
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">方向:</span>
                        <Badge
                          variant="outline"
                          className={cn(
                            (wh.direction ?? 0) > 0.1
                              ? "text-profit"
                              : (wh.direction ?? 0) < -0.1
                                ? "text-loss"
                                : ""
                          )}
                        >
                          {(wh.direction ?? 0) > 0.1
                            ? "🟢 净买入"
                            : (wh.direction ?? 0) < -0.1
                              ? "🔴 净卖出"
                              : "⚪ 中性"}{" "}
                          ({(wh.direction ?? 0).toFixed(2)})
                        </Badge>
                      </div>
                      {wh.total_usd && wh.total_usd > 0 && (
                        <div className="text-sm">
                          异动金额:{" "}
                          <strong className="tabular-nums">${(wh.total_usd / 1000000).toFixed(2)}M</strong>
                        </div>
                      )}
                      {wh.confidence !== null && (
                        <div className="text-xs text-muted-foreground">
                          置信度: {(wh.confidence * 100).toFixed(0)}%
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="text-center py-3 text-muted-foreground text-xs">
                      近100笔无大单(&gt;$50K)
                    </div>
                  )}
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
