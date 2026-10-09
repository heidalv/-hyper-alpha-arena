"use client";

import { useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Activity, BookOpen, ChevronDown, ChevronUp, Loader2, Save, SlidersHorizontal,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { PromptEditorPanel } from "@/components/config/PromptEditorPanel";
import { LongReportsPanel } from "@/components/long/LongReportsPanel";
import { useStrategyConfig, useUpdateStrategyConfig } from "@/hooks/useTradingData";

/**
 * 车道参数配置卡（中线 / 长线）。
 *
 * [2026-10-01 合并] 用户指令：「中线配置 长线配置 并入 ai策略 作为页内的卡片」。
 * 原 `/mid`、`/long` 两个独立页（结构近乎相同，只差 tier 与长线多一个「报告观测」页签）
 * 收敛成本组件，由「AI 策略」页（`src/app/strategy/page.tsx`）以**页内卡片**渲染：
 *   - 卡片可折叠（默认展开）；展开后页内子页签：参数配置 / 提示词（长线另有 报告观测）；
 *   - 保存按钮回到各卡自己的卡头（只保存该 tier）；
 *   - 旧路由 `/mid`、`/long` 保留为跳转桩 → `/strategy?cfg=mid|long[&sub=...]`；
 *   - 后端 `/api/strategy-config/*` 与 `useStrategyConfig/useUpdateStrategyConfig` 均未改。
 */

export type Tier = "mid" | "long";
export type TierSubTab = "params" | "prompts" | "reports";

export function TierConfigCard({
  tier,
  title,
  subtitle,
  hint,
  id,
  open,
  onToggle,
  initialSub = "params",
  withReports = false,
}: {
  tier: Tier;
  title: string;
  subtitle: string;
  /** 卡头右侧的口径提示（沿用原页 PageHeader 的 refreshHint 文案） */
  hint?: string;
  /** 锚点 id：页内「配置」按钮/旧路由深链据此滚动定位 */
  id: string;
  open: boolean;
  onToggle: () => void;
  initialSub?: TierSubTab;
  /** 长线独有「报告观测」页签 */
  withReports?: boolean;
}) {
  const { data, isLoading, isError, error, refetch } = useStrategyConfig(tier);
  const updateMutation = useUpdateStrategyConfig(tier);

  const [config, setConfig] = useState<Record<string, any> | null>(null);
  const [dirty, setDirty] = useState(false);
  const [tab, setTab] = useState<TierSubTab>(initialSub);

  // 沿用原页写法（data 到达后回填一次）
  if (data && !config) setConfig(data.config);

  const updateParam = (key: string, value: number | boolean) => {
    setConfig((prev) => (prev ? { ...prev, [key]: value } : prev));
    setDirty(true);
  };

  const handleSave = async () => {
    if (!config) return;
    await updateMutation.mutateAsync(config);
    setDirty(false);
  };

  const stats = data?.stats;
  const groups = data?.groups ?? {};
  const params = data?.param_defs ?? {};
  const hasStats = !!stats && stats.trades > 0;
  const subTabs: { key: TierSubTab; label: string; icon: typeof SlidersHorizontal }[] = [
    { key: "params", label: "参数配置", icon: SlidersHorizontal },
    { key: "prompts", label: "提示词", icon: BookOpen },
    ...(withReports ? [{ key: "reports" as TierSubTab, label: "报告观测", icon: Activity }] : []),
  ];

  // 折叠态摘要：不看配置也能看到「几组参数 / 近7天战绩」
  const collapsedSummary = [
    `${Object.keys(groups).length} 组参数`,
    hasStats ? `近7天 ${stats.trades} 笔 · 胜率 ${(stats.win_rate * 100).toFixed(0)}% · 净PnL ${stats.net_pnl > 0 ? "+" : ""}${stats.net_pnl.toFixed(1)}` : "近7天无成交",
    dirty ? "● 有未保存修改" : null,
  ].filter(Boolean).join(" · ");

  return (
    <Card id={id} className="glass overflow-hidden scroll-mt-4">
      {/* 卡头：标题 + 保存 + 折叠 */}
      <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-border/50">
        <div className="flex items-center gap-2 min-w-0">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0 bg-primary/10">
            <SlidersHorizontal className="w-4 h-4 text-primary" />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-medium truncate flex items-center gap-2">
              {title}
              {dirty && <span className="text-[10px] text-warning">未保存</span>}
            </div>
            <div className="text-xs text-muted-foreground truncate">{subtitle}</div>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {hint && <span className="text-xs font-mono text-slate-500 hidden md:inline">{hint}</span>}
          {open && tab === "params" && (
            <Button size="sm" onClick={handleSave} disabled={!dirty || updateMutation.isPending} className="btn-glow">
              {updateMutation.isPending ? (
                <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
              ) : (
                <Save className="w-3.5 h-3.5 mr-1" />
              )}
              保存参数
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={onToggle} aria-expanded={open}>
            {open ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            {open ? "收起" : "展开配置"}
          </Button>
        </div>
      </div>

      {!open && (
        <div className="px-4 py-2.5 text-xs text-muted-foreground">{collapsedSummary}</div>
      )}

      {open && (
        <>
          {/* 页内子页签 */}
          <div className="flex gap-1 border-b border-border px-4">
            {subTabs.map((t) => {
              const Icon = t.icon;
              return (
                <button
                  key={t.key}
                  onClick={() => setTab(t.key)}
                  className={cn(
                    "flex items-center gap-1.5 px-3 py-2 text-xs border-b-2 -mb-px transition-colors",
                    tab === t.key
                      ? "border-primary text-primary font-medium"
                      : "border-transparent text-muted-foreground hover:text-foreground"
                  )}
                >
                  <Icon className="w-3.5 h-3.5" />
                  {t.label}
                </button>
              );
            })}
          </div>

          <div className="px-4 py-3">
            {/* 接口失败时给重试（原页行为：绝不停在转圈） */}
            {isError && !data ? (
              <div className="flex flex-col items-center justify-center gap-2 py-8 text-sm">
                <span className="text-loss">{title}加载失败：{(error as Error)?.message || "未知错误"}</span>
                <button
                  type="button"
                  onClick={() => void refetch()}
                  className="text-xs text-cyan-300 underline underline-offset-2 hover:opacity-80"
                >
                  重试
                </button>
              </div>
            ) : isLoading || !config || !data ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
              </div>
            ) : tab === "reports" ? (
              <LongReportsPanel />
            ) : tab === "prompts" ? (
              <PromptEditorPanel defaultTier={tier} />
            ) : (
              <div className={cn("grid grid-cols-1 gap-4 items-start", hasStats && "lg:grid-cols-[2fr_1fr]")}>
                {/* 左列：因子路由 / 规则化参数（按后端 groups 顺序） */}
                <div className="space-y-4 min-w-0">
                  {Object.entries(groups)
                    .sort(([, a]: any, [, b]: any) => a.order - b.order)
                    .map(([gKey, gDef]: [string, any]) => {
                      const keys = Object.entries(params)
                        .filter(([, d]: any) => d.group === gKey)
                        .map(([k]) => k);
                      if (!keys.length) return null;
                      return (
                        <Card key={gKey} className="glass p-0 [--card-spacing:0px]">
                          <CardHead
                            icon={<SlidersHorizontal className="w-[15px] h-[15px] text-cyan-300" />}
                            title={gDef.title}
                            hint="保存后生效"
                          />
                          <div className="px-4 py-3 space-y-4">
                            {keys.map((key) => (
                              <ParamRow
                                key={key}
                                k={key}
                                def={params[key]}
                                val={config[key]}
                                onChange={updateParam}
                              />
                            ))}
                          </div>
                        </Card>
                      );
                    })}
                </div>

                {/* 右列：战绩统计 */}
                {hasStats && (
                  <div className="space-y-4 min-w-0">
                    <Card className="glass p-0 [--card-spacing:0px]">
                      <CardHead
                        icon={<Activity className="w-[15px] h-[15px] text-cyan-300" />}
                        title="战绩统计"
                        hint="近7天"
                      />
                      <div className="px-4 py-3 grid grid-cols-2 gap-2">
                        <Stat label="近7天" value={`${stats.trades}笔`} />
                        <Stat
                          label="胜率"
                          value={`${(stats.win_rate * 100).toFixed(0)}%`}
                          positive={stats.win_rate >= 0.5}
                        />
                        <Stat
                          label="净PnL"
                          value={`${stats.net_pnl > 0 ? "+" : ""}${stats.net_pnl.toFixed(1)}`}
                          positive={stats.net_pnl > 0}
                        />
                        <Stat
                          label="盈亏比"
                          value={stats.profit_factor.toFixed(2)}
                          positive={stats.profit_factor >= 1}
                        />
                        <Stat label="持仓" value={`${stats.avg_hold_hours}h`} />
                      </div>
                    </Card>
                  </div>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </Card>
  );
}

// ═══ 以下三个小组件原为 /mid 与 /long 各自重复的一份，现收敛到这里 ═══

function CardHead({
  icon,
  title,
  hint,
  right,
}: {
  icon?: React.ReactNode;
  title: string;
  hint?: string;
  right?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3 px-4 pt-3 pb-2.5 border-b border-white/5">
      <div className="flex items-center gap-2 text-sm font-semibold min-w-0">
        {icon}
        <span className="truncate">{title}</span>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {hint && <span className="text-xs font-mono text-slate-500">{hint}</span>}
        {right}
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  positive,
}: {
  label: string;
  value: string;
  positive?: boolean;
}) {
  return (
    <div className="bg-muted/40 rounded-lg p-2">
      <div className="text-xs text-muted-foreground mb-0.5">{label}</div>
      <div className={cn("text-sm font-bold tabular-nums", positive === undefined ? "grad-text" : positive ? "text-profit" : "text-loss")}>{value}</div>
    </div>
  );
}

function ParamRow({
  k,
  def,
  val,
  onChange,
}: {
  k: string;
  def: any;
  val: any;
  onChange: (k: string, v: any) => void;
}) {
  if (def.type === "bool") {
    return (
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted-foreground">{def.label}</span>
        <button
          onClick={() => onChange(k, !val)}
          className={cn(
            "relative w-11 h-6 rounded-full transition-colors",
            val ? "bg-primary" : "bg-muted"
          )}
        >
          <span
            className={cn(
              "absolute top-0.5 w-5 h-5 bg-white rounded-full transition-transform",
              val ? "left-5" : "left-0.5"
            )}
          />
        </button>
      </div>
    );
  }
  return (
    <div>
      <div className="flex justify-between text-xs mb-1.5">
        <span className="text-muted-foreground">{def.label}</span>
        <span className="text-sm font-bold tabular-nums">
          {def.unit === "%" ? (val * 100).toFixed(1) + "%" : val}
        </span>
      </div>
      <input
        type="range"
        min={def.min}
        max={def.max}
        step={(def.max - def.min) / 100}
        value={val}
        onChange={(e) => onChange(k, parseFloat(e.target.value))}
        className="w-full"
      />
    </div>
  );
}
