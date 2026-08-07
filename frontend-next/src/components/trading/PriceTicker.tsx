"use client";

import { useEffect, useRef, useState } from "react";
import { useMarketStore } from "@/lib/stores/market";
import { cn } from "@/lib/utils";

interface PriceTickerProps {
  symbol: string;
  className?: string;
  size?: "sm" | "md" | "lg";
}

/**
 * 实时价格组件 — 价格变化时闪烁绿/红
 * 从 Zustand market store 读取，WebSocket 推送驱动
 */
export function PriceTicker({ symbol, className, size = "md" }: PriceTickerProps) {
  const priceData = useMarketStore((s) => s.prices[symbol]);
  const [flash, setFlash] = useState<"profit" | "loss" | null>(null);
  const prevPriceRef = useRef<number | undefined>(priceData?.price);

  const currentPrice = priceData?.price ?? 0;

  useEffect(() => {
    const prev = prevPriceRef.current;
    if (prev !== undefined && currentPrice !== prev) {
      setFlash(currentPrice > prev ? "profit" : "loss");
      const timer = setTimeout(() => setFlash(null), 400);
      prevPriceRef.current = currentPrice;
      return () => clearTimeout(timer);
    }
    prevPriceRef.current = currentPrice;
  }, [currentPrice]);

  const sizeClass = {
    sm: "text-xs",
    md: "text-sm",
    lg: "text-lg",
  }[size];

  const formatPrice = (p: number) => {
    if (p >= 1000) return p.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (p >= 1) return p.toFixed(3);
    return p.toFixed(5);
  };

  return (
    <span
      className={cn(
        "font-mono tabular-nums font-medium transition-colors",
        sizeClass,
        flash === "profit" && "text-profit",
        flash === "loss" && "text-loss",
        !flash && "text-foreground",
        className
      )}
    >
      ${formatPrice(currentPrice)}
    </span>
  );
}
