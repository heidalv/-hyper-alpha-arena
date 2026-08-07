"use client";

import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Activity, Save, Loader2, BookOpen, SlidersHorizontal } from "lucide-react";
import { useStrategyConfig, useUpdateStrategyConfig } from "@/hooks/useTradingData";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { cn } from "@/lib/utils";
import { PromptEditorPanel } from "@/components/config/PromptEditorPanel";

type Tab = "params" | "prompts";

export default function LongPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center h-40">
          <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
        </div>
      }
    >
      <LongPageInner />
    </Suspense>
  );
}

function LongPageInner() {
  const searchParams = useSearchParams();
  const tier = "long" as const;
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
  const stagedTpEnabled = config.staged_tp_enabled;

  return (
    <div className="p-4 space-y-4 max-w-5xl mx-auto">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h1 className="text-lg font-bold flex items-center gap-2">
          <Activity className="w-5 h-5 text-primary" />
          长线策略配置
        </h1>
        {tab === "params" && (
          <Button size="sm" onClick={handleSave} disabled={!dirty || updateMutation.isPending}>
            {updateMutation.isPending ? (
              <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
            ) : (
              <Save className="w-3.5 h-3.5 mr-1" />
            )}
            保存参数
          </Button>
        )}
      </div>

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
        <PromptEditorPanel defaultTier="long" />
      ) : (
        <>
          {stats && stats.trades > 0 && (
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
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
          )}

          {stagedTpEnabled && (
            <Card className="border-primary/20 p-4">
              <div className="text-sm font-medium mb-2">分批止盈路线图 (D14)</div>
              <div className="flex items-center gap-2 text-xs flex-wrap">
                <span className="px-2 py-1 bg-muted/50 rounded">持仓</span>
                <span>→</span>
                <span className="px-2 py-1 bg-profit/10 text-profit rounded">
                  +{((config.tp1_trigger as number) * 100).toFixed(0)}% 减
                  {((config.tp1_reduce as number) * 100).toFixed(0)}%
                </span>
                <span>→</span>
                <span className="px-2 py-1 bg-profit/10 text-profit rounded">
                  +{((config.tp2_trigger as number) * 100).toFixed(0)}% 减
                  {((config.tp2_reduce as number) * 100).toFixed(0)}%
                </span>
                <span>→</span>
                <span className="px-2 py-1 bg-profit/10 text-profit rounded">
                  +{((config.tp3_trigger as number) * 100).toFixed(0)}% 减
                  {((config.tp3_reduce as number) * 100).toFixed(0)}%
                </span>
                <span>→</span>
                <span className="px-2 py-1 bg-primary/10 text-primary rounded">
                  ATR trailing {config.trailing_atr_mult}x
                </span>
              </div>
            </Card>
          )}

          {Object.entries(groups)
            .sort(([, a]: any, [, b]: any) => a.order - b.order)
            .map(([gKey, gDef]: [string, any]) => {
              const keys = Object.entries(params)
                .filter(([, d]: any) => d.group === gKey)
                .map(([k]) => k);
              if (!keys.length) return null;
              return (
                <Card key={gKey} className="p-4">
                  <h2 className="text-sm font-medium mb-3">{gDef.title}</h2>
                  <div className="space-y-3">
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
        </>
      )}
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
    <div
      className={cn(
        "p-2 rounded bg-muted/30",
        positive !== undefined && (positive ? "text-profit" : "text-loss")
      )}
    >
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-sm font-bold tabular-nums">{value}</div>
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
        <span className="text-sm">{def.label}</span>
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
      <div className="flex justify-between text-sm mb-1">
        <span>{def.label}</span>
        <span className="font-bold tabular-nums">
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
