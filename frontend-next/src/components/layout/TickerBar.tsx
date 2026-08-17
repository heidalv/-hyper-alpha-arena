"use client";

import { useEffect, useRef, useState } from "react";
import { useMarketStore } from "@/lib/stores/market";
import { cn } from "@/lib/utils";

/** 固定币备选池（常驻显示） */
const FIXED_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "VIRTUAL", "ASTER", "XPL"] as const;

const PRICE_POLL_MS = 1_000;   // 数据中心价格轮询（秒级 ticker，1s 刷新）
const AUTO_POLL_MS = 15_000;   // AI 选币列表轮询

interface PriceEntry {
  price: number;
  changePct: number;
}

function fmtPrice(symbol: string, price: number): string {
  if (["BTC", "ETH", "SOL", "BNB"].includes(symbol)) {
    return price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  return price.toLocaleString("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 4 });
}

/**
 * 全局行情 ticker 条（Aurora 设计稿签名元素）
 * - 固定币备选池常驻显示
 * - AI 选币动态追加（交易对更换时立即刷新为最新）
 * - 数据源：后端 /api/market/ticker-bar（数据中心进程内存直读，1s 稳定低时延）
 */
export function TickerBar() {
  const wsConnected = useMarketStore((s) => s.wsConnected);
  const [prices, setPrices] = useState<Record<string, PriceEntry>>({});
  const [autoSymbols, setAutoSymbols] = useState<string[]>([]);
  const [flash, setFlash] = useState<Record<string, "up" | "down">>({});
  const prevRef = useRef<Record<string, number>>({});

  // AI 选币动态列表
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch("/api/auto-coin/active-symbols");
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled || !Array.isArray(data?.auto_symbols)) return;
        setAutoSymbols(
          data.auto_symbols
            .map((s: string) => String(s).trim().toUpperCase())
            .filter(Boolean)
        );
      } catch { /* 忽略轮询错误 */ }
    };
    void load();
    const id = setInterval(() => void load(), AUTO_POLL_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // 数据中心价格轮询（固定币 + AI 选币动态并集）
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const all = [...new Set([...FIXED_SYMBOLS, ...autoSymbols])];
      if (!all.length) return;
      try {
        const res = await fetch(`/api/market/ticker-bar?symbols=${all.join(",")}`);
        if (!res.ok) return;
        const rows = await res.json();
        if (cancelled || !Array.isArray(rows)) return;
        const next: Record<string, PriceEntry> = {};
        const flashNext: Record<string, "up" | "down"> = {};
        for (const r of rows) {
          const sym = String(r?.symbol || "").toUpperCase();
          const price = Number(r?.price);
          if (!sym || !Number.isFinite(price) || price <= 0) continue;
          next[sym] = { price, changePct: Number(r?.percentage24h || 0) };
          const prev = prevRef.current[sym];
          if (prev !== undefined && price !== prev) {
            flashNext[sym] = price > prev ? "up" : "down";
          }
          prevRef.current[sym] = price;
        }
        setPrices(next);
        if (Object.keys(flashNext).length) {
          setFlash(flashNext);
          setTimeout(() => setFlash({}), 500);
        }
      } catch { /* 忽略轮询错误 */ }
    };
    void load();
    const id = setInterval(() => void load(), PRICE_POLL_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, [autoSymbols]);

  const symbols = [...new Set([...FIXED_SYMBOLS, ...autoSymbols])];

  return (
    <div className="sticky top-0 z-30 flex items-center px-4 py-2 mx-4 mb-3 rounded-xl border border-border bg-[#090e19]/80 backdrop-blur-md shadow-[0_8px_24px_rgba(3,6,14,0.4)] overflow-x-auto">
      {symbols.map((sym, i) => {
        const p = prices[sym];
        const isAuto = autoSymbols.includes(sym);
        const up = (p?.changePct ?? 0) >= 0;
        return (
          <div key={sym} className={cn("flex items-center gap-2 px-4 flex-shrink-0", i > 0 && "border-l border-white/5")}>
            <span className="flex items-center gap-1 text-[11px] font-semibold tracking-wide text-slate-400 uppercase">
              {sym}
              {isAuto && (
                <span className="px-1 py-px rounded bg-cyan-400/15 text-cyan-300 text-[8px] font-bold" title="AI 选币动态跟踪">
                  AI
                </span>
              )}
            </span>
            {p ? (
              <>
                <span
                  className={cn(
                    "font-mono text-[13px] font-semibold text-foreground tabular-nums rounded px-0.5",
                    flash[sym] === "up" && "flash-profit",
                    flash[sym] === "down" && "flash-loss"
                  )}
                >
                  ${fmtPrice(sym, p.price)}
                </span>
                <span className={cn("inline-flex items-center gap-1 font-mono text-[11.5px] font-semibold tabular-nums", up ? "text-profit" : "text-loss")}>
                  <span className={cn("inline-block w-0 h-0", up ? "border-x-4 border-x-transparent border-b-[5px] border-b-current" : "border-x-4 border-x-transparent border-t-[5px] border-t-current")} />
                  {up ? "+" : ""}{p.changePct.toFixed(2)}%
                </span>
              </>
            ) : (
              <span className="font-mono text-[13px] text-slate-600 tabular-nums">—</span>
            )}
          </div>
        );
      })}
      <span className="ml-auto flex items-center gap-1.5 text-[11px] font-bold tracking-widest text-profit flex-shrink-0">
        <span className={cn("w-1.5 h-1.5 rounded-full bg-profit shadow-[0_0_8px_rgba(52,211,153,0.8)]", wsConnected && "animate-pulse")} />
        {wsConnected ? "LIVE" : "轮询"}
      </span>
    </div>
  );
}
