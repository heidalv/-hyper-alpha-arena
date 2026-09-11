"use client";

/**
 * AsterdexPointsPanel — 实盘成交积分账本（成交顺路吃 Rh 积分计量）
 *
 * 数据源：GET /api/rebate/asterdex-points/summary
 * 设计：docs/ASTERDEX_LIVE_POINTS_DESIGN.md
 * 注意：官方未公开精确权重，所有积分/估值均为估算（投机性），
 *       以官方 totalRhPoints 快照为准。
 */

import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RefreshCw, Coins, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { getBackendUrl } from "@/lib/backend-config";

interface SummaryData {
  events?: number;
  notional_usd?: number;
  trade_points?: number;
  hold_points?: number;
  fee_usd?: number;
  est_usd?: number;
  maker_ratio?: number | null;
  maker_fills?: number;
  error?: string;
}

interface ReconcileData {
  available?: boolean;
  official_points?: number;
  multiplier?: number;
  estimated_points_30d?: number;
  season?: string;
  airdrop_eligible?: boolean;
  note?: string;
  reason?: string;
}

interface Response {
  days?: number;
  summary?: SummaryData;
  reconcile?: ReconcileData;
  speculative?: boolean;
  note?: string;
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-border/60 p-3">
      <div className="text-[11px] text-muted-foreground">{label}</div>
      <div className="text-base font-semibold tabular-nums mt-0.5">{value}</div>
      {hint && <div className="text-[10px] text-muted-foreground mt-0.5">{hint}</div>}
    </div>
  );
}

export default function AsterdexPointsPanel() {
  const [data, setData] = useState<Response | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(
        `${getBackendUrl()}/api/rebate/asterdex-points/summary?days=7&reconcile=true`,
        { cache: "no-store" }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = (await res.json()) as Response;
      setData(json);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);

  const s = data?.summary ?? {};
  const r = data?.reconcile;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <Coins className="w-4 h-4 text-amber-300" />
          <span className="text-sm font-medium">Asterdex 实盘成交计量（近 7 天）</span>
          <Badge variant="outline" className="text-[10px] text-muted-foreground">
            <Sparkles className="w-3 h-3 mr-0.5" /> Stage 6 积分已结束 · 不再计值
          </Badge>
        </div>
        <Button variant="ghost" size="sm" onClick={load}>
          <RefreshCw className={cn("w-3.5 h-3.5", loading && "animate-spin")} />
        </Button>
      </div>

      {error && !data && (
        <div className="text-center py-6 text-loss text-sm">加载失败：{error}</div>
      )}

      {/* [2026-09-03] 只保留本地计量里"真实"的部分：成交事件 / 名义 / 手续费 / maker 占比。
          交易积分 / 持仓积分 / 积分估值 已随 Stage 6 结束归零，不再展示以免误导。 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <Stat label="实盘成交事件" value={`${s.events ?? 0}`} hint="open + close" />
        <Stat label="成交名义额" value={`$${((s.notional_usd ?? 0) / 1).toLocaleString(undefined, { maximumFractionDigits: 0 })}`} />
        <Stat label="手续费（按费率表估）" value={`$${(s.fee_usd ?? 0).toFixed(4)}`} hint="maker 0 / taker 0.04%" />
        <Stat
          label="Maker 成交占比"
          value={s.maker_ratio != null ? `${((s.maker_ratio ?? 0) * 100).toFixed(0)}%` : "—"}
          hint={`maker 成交 ${s.maker_fills ?? 0} 笔`}
        />
      </div>

      <div className="rounded-lg border border-border/60 p-3">
        <div className="text-[11px] text-muted-foreground">官方积分对账</div>
        <div className="text-xs text-muted-foreground mt-1">
          {r?.available
            ? `${(r.official_points ?? 0).toLocaleString()} 分（${r.season || "历史赛季"}）`
            : `不可用：${r?.reason || "Stage 6 已结束，官方 API 不再提供积分数据"}`}
        </div>
      </div>

      <div className="text-[11px] text-muted-foreground leading-relaxed">
        真实收入（手续费/资金费/已实现盈亏/USDF 奖励入账、Trade &amp; Earn 门槛进度）请看「实盘交易」页的真实收入账本。
        Maker 优先只对中长线开仓生效，绝不为刷量而交易。{data?.note ?? ""}
      </div>
    </div>
  );
}
