"use client";

/**
 * 车道配置面板（[2026-10-03 用户需求] 「ai 策略里只保留会话管理…原来的 ai 策略就没有了」）。
 *
 * 原「中线配置」「长线配置」两页在 [2026-10-01] 并成 `TierConfigCard` 挂在 AI 策略页的
 * 双周期总览里。AI 策略页现在被移除，**这两张配置卡不能跟着消失**（它们是参数口径的唯一入口，
 * 且 `/mid`、`/long` 跳转桩与命令面板「提示词」都指向它们）⇒ 原样迁到本面板，挂在
 * `/control?tab=lanes`，深链参数 `cfg=mid|long`、`sub=params|prompts|reports` 语义保持不变。
 */
import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { TierConfigCard } from "@/components/config/TierConfigCard";
import { SlidersHorizontal } from "lucide-react";

export function LaneConfigPanel() {
  const searchParams = useSearchParams();
  // 深链：旧 /mid、/long 跳转桩落到 `?cfg=`；`&sub=` 指定卡片内的子页签
  const cfgParam = (searchParams.get("cfg") || "").toLowerCase();
  const cfgTier: "mid" | "long" | null = cfgParam === "mid" || cfgParam === "long" ? cfgParam : null;
  const subParam = (searchParams.get("sub") || "").toLowerCase();
  const cfgSub: "params" | "prompts" | "reports" | null =
    subParam === "params" || subParam === "prompts" || subParam === "reports" ? subParam : null;

  // 页内配置卡展开态（默认都展开）
  const [cfgOpen, setCfgOpen] = useState<{ mid: boolean; long: boolean }>({ mid: true, long: true });

  // 深链进入时滚到对应配置卡（不做 setState，避免 set-state-in-effect）
  useEffect(() => {
    if (!cfgTier) return;
    const t = window.setTimeout(
      () => document.getElementById(`tier-config-${cfgTier}`)?.scrollIntoView({ behavior: "smooth", block: "start" }),
      400,
    );
    return () => window.clearTimeout(t);
  }, [cfgTier]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <SlidersHorizontal className="w-4 h-4 text-primary" />
        <h2 className="text-sm font-medium">车道配置</h2>
        <Badge variant="secondary" className="text-xs">原「中线配置」「长线配置」页已并入此处</Badge>
        <span className="text-xs text-muted-foreground ml-auto">参数修改后在各卡卡头点「保存参数」生效</span>
      </div>
      <TierConfigCard
        id="tier-config-mid"
        tier="mid"
        title="中线配置 · 日内波段"
        subtitle="因子路由参数 · 持仓 12h-48h · 论题 4h TTL"
        hint="因子路由"
        open={cfgOpen.mid}
        onToggle={() => setCfgOpen((o) => ({ ...o, mid: !o.mid }))}
        initialSub={cfgTier === "mid" && cfgSub ? cfgSub : "params"}
      />
      <TierConfigCard
        id="tier-config-long"
        tier="long"
        title="长线配置 · 趋势"
        subtitle="long_trend_v2 规则化参数 · 持仓 3-7 天 · Chandelier 追踪"
        hint="路线图 D14"
        open={cfgOpen.long}
        onToggle={() => setCfgOpen((o) => ({ ...o, long: !o.long }))}
        initialSub={cfgTier === "long" && cfgSub ? cfgSub : "params"}
        withReports
      />
    </div>
  );
}
