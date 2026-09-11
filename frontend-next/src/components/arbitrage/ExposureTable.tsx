"use client";

/**
 * ExposureTable — 敞口表（单币 / 总敞口 / 上限 / 使用率）
 *
 * 设计 §3.5：跨车道的敞口汇总。数据来源 `/positions`（open_items）+
 * `/risk/summary`（equity、limits.max_symbol_exposure_pct、limits.max_net_exposure_pct）。
 * 单币使用率 = |该币净敞口| / 权益；总使用率 = |总净敞口| / 权益。
 */
import { useMemo } from "react";
import { cn } from "@/lib/utils";
import { fmtUsd, fmtPct } from "@/lib/format";
import type { Position } from "@/lib/trading-api";

export function ExposureTable({
  positions,
  equity,
  maxSymbolPct,
  maxNetPct,
  className,
}: {
  positions?: Position[] | null;
  equity?: number | null;
  maxSymbolPct?: number | null;
  maxNetPct?: number | null;
  className?: string;
}) {
  const s = useMemo(() => {
    const open = (positions ?? []).filter((p) => Math.abs(p.qty) > 1e-12);
    let gross = 0;
    let net = 0;
    const bySymbol = new Map<string, { long: number; short: number }>();
    for (const p of open) {
      const notional = p.notional_usd || 0;
      gross += notional;
      const signed = p.qty > 0 ? notional : -notional;
      net += signed;
      const cur = bySymbol.get(p.symbol) ?? { long: 0, short: 0 };
      if (p.qty > 0) cur.long += notional;
      else cur.short += notional;
      bySymbol.set(p.symbol, cur);
    }
    const symbols = Array.from(bySymbol.entries()).sort((a, b) => (b[1].long + b[1].short) - (a[1].long + a[1].short));
    return { openCount: open.length, gross, net, symbols };
  }, [positions]);

  const eq = equity && equity > 0 ? equity : null;
  const netPct = eq ? (Math.abs(s.net) / eq) * 100 : null;
  const maxNet = maxNetPct ?? null;
  const overNet = maxNet != null && netPct != null && netPct > maxNet;

  if (s.openCount === 0) {
    return (
      <div className={cn("rounded-lg border border-muted/40 bg-muted/20 px-3 py-5 text-center text-xs text-muted-foreground", className)}>
        当前无持仓，敞口为 0
      </div>
    );
  }

  return (
    <div className={cn("space-y-3", className)}>
      {/* 汇总行 */}
      <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <Cell label="总敞口" value={fmtUsd(s.gross)} />
        <Cell label="净敞口" value={`${fmtUsd(s.net)}${netPct != null ? ` · ${fmtPct(netPct, 1)}` : ""}`} tone={s.net >= 0 ? "profit" : "loss"} />
        <Cell label="净敞口上限" value={maxNet != null ? fmtPct(maxNet, 1) : "—"} />
        <Cell
          label="净敞口使用率"
          value={netPct != null ? fmtPct(netPct, 1) : "—"}
          tone={netPct != null && maxNet != null && netPct > maxNet ? "loss" : "profit"}
        />
      </div>
      {overNet && <div className="text-[11px] text-loss">总净敞口已超出净敞口上限（{maxNet}%）</div>}

      {/* 单币敞口表 */}
      <div className="overflow-x-auto rounded-xl border border-border/40">
        <table className="data-table">
          <thead>
            <tr className="text-muted-foreground border-b border-border">
              <th className="text-left">标的</th>
              <th className="text-right">多头名义</th>
              <th className="text-right">空头名义</th>
              <th className="text-right">净敞口</th>
              <th className="text-right">单币使用率</th>
              <th className="text-right">单币上限</th>
            </tr>
          </thead>
          <tbody>
            {s.symbols.map(([sym, v]) => {
              const netSym = v.long - v.short;
              const symPct = eq ? (Math.abs(netSym) / eq) * 100 : null;
              const cap = maxSymbolPct ?? null;
              const over = cap != null && symPct != null && symPct > cap;
              return (
                <tr key={sym} className="border-b border-border/20">
                  <td className="font-medium">{sym}</td>
                  <td className="text-right font-mono tabular-nums text-profit">{fmtUsd(v.long)}</td>
                  <td className="text-right font-mono tabular-nums text-loss">{fmtUsd(v.short)}</td>
                  <td className={cn("text-right font-mono tabular-nums font-semibold", netSym >= 0 ? "text-profit" : "text-loss")}>{fmtUsd(netSym)}</td>
                  <td className={cn("text-right font-mono tabular-nums", over ? "text-loss" : "text-foreground")}>
                    {symPct != null ? fmtPct(symPct, 1) : "—"}
                  </td>
                  <td className="text-right font-mono tabular-nums text-muted-foreground">{cap != null ? fmtPct(cap, 1) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="text-[11px] text-muted-foreground">单币/净敞口上限来源：/api/trading/risk/summary（lane 风控最大值）。</div>
    </div>
  );
}

function Cell({ label, value, tone }: { label: string; value: string; tone?: "profit" | "loss" }) {
  return (
    <div className="rounded-md bg-muted/20 px-3 py-2">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className={cn("font-mono tabular-nums font-semibold", tone === "profit" && "text-profit", tone === "loss" && "text-loss")}>{value}</div>
    </div>
  );
}
