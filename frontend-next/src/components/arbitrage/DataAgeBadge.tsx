"use client";

/**
 * DataAgeBadge — `as of HH:MM:SS` + 数据年龄判定
 *
 * 设计 §4：每个数据块必须有数据年龄。as_of 可能为 UTC(+00:00) 或 +08:00，
 * 统一 `new Date(as_of)` 解析后按本地时区展示 HH:MM:SS；年龄 > 90s 判定为 stale，
 * 灰化并提示「数据偏旧」，同时仍给出实际年龄。
 */
import { useMemo } from "react";
import { Clock } from "lucide-react";
import { cn } from "@/lib/utils";
import { ageFromAsOf, fmtAgeMs } from "@/lib/trading-api";

export function DataAgeBadge({
  asOf,
  staleAfterMs = 90_000,
  className,
}: {
  asOf?: string | null;
  staleAfterMs?: number;
  className?: string;
}) {
  const { timeLabel, ageMs, stale } = useMemo(() => {
    if (!asOf) {
      return { timeLabel: "时间未知", ageMs: null, stale: false };
    }
    const raw = new Date(asOf);
    const valid = !Number.isNaN(raw.getTime());
    const timeLabel = valid
      ? raw.toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" })
      : asOf;
    const age = ageFromAsOf(asOf);
    return { timeLabel, ageMs: age, stale: age != null && age > staleAfterMs };
  }, [asOf, staleAfterMs]);

  if (!asOf) {
    return (
      <span className={cn("inline-flex items-center gap-1 text-[11px] text-muted-foreground", className)}>
        <Clock className="h-3 w-3" /> 时间未知
      </span>
    );
  }

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 font-mono text-[11px]",
        stale ? "text-loss/80" : "text-muted-foreground",
        className
      )}
      title={stale ? "数据已超过判定窗口，可能偏旧" : undefined}
    >
      <Clock className={cn("h-3 w-3", stale && "text-loss/80")} />
      <span>as of {timeLabel}</span>
      <span className={cn("rounded px-1", stale ? "bg-loss/15 text-loss" : "bg-muted/40 text-muted-foreground")}>
        {stale ? `偏旧 ${fmtAgeMs(ageMs)}` : fmtAgeMs(ageMs)}
      </span>
    </span>
  );
}
