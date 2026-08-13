"use client";

/**
 * CooldownMatrixPanel + BlockEventStream — P0-D 冷却/门禁透明化。
 * 消费只读接口：
 *   GET /api/full-auto/cooldowns/{sessionId}  → 冷却矩阵（全平/减仓/AI反向 + 倒计时）
 *   GET /api/full-auto/events/{sessionId}     → 门禁拦截事件流
 * 目的：冷却叠加导致「事实性停摆」时，面板 30s 内可见倒计时与原因。
 */
import { useQuery } from "@tanstack/react-query";
import { Card } from "@/components/ui/card";
import { ShieldAlert, ShieldCheck, Timer, Zap } from "lucide-react";
import { apiRequest } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CooldownSnapshot, SessionEventsResponse, TierCircuitState } from "@/types/api";

const TIER_LABELS: Record<string, string> = { short: "短线", mid: "中线", long: "长线", default: "默认" };

function fmtTime(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function CooldownMatrixPanel({ sessionId }: { sessionId: string | undefined }) {
  const { data } = useQuery({
    queryKey: ["p0d-cooldowns", sessionId],
    queryFn: () => apiRequest<CooldownSnapshot>(`/full-auto/cooldowns/${sessionId}`),
    enabled: Boolean(sessionId),
    refetchInterval: 10_000,
    staleTime: 5_000,
  });

  const fullClose = data?.full_close ?? [];
  const reduce = data?.reduce ?? [];
  const aiReverse = data?.ai_reverse ?? [];
  const tierBlocked = data?.tier_blocked;
  const blockedSymbols = new Set<string>();
  for (const r of fullClose) blockedSymbols.add(`${r.symbol}·${TIER_LABELS[r.tier]}`);
  for (const r of reduce) blockedSymbols.add(`${r.symbol}·${TIER_LABELS[r.tier] ?? r.tier}`);
  for (const r of aiReverse) blockedSymbols.add(r.symbol);

  return (
    <Card className="p-2.5 border-border flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h2 className="text-[13px] font-medium flex items-center gap-1.5">
          <Timer className="w-3.5 h-3.5 text-primary" />
          冷却矩阵
          {tierBlocked && (
            <span className="text-[9px] font-mono text-muted-foreground">
              {(["short", "mid", "long"] as const).map((k) => `${TIER_LABELS[k]}⛔${tierBlocked[k]?.length ?? 0}`).join(" ")}
            </span>
          )}
        </h2>
        <span className={cn("text-[9px] font-mono", blockedSymbols.size > 0 ? "text-warning" : "text-profit")}>
          {blockedSymbols.size > 0 ? `${blockedSymbols.size} 处阻挡` : "✅ 全部放行"}
        </span>
      </div>

      {/* P0-E 周期级日亏熔断 */}
      {data?.tier_circuit && (
        <div className="grid grid-cols-3 gap-1.5">
          {(["short", "mid", "long"] as const).map((k) => {
            const c: TierCircuitState | undefined = data.tier_circuit?.[k];
            if (!c) return null;
            const loss = Number(c.loss ?? 0);
            const budget = Number(c.budget ?? 0);
            return (
              <div key={k} className={cn("rounded px-1.5 py-1 border", c.frozen ? "border-loss/40 bg-loss/10" : "border-border/60 bg-muted/20")}>
                <div className="flex items-center justify-between">
                  <span className="text-[9px] font-mono flex items-center gap-1">
                    <Zap className="w-2.5 h-2.5 text-warning" />{TIER_LABELS[k]}
                  </span>
                  <span className={cn("text-[9px] font-mono", c.frozen ? "text-loss" : "text-profit")}>
                    {c.frozen ? "⛔熔断" : "✅正常"}
                  </span>
                </div>
                <div className="text-[9px] font-mono text-muted-foreground">
                  {c.loss != null ? `日 ${loss >= 0 ? "+" : ""}$${loss.toFixed(2)} / $${budget.toFixed(0)}` : "未巡检"}
                </div>
                {c.frozen && c.reason && (
                  <div className="text-[9px] text-loss leading-snug mt-0.5">{c.reason.slice(0, 60)}</div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {fullClose.length === 0 && reduce.length === 0 && aiReverse.length === 0 ? (
        <div className="text-center text-muted-foreground text-xs py-3">当前无冷却阻挡 — 各周期可正常开仓</div>
      ) : (
        <div className="flex flex-col gap-1.5 max-h-[220px] overflow-y-auto">
          {fullClose.map((r, i) => (
            <div key={`fc-${i}`} className="py-1 border-b border-border/20 last:border-0">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-medium font-mono">
                  {r.symbol} <span className="text-muted-foreground">· {TIER_LABELS[r.tier] ?? r.tier} · 平{r.closed_side === "long" ? "多" : "空"}</span>
                </span>
                <span className="text-[10px] font-mono text-warning shrink-0">同向 ⛔ {r.same_dir_remain}</span>
              </div>
              <div className="flex items-center gap-2 mt-0.5">
                <div className="flex-1 h-1 rounded-sm overflow-hidden bg-muted/20">
                  <div className="h-full bg-warning/70" style={{ width: `${Math.min(100, (r.same_dir_remain_sec / (12 * 3600)) * 100)}%` }} />
                </div>
                <span className="text-[9px] text-muted-foreground font-mono shrink-0">翻转 {r.flip_remain}</span>
              </div>
              <div className="text-[9px] text-muted-foreground mt-0.5">{r.same_dir_reason}</div>
            </div>
          ))}
          {reduce.map((r, i) => (
            <div key={`rd-${i}`} className="py-1 border-b border-border/20 last:border-0 flex items-center justify-between gap-2">
              <span className="text-xs font-mono">
                {r.symbol} <span className="text-muted-foreground">· 减仓</span>
              </span>
              <span className="text-[10px] font-mono text-warning">重仓 ⛔ {r.remain}</span>
            </div>
          ))}
          {aiReverse.map((r, i) => (
            <div key={`ar-${i}`} className="py-1 border-b border-border/20 last:border-0 flex items-center justify-between gap-2">
              <span className="text-xs font-mono">
                {r.symbol} <span className="text-muted-foreground">· AI反向</span>
              </span>
              <span className="text-[10px] font-mono text-warning">翻转 ⛔ {r.remain}</span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

export function BlockEventStream({ sessionId }: { sessionId: string | undefined }) {
  const { data } = useQuery({
    queryKey: ["p0d-events", sessionId],
    queryFn: () => apiRequest<SessionEventsResponse>(`/full-auto/events/${sessionId}?limit=12&mode=blocks`),
    enabled: Boolean(sessionId),
    refetchInterval: 10_000,
    staleTime: 5_000,
  });

  const events = data?.events ?? [];

  return (
    <Card className="p-2.5 border-border flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h2 className="text-[13px] font-medium flex items-center gap-1.5">
          <ShieldAlert className="w-3.5 h-3.5 text-warning" />
          门禁拦截流
        </h2>
        <span className="text-[9px] text-muted-foreground font-mono">近 {data?.total ?? 0} 条</span>
      </div>
      {events.length === 0 ? (
        <div className="flex items-center gap-1.5 text-center text-muted-foreground text-xs py-3 justify-center">
          <ShieldCheck className="w-3.5 h-3.5 text-profit" /> 无拦截事件
        </div>
      ) : (
        <div className="flex flex-col gap-1 max-h-[220px] overflow-y-auto">
          {events.map((e, i) => (
            <div key={`ev-${i}`} className="py-1 border-b border-border/20 last:border-0">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[10px] font-mono text-primary shrink-0">{fmtTime(e.time)}</span>
                <span className={cn("text-[9px] px-1 py-0.5 rounded shrink-0",
                  e.severity === "critical" ? "bg-loss/15 text-loss" : e.severity === "warning" ? "bg-warning/15 text-warning" : "bg-muted/30 text-muted-foreground")}>
                  {e.event ?? "?"}
                </span>
              </div>
              <div className="text-[10px] text-muted-foreground mt-0.5 leading-snug">{(e.detail ?? "").slice(0, 110)}</div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
