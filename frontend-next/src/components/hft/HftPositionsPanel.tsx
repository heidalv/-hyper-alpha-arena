"use client";

/**
 * [F250] 中短期高频交易 · 持仓面板
 *
 * ## 为什么单独拆出来（用户 2026-09-20 三条要求）
 *
 *  ① **"持仓数据必须实时"** —— 原先持仓混在「账户与控制」卡片里，靠
 *     `useHftAccount` 的 **10s** 轮询；而深度梯是 2s。同屏两个节奏会让
 *     "挂单价动了但持仓没动"看起来像 bug。本组件由页面用 **2s 的 board**
 *     数据驱动（`position` 字段随每张卡片一起回来）。
 *
 *  ② **"数字太小了，也不明显"** —— 原先是 11px 小字网格里的一行。
 *     这里改为**大号数字块**（text-xl/2xl）+ 明显的正负配色 + 逐币明细条。
 *
 *  ③ **"小数点后四位不就好了，为什么非要后两位"** —— 这条批评是对的，没有正当理由。
 *     本模块单腿名义只有 $30，一笔 1.5bp 的往返赚 $0.0045；
 *     `fmtUsd` 默认 2 位 ⇒ 永远显示 `$0.00`，等于把信息抹掉。
 *     ⇒ 盈亏/持仓一律 **4 位小数**（价格按品种精度，至少 4 位）。
 *
 * ## 数据来源与一致性
 *
 * 持仓取自 `/api/hft/board` 的 `cards[].position`（引擎运行态，2s 刷新），
 * 与「实时深度」块**同源同节奏** ⇒ 不会出现两块对不上的情况。
 * 账户余额/开关等低频信息仍走 `/api/hft/account`（10s）。
 */
import { useMemo } from "react";
import { Coins } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { DataState } from "@/components/arbitrage";
import type { HftBoardCard } from "@/lib/hft-api";

/** 4 位小数 —— 这个尺度下的最小可用精度（见文件头 ③）。 */
function usd4(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v < 0 ? "−" : v > 0 ? "+" : "";
  return `${sign}$${Math.abs(v).toFixed(4)}`;
}

function px(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  // 价格至少 4 位；极小价（如 1000SHIB 0.00535）给 6 位，否则等于没有信息
  const d = a >= 100 ? 2 : a >= 1 ? 4 : a >= 0.01 ? 5 : 6;
  return v.toFixed(d);
}

function qty(v: number): string {
  const a = Math.abs(v);
  const d = a >= 1000 ? 2 : a >= 1 ? 4 : 6;
  return v.toFixed(d);
}

interface PosRow {
  symbol: string;
  qty: number;
  avgMid: number | null;
  mid: number | null;
  upl: number | null;
  holdMs: number | null;
}

