"use client";

import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Search, ArrowUp, ArrowDown, ArrowUpDown, Loader2, Database } from "lucide-react";
import { cn } from "@/lib/utils";

type Row = {
  exchange?: string;
  symbol: string;
  price: number;
  change_pct: number;
  high_24h: number;
  low_24h: number;
  volume_24h: number;
  quote_volume_24h: number;
  trades_24h: number;
  active: boolean;
};

type SortKey = "symbol" | "price" | "change_pct" | "high_24h" | "low_24h" | "volume_24h" | "quote_volume_24h";

const COLUMNS: { key: SortKey; label: string; numeric?: boolean; width?: string }[] = [
  { key: "symbol", label: "交易对", width: "w-28" },
  { key: "price", label: "最新价", numeric: true },
  { key: "change_pct", label: "24h涨跌", numeric: true },
  { key: "high_24h", label: "24h最高", numeric: true },
  { key: "low_24h", label: "24h最低", numeric: true },
  { key: "volume_24h", label: "24h成交量", numeric: true },
  { key: "quote_volume_24h", label: "24h成交额", numeric: true },
];

function fmtPrice(v: number): string {
  if (!v || v <= 0) return "—";
  if (v >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (v >= 1) return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return v.toFixed(6);
}

function fmtVol(v: number): string {
  if (!v || v <= 0) return "—";
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(2)}K`;
  return v.toFixed(2);
}

export default function MarketOverviewTable({
  rows,
  loading,
  fetchedAt,
  source,
  onSymbolClick,
}: {
  rows: Row[];
  loading?: boolean;
  fetchedAt?: number;
  source?: string;
  /** 点击交易对 → 跳转 K 线等 */
  onSymbolClick?: (row: Row) => void;
}) {
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("quote_volume_24h");
  const [sortDesc, setSortDesc] = useState(true);

  const filtered = useMemo(() => {
    const q = query.trim().toUpperCase();
    const qq = q.replace(/USDT$/i, "");
    let list = rows;
    if (q) {
      list = rows.filter(
        (r) => r.symbol.includes(q) || r.symbol.includes(qq) || r.symbol.startsWith(qq)
      );
    }
    const sorted = [...list].sort((a, b) => {
      // 搜索或排序时，交易中始终置顶
      if (a.active !== b.active) return a.active ? -1 : 1;
      const av = a[sortKey];
      const bv = b[sortKey];
      const cmp =
        typeof av === "string" || typeof bv === "string"
          ? String(av).localeCompare(String(bv))
          : Number(av || 0) - Number(bv || 0);
      return sortDesc ? -cmp : cmp;
    });
    return sorted;
  }, [rows, query, sortKey, sortDesc]);

  const activeCount = rows.filter((r) => r.active).length;
  const ageSec = fetchedAt ? Math.max(0, Math.floor(Date.now() / 1000 - fetchedAt)) : null;
  const showExchange = rows.some((r) => r.exchange);
  const columns = showExchange
    ? ([{ key: "exchange" as SortKey, label: "交易所", width: "w-24" }] as typeof COLUMNS).concat(COLUMNS)
    : COLUMNS;

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDesc(!sortDesc);
    } else {
      setSortKey(key);
      setSortDesc(key === "symbol" ? false : true);
    }
  };

  return (
    <div className="space-y-2">
      {/* 工具行：搜索 + 统计 */}
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="模糊搜索交易对，如 BTC / SOL / PEPE"
              className="w-64 pl-8 pr-3 py-1.5 text-sm bg-card border border-border rounded-md outline-none focus:border-primary/50"
            />
          </div>
          <Badge variant="secondary" className="text-[10px]">
            共 {rows.length} 对
          </Badge>
          <Badge variant="secondary" className={cn("text-[10px]", activeCount > 0 ? "bg-profit/15 text-profit" : "")}>
            交易中 {activeCount}
          </Badge>
        </div>
        <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
          {source && (
            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-primary/10 text-primary">
              <Database className="w-3 h-3" /> {source}
            </span>
          )}
          {ageSec !== null && <span>{ageSec}s 前更新</span>}
          {loading && <Loader2 className="w-3 h-3 animate-spin" />}
        </div>
      </div>

      {/* 表格 */}
      <div className="border border-border rounded-lg overflow-hidden">
        <div className="max-h-[62vh] overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-card/95 backdrop-blur z-10">
              <tr className="border-b border-border text-xs text-muted-foreground">
                {columns.map((c) => (
                  <th
                    key={c.key}
                    className={cn("px-3 py-2 text-left font-medium select-none cursor-pointer hover:text-foreground whitespace-nowrap", c.numeric && "text-right", c.width)}
                    onClick={() => toggleSort(c.key)}
                  >
                    <span className="inline-flex items-center gap-1">
                      {c.label}
                      {sortKey === c.key ? (
                        sortDesc ? <ArrowDown className="w-3 h-3" /> : <ArrowUp className="w-3 h-3" />
                      ) : (
                        <ArrowUpDown className="w-3 h-3 opacity-40" />
                      )}
                    </span>
                  </th>
                ))}
                <th className="px-3 py-2 text-left font-medium whitespace-nowrap">状态</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r) => {
                const up = r.change_pct >= 0;
                return (
                  <tr
                    key={`${r.exchange || "x"}-${r.symbol}`}
                    role={onSymbolClick ? "button" : undefined}
                    title={onSymbolClick ? `查看 ${r.symbol} K 线` : undefined}
                    onClick={onSymbolClick ? () => onSymbolClick(r) : undefined}
                    className={cn(
                      "border-b border-border/40 last:border-0 hover:bg-muted/40 transition-colors",
                      r.active && "bg-profit/5",
                      onSymbolClick && "cursor-pointer hover:bg-primary/5"
                    )}
                  >
                    {showExchange && (
                      <td className="px-3 py-1.5">
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted/40 text-muted-foreground uppercase">
                          {r.exchange}
                        </span>
                      </td>
                    )}
                    <td className="px-3 py-1.5">
                      <span className="font-medium">{r.symbol}</span>
                      <span className="text-muted-foreground text-[10px] ml-1">/USDT</span>
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">{fmtPrice(r.price)}</td>
                    <td className={cn("px-3 py-1.5 text-right tabular-nums font-medium", up ? "text-profit" : "text-loss")}>
                      {r.change_pct === 0 && r.price === 0 ? "—" : `${up ? "+" : ""}${r.change_pct.toFixed(2)}%`}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">{fmtPrice(r.high_24h)}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">{fmtPrice(r.low_24h)}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">{fmtVol(r.volume_24h)}</td>
                    <td className="px-3 py-1.5 text-right tabular-nums">{fmtVol(r.quote_volume_24h)}</td>
                    <td className="px-3 py-1.5">
                      {r.active ? (
                        <Badge className="text-[9px] bg-profit/15 text-profit border border-profit/30">交易中</Badge>
                      ) : (
                        <span className="text-[10px] text-muted-foreground">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={columns.length + 1} className="px-3 py-8 text-center text-muted-foreground text-sm">
                    没有匹配的交易对
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
