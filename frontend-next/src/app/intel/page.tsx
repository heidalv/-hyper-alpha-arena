"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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

type Tab = "overview" | "kline" | "orderbook" | "oi" | "whale" | "health";

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
    if (sp.get("symbol")) return "kline";
    return "overview";
  };

  const [tab, setTab] = useState<Tab>(() => readTab(searchParams));
  const [symbols] = useState(["BTC", "ETH", "SOL"]);
  const [exchange, setExchange] = useState("asterdex");
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
      exchange: searchParams.get("exchange") || "asterdex",
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
      return { symbol: sym, exchange: ex || "asterdex", key: Date.now() };
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
      (exchange && exchange !== "all" ? exchange : "asterdex");
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
    { key: "health", label: "数据健康", icon: HeartPulse },
  ];

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h1 className="text-lg font-bold flex items-center gap-2">
          <Globe className="w-5 h-5 text-primary" />
          全市场数据中台
        </h1>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">健康度</span>
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
              {(score * 100).toFixed(0)}%
            </Badge>
            <span className="text-xs text-muted-foreground">
              {onlineCount}/{totalCount} 在线
            </span>
          </div>
          {Object.entries(venues).map(([v, info]: [string, any]) => (
            <span
              key={v}
              className={cn(
                "text-[10px] px-1.5 py-0.5 rounded",
                info.healthy ? "text-profit bg-profit/10" : "text-loss bg-loss/10"
              )}
            >
              {v} {info.healthy ? "✅" : "❌"}
            </span>
          ))}
          {health?.data_center && (
            <span
              className={cn(
                "text-[10px] px-1.5 py-0.5 rounded",
                health.data_center.online ? "text-profit bg-profit/10" : "text-loss bg-loss/10"
              )}
            >
              数据中心 {health.data_center.online ? "✅" : "❌"}
            </span>
          )}
          <Button variant="ghost" size="sm" onClick={refetch}>
            <RefreshCw className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>

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
        <DataQualityPanel />
      ) : tab === "overview" ? (
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <div className="flex items-center gap-1 flex-wrap">
              <span className="text-xs text-muted-foreground mr-1">交易所:</span>
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
                    "px-2.5 py-1 text-xs rounded border transition-colors",
                    exchange === e.k
                      ? "border-primary bg-primary/10 text-primary font-medium"
                      : "border-border text-muted-foreground hover:text-foreground"
                  )}
                >
                  {e.label}
                </button>
              ))}
            </div>
            <span className="text-[10px] text-muted-foreground">点击交易对可打开对应 K 线</span>
          </div>
          <MarketOverviewTable
            rows={allMarket?.rows ?? []}
            loading={allLoading}
            fetchedAt={allMarket?.fetched_at}
            source={allMarket?.source}
            onSymbolClick={openKline}
          />
        </div>
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
                <Card key={sym} className="p-4">
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-sm font-medium">{sym}</span>
                    <Badge
                      variant="secondary"
                      className={cn("text-[9px]", ob.available ? "text-profit" : "text-muted-foreground")}
                    >
                      {ob.active_venues ?? 0}所 {ob.available ? "✅" : "❌"}
                    </Badge>
                  </div>
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
                        <span className="text-profit">
                          买 {ob.best_bid ? `$${ob.best_bid.toLocaleString()}` : "—"}
                        </span>
                        <span className="text-loss">
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
                                <span>
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
                </Card>
              );
            }

            if (tab === "oi") {
              return (
                <Card key={sym} className="p-4">
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-sm font-medium">{sym} OI/费率</span>
                    <Badge
                      variant="secondary"
                      className={cn("text-[9px]", mk.available ? "text-profit" : "text-muted-foreground")}
                    >
                      {mk.available ? "✅" : "❌"}
                    </Badge>
                  </div>
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
                </Card>
              );
            }

            return (
              <Card key={sym} className="p-4">
                <div className="flex items-center justify-between mb-3">
                  <span className="text-sm font-medium">{sym} 鲸鱼/资金流</span>
                  <Badge
                    variant="secondary"
                    className={cn("text-[9px]", wh?.available ? "text-profit" : "text-muted-foreground")}
                  >
                    {wh?.available ? "✅" : "❌"}
                  </Badge>
                </div>
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
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