function fmtHold(ms: number | null): string {
  if (ms == null || ms <= 0) return "—";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m${s % 60}s` : `${Math.floor(m / 60)}h${m % 60}m`;
}

export function HftPositionsPanel({
  cards,
  className,
  stateAgeMs,
  stateSource,
}: {
  cards: HftBoardCard[] | null | undefined;
  className?: string;
  /** 运行态快照年龄（ms）——如实展示，不要假装是实时的 */
  stateAgeMs?: number | null;
  stateSource?: string | null;
}) {
  const { rows, grossUsd, netUsd, totalUpl, netQty } = useMemo(() => {
    const out: PosRow[] = [];
    let gross = 0;
    let net = 0;
    let upl = 0;
    let nq = 0;
    for (const c of cards ?? []) {
      const p = c.position;
      if (!p || Math.abs(p.qty ?? 0) <= 1e-12) continue;
      const mid = c.mid;
      const notional = mid ? Math.abs(p.qty) * mid : 0;
      gross += notional;
      net += mid ? p.qty * mid : 0;
      nq += p.qty;
      if (p.unrealized_usd != null) upl += p.unrealized_usd;
      out.push({
        symbol: c.symbol,
        qty: p.qty,
        avgMid: p.avg_mid,
        mid,
        upl: p.unrealized_usd,
        holdMs: p.hold_ms ?? null,
      });
    }
    out.sort((a, b) => Math.abs(b.upl ?? 0) - Math.abs(a.upl ?? 0));
    return { rows: out, grossUsd: gross, netUsd: net, totalUpl: upl, netQty: nq };
  }, [cards]);

  const tone = (v: number | null) =>
    v == null ? "text-muted-foreground" : v > 0 ? "text-profit" : v < 0 ? "text-loss" : "text-foreground";

  return (
    <Card className={cn("glass p-4", className)}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <span className="flex h-6 w-6 items-center justify-center rounded-lg border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
            <Coins className="h-3.5 w-3.5" />
          </span>
          持仓（实时）
        </h2>
        <span className="text-[11px] text-muted-foreground">
          2s 刷新 · 与深度同源
          {/* 运行态新鲜度如实标注：快照由 worker 每 15s 写出 ⇒ 通常 0~15s。
              `in_process_runner` 说明退回了 HTTP 进程内的缓存实例（会陈旧），
              必须显著提示，否则用户会以为持仓卡住了。 */}
          {stateSource === "in_process_runner" && (
            <span className="ml-2 rounded border border-warning/40 bg-warning/10 px-1.5 py-[1px] text-warning">
              运行态来源异常（进程内缓存，可能陈旧）
            </span>
          )}
          {stateSource === "snapshot_file" && stateAgeMs != null && (
            <span className="ml-2 font-mono tabular-nums">
              运行态 {stateAgeMs < 1000 ? `${stateAgeMs}ms` : `${(stateAgeMs / 1000).toFixed(0)}s`} 前
            </span>
          )}
        </span>
      </div>

      <DataState
        loading={!cards}
        error={null}
        hasData={!!cards}
        empty={!!cards && rows.length === 0}
        emptyHint="当前无持仓（空仓是正常状态，不是数据缺失）"
      >
        {/* ── 汇总：大号数字（用户要求"明显"） ── */}
        <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div className="rounded-lg border border-muted/40 bg-muted/10 px-3 py-2">
            <div className="text-[10px] text-muted-foreground">持仓浮盈（未实现）</div>
            <div className={cn("font-mono text-xl font-bold tabular-nums", tone(totalUpl))}>
              {usd4(totalUpl)}
            </div>
          </div>
          <div className="rounded-lg border border-muted/40 bg-muted/10 px-3 py-2">
            <div className="text-[10px] text-muted-foreground">持仓数</div>
            <div className="font-mono text-xl font-bold tabular-nums">{rows.length}</div>
          </div>
          <div className="rounded-lg border border-muted/40 bg-muted/10 px-3 py-2">
            <div className="text-[10px] text-muted-foreground">总敞口</div>
            <div className="font-mono text-xl font-bold tabular-nums">${grossUsd.toFixed(2)}</div>
          </div>
          <div className="rounded-lg border border-muted/40 bg-muted/10 px-3 py-2">
            <div className="text-[10px] text-muted-foreground">净敞口</div>
            <div className={cn("font-mono text-xl font-bold tabular-nums", tone(netUsd))}>
              {netUsd < 0 ? "−" : netUsd > 0 ? "+" : ""}${Math.abs(netUsd).toFixed(2)}
            </div>
          </div>
        </div>

        {rows.length > 0 && (
          <>
            {/* ── 逐币明细：大字 + 4 位小数 ── */}
            {/* [fix 2026-10-07] 持仓明细加高度上限+内部滚动：持仓币数多时
                不再把页面无限向下撑长（用户反馈"页面不断叠加、装不下"）。 */}
            <div className="max-h-[340px] space-y-1.5 overflow-y-auto pr-1">
              {rows.map((r) => {
                const long = r.qty > 0;
                return (
                  <div
                    key={r.symbol}
                    className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-muted/30 bg-background/40 px-3 py-2"
                  >
                    <span className="w-20 flex-shrink-0 text-sm font-semibold">{r.symbol}</span>
                    <span
                      className={cn(
                        "rounded px-1.5 py-[1px] text-[10px] font-semibold",
                        long ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"
                      )}
                    >
                      {long ? "多" : "空"}
                    </span>
                    <span className="font-mono text-base font-semibold tabular-nums">
                      {qty(r.qty)}
                    </span>
                    <span className="text-[11px] text-muted-foreground">
                      开仓 <span className="font-mono tabular-nums text-foreground">{px(r.avgMid)}</span>
                    </span>
                    <span className="text-[11px] text-muted-foreground">
                      现价 <span className="font-mono tabular-nums text-foreground">{px(r.mid)}</span>
                    </span>
                    <span className="text-[11px] text-muted-foreground">
                      持有 <span className="font-mono tabular-nums">{fmtHold(r.holdMs)}</span>
                    </span>
                    <span className={cn("ml-auto font-mono text-base font-bold tabular-nums", tone(r.upl))}>
                      {usd4(r.upl)}
                    </span>
                  </div>
                );
              })}
            </div>
            <p className="mt-2 text-[10px] text-muted-foreground">
              浮盈口径与引擎一致：<span className="font-mono">qty × (mid − avg_mid)</span>；
              全程 4 位小数（本模块单腿仅 $30，2 位会把盈亏显示成 $0.00）。
              净 qty 合计 {netQty.toFixed(6)}。
            </p>
          </>
        )}
      </DataState>
    </Card>
  );
}
