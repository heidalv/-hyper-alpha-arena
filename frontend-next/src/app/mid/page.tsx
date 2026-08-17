"use client";

import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Activity, Save, Loader2, BookOpen, SlidersHorizontal } from "lucide-react";
import { useStrategyConfig, useUpdateStrategyConfig } from "@/hooks/useTradingData";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { cn } from "@/lib/utils";
import { PromptEditorPanel } from "@/components/config/PromptEditorPanel";
import { PageHeader } from "@/components/layout/PageHeader";

type Tab = "params" | "prompts";

export default function MidPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center h-40">
          <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
        </div>
      }
    >
      <MidPageInner />
    </Suspense>
  );
}

function MidPageInner() {
  const searchParams = useSearchParams();
  const tier = "mid" as const;
  const { data, isLoading } = useStrategyConfig(tier);
  const updateMutation = useUpdateStrategyConfig(tier);

  const [config, setConfig] = useState<Record<string, any> | null>(null);
  const [dirty, setDirty] = useState(false);
  const [tab, setTab] = useState<Tab>("params");

  useEffect(() => {
    const t = (searchParams.get("tab") || "").toLowerCase();
    if (t === "prompts" || t === "prompt" || t === "提示词") {
      setTab("prompts");
    }
  }, [searchParams]);

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

  if (isLoading || !config || !data) {
    return (
      <div className="flex items-center justify-center h-40">
        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const stats = data.stats;
  const groups = data.groups;
  const params = data.param_defs;
  const hasStats = stats && stats.trades > 0;

  return (
    <div className="p-4 space-y-4">
      <PageHeader
        icon={<Activity className="w-4 h-4" />}
        title="中线策略配置"
        subtitle="参数修改后需点击保存生效"
        breadcrumb={[{ label: "策略配置" }, { label: "中线配置" }]}
        refreshHint="因子路由"
        actions={
          tab === "params" && (
            <Button size="sm" onClick={handleSave} disabled={!dirty || updateMutation.isPending} className="btn-glow">
              {updateMutation.isPending ? (
                <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
              ) : (
                <Save className="w-3.5 h-3.5 mr-1" />
              )}
              保存参数
            </Button>
          )
        }
      />

      {/* 参数 / 提示词 */}
      <div className="flex gap-1 border-b border-border pb-0">
        <button
          onClick={() => setTab("params")}
          className={cn(
            "flex items-center gap-1.5 px-3 py-2 text-xs border-b-2 -mb-px transition-colors",
            tab === "params"
              ? "border-primary text-primary font-medium"
              : "border-transparent text-muted-foreground hover:text-foreground"
          )}
        >
          <SlidersHorizontal className="w-3.5 h-3.5" />
          参数配置
        </button>
        <button
          onClick={() => setTab("prompts")}
          className={cn(
            "flex items-center gap-1.5 px-3 py-2 text-xs border-b-2 -mb-px transition-colors",
            tab === "prompts"
              ? "border-primary text-primary font-medium"
              : "border-transparent text-muted-foreground hover:text-foreground"
          )}
        >
          <BookOpen className="w-3.5 h-3.5" />
          提示词
        </button>
      </div>

      {tab === "prompts" ? (
        <PromptEditorPanel defaultTier="mid" />
      ) : (
        <div className={cn("grid grid-cols-1 gap-4 items-start", hasStats && "lg:grid-cols-[2fr_1fr]")}>
          {/* 左列：中线因子路由参数 */}
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
  );
}

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
