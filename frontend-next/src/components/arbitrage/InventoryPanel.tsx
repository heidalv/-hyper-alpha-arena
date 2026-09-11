"use client";

/**
 * InventoryPanel — 库存/敞口 + 进度条（含上限百分比）
 *
 * 设计 §3.2：按标的展示敞口、净敞口、上限使用率；单边持仓最久。
 * 输入来自 `/positions`（open_items 判定 abs(qty)>0）。
 */
import { useMemo } from "react";
import { cn } from "@/lib/utils";
import { fmtUsd, fmtNum } from "@/lib/format";
import type { Position } from "@/lib/trading-api";

function fmtHold(sec: number): string {
  if (!sec || sec <= 0) return "—";
  const s = Math.floor(sec);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m < 60 ? `${m}m${r}s` : `${Math.floor(m / 60)}h${m % 60}m`;
}

export function InventoryPanel({
  positions,
  equity,
  limitPct,
  limitLabel = "敞口上限",
  className,
}: {
  positions?: Position[] | null;
  equity?: number | null;
  limitPct?: number | null;
  limitLabel?: string;
  className?: string;
}) {
  const s = useMemo(() => {
    const list = (positions ?? []).filter((p) => Math.abs(p.qty) > 1e-12);
    let gross = 0;
    let net = 0;
    let unrealized = 0;
    let maxHold = 0;
    const bySymbol = new Map<string, { long: number; short: number; unrealized: number; hold: number }>();
    for (const p of list) {
      gross += p.notional_usd;
      net += p.notional_usd * (p.qty > 0 ? 1 : -1);
      unrealized += p.unrealized_usd;
      maxHold = Math.max(maxHold, p.hold_sec);
      const cur = bySymbol.get(p.symbol) ?? { long: 0, short: 0, unrealized: 0, hold: 0 };
      if (p.qty > 0) cur.long += p.notional_usd;
      else cur.short += p.notional_usd;
      cur.unrealized += p.unrealized_usd;
      cur.hold = Math.max(cur.hold, p.hold_sec);
      bySymbol.set(p.symbol, cur);
    }
    const symbols = Array.from(bySymbol.entries()).sort((a, b) => (b[1].long + b[1].short) - (a[1].long + a[1].short));
    return { list, gross, net, unrealized, maxHold, symbols };
  }, [positions]);

  const netPct = equity ? (Math.abs(s.net) / equity) * 100 : 0;
  const limit = limitPct != null ? limitPct : null;
  const overLimit = limit != null && netPct > limit;

  if (s.list.length === 0) {
    return (
      <div className={cn("rounded-lg border border-muted/40 bg-muted/20 px-3 py-5 text-center text-xs text-muted-foreground", className)}>
        当前无持仓，敞口为 0（不代表遗漏，实为空）
      </div>
    );
  }

  const maxNotional = Math.max(s.gross, 1e-9);

  return (
    <div className={cn("space-y-3", className)}>
      {/* 汇总 */}
      <div className="grid grid-cols-3 gap-2 text-xs">
        <div>
          <div className="text-muted-foreground">总敞口</div>
          <div className="font-mono tabular-nums font-semibold">{fmtUsd(s.gross)}</div>
        </div>
        <div>
          <div className="text-muted-foreground">净敞口</div>
          <div className={cn("font-mono tabular-nums font-semibold", s.net >= 0 ? "text-profit" : "text-loss")}>{fmtUsd(s.net)}</div>
        </div>
        <div>
          <div className="text-muted-foreground">浮动盈亏</div>
          <div className={cn("font-mono tabular-nums font-semibold", s.unrealized >= 0 ? "text-profit" : "text-loss")}>{fmtUsd(s.unrealized)}</div>
        </div>
      </div>

      {/* 按标的敞口条 */}
      <div className="space-y-1.5">
        {s.symbols.map(([sym, v]) => {
          return (
            <div key={sym} className="text-[11px]">
              <div className="mb-0.5 flex items-center justify-between gap-2">
                <span className="font-medium">{sym}</span>
                <span className="font-mono tabular-nums text-muted-foreground">
                  多 {fmtUsd(v.long)} · 空 {fmtUsd(v.short)}
                </span>
              </div>
              <div className="flex h-1.5 w-full items-stretch gap-px overflow-hidden rounded-full bg-muted/30">
                {/* 多头（绿） */}
                <div className="h-full bg-profit/70" style={{ width: `${(v.long / maxNotional) * 100}%` }} />
                {/* 空头（红） */}
                <div className="h-full bg-loss/70" style={{ width: `${(v.short / maxNotional) * 100}%` }} />
              </div>
            </div>
          );
        })}
      </div>

      {/* 敞口使用率 vs 上限 */}
      {limit != null && (
        <div>
          <div className="mb-1 flex items-center justify-between text-[11px]">
            <span className="text-muted-foreground">{limitLabel}</span>
            <span className={cn("font-mono tabular-nums", overLimit ? "text-loss" : "text-foreground")}>
              {netPct.toFixed(1)}% <span className="text-muted-foreground">/ {limit}%</span>
            </span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted/30">
            <div
              className={cn("h-full", overLimit ? "bg-loss" : "bg-cyan-400")}
              style={{ width: `${Math.min(100, (netPct / limit) * 100)}%` }}
            />
          </div>
          {overLimit && <div className="mt-1 text-[11px] text-loss">已超出 {limitLabel}（{limit}%）</div>}
        </div>
      )}

      {/* 单边持仓最久 */}
      <div className="flex items-center justify-between text-[11px] text-muted-foreground">
        <span>单边持仓最久</span>
        <span className="font-mono tabular-nums">{fmtHold(s.maxHold)}</span>
      </div>

      {/* 细分归因（可选，行内展开用） */}
      {s.list.length > 0 && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-[11px] text-muted-foreground sm:grid-cols-3">
          {s.list.map((p) => (
            <div key={`${p.lane_id}-${p.symbol}`} className="flex items-center justify-between gap-2">
              <span className="truncate">{p.symbol}</span>
              <span className="font-mono tabular-nums">
                {p.qty > 0 ? "多" : "空"}·{fmtNum(Math.abs(p.qty), 4)}@{fmtUsd(p.avg_px)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
