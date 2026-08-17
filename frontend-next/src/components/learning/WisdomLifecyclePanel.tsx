/**
 * Wisdom 生命周期面板（对齐 v6 9.2 IlcHermesTab 重建）
 *
 * 五步流水：提取 → 质量闸门 → 注入使用 → 生效验证 → 淘汰
 * 数据全部来自后端真实统计（/api/learning/wisdom/stats），
 * 指标为 0 即如实显示 0（使用率 0% 不粉饰）。
 */
"use client";

import { useEffect, useState } from "react";
import {
  getWisdomStats,
  getWisdomLoop,
  type WisdomStatsResponse,
  type WisdomLoopResponse,
} from "@/lib/intelligentLearningApi";
import { SectionCard, RefreshButton, StatCard } from "../operations/IlcUi";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { AlertTriangle, Database, PackageOpen } from "lucide-react";

const STEP_KEYS = ["extract", "gate", "inject", "validate", "retire"] as const;
const STEP_LABEL: Record<(typeof STEP_KEYS)[number], string> = {
  extract: "提取",
  gate: "质量闸门",
  inject: "注入使用",
  validate: "生效验证",
  retire: "淘汰",
};

export function WisdomLifecyclePanel() {
  const [stats, setStats] = useState<WisdomStatsResponse | null>(null);
  const [loop, setLoop] = useState<WisdomLoopResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = () => {
    setLoading(true);
    Promise.allSettled([getWisdomStats(), getWisdomLoop()])
      .then(([s, l]) => {
        if (s.status === "fulfilled") setStats(s.value);
        if (l.status === "fulfilled") setLoop(l.value);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30000);
    return () => clearInterval(t);
  }, []);

  const steps = stats?.steps ?? {};
  const rates = stats?.rates ?? {};
  const slot = stats?.slot_budget;
  const retrieval = stats?.retrieval ?? {};

  const stepValues: Record<(typeof STEP_KEYS)[number], number> = {
    extract: steps.extract?.total ?? 0,
    gate: steps.gate?.total ?? 0,
    inject: steps.inject?.total ?? 0,
    validate: steps.validate?.total ?? 0,
    retire: steps.retire?.total ?? 0,
  };
  const byOutcome = steps.extract?.by_outcome ?? {};
  const ranked = loop?.ranked ?? [];

  const usagePct = (rates.usage_rate ?? 0) * 100;
  const effectPct = (rates.effect_rate ?? 0) * 100;
  const retirePct = (rates.retire_rate ?? 0) * 100;

  return (
    <div className="space-y-4">
      <SectionCard
        title="Wisdom 生命周期 · 五步流水"
        description="从平仓沉淀到注入决策的真实计数；任何环节为 0 都如实展示，不做假数据"
        action={<RefreshButton onClick={refresh} loading={loading} />}
      >
        {stats?.error && <p className="text-sm text-red-500 mb-3">{stats.error}</p>}

        {/* 五步流水条 */}
        <div className="grid grid-cols-1 sm:grid-cols-5 gap-2 mb-5">
          {STEP_KEYS.map((key, idx) => {
            const v = stepValues[key];
            const isZero = v === 0;
            return (
              <div key={key} className="relative">
                {idx < STEP_KEYS.length - 1 && (
                  <div className="hidden sm:block absolute top-7 left-1/2 w-full h-px bg-border/40" />
                )}
                <div
                  className={cn(
                    "relative z-10 rounded-lg border p-3 text-center transition-shadow",
                    isZero
                      ? "border-border/60 bg-muted/30"
                      : key === "retire"
                        ? "border-loss/30 bg-loss/5"
                        : "border-cyan-400/30 bg-primary/5 shadow-[0_0_12px_rgba(34,211,238,0.25)]"
                  )}
                >
                  <div className="text-[10px] text-muted-foreground mb-1">
                    {idx + 1} · {STEP_LABEL[key]}
                  </div>
                  <div
                    className={cn(
                      "text-2xl font-bold tabular-nums",
                      isZero
                        ? "text-muted-foreground/70"
                        : key === "retire"
                          ? "grad-text-red"
                          : "grad-text"
                    )}
                  >
                    {v.toLocaleString()}
                  </div>
                  {key === "extract" && byOutcome.win != null && (
                    <div className="text-[10px] text-muted-foreground mt-1">
                      win {byOutcome.win ?? 0} / loss {byOutcome.loss ?? 0}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        {/* 三率卡 */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-5">
          <StatCard
            label="使用率（注入/提取）"
            value={`${usagePct.toFixed(1)}%`}
            hint="验收目标 ≥80%，当前如实展示"
            tone={usagePct >= 80 ? "good" : usagePct > 0 ? "warn" : "bad"}
          />
          <StatCard
            label="生效率（质量命中/验证）"
            value={`${effectPct.toFixed(1)}%`}
            hint={stepValues.validate > 0 ? "质量闸门通过样本的盈利占比" : "无验证样本（validate=0）"}
            tone={effectPct > 0 ? "good" : "bad"}
          />
          <StatCard
            label="淘汰率（淘汰/总数）"
            value={`${retirePct.toFixed(1)}%`}
            hint="持续无效 wisdom 自动停用比例"
            tone="default"
          />
        </div>

        {/* 注入前后对照区 */}
        <ValidationChartArea
          validated={stepValues.validate}
          qualityHit={steps.validate?.quality_hit_count ?? 0}
        />

        {/* slot 预算 + 检索注入子卡 */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="glass rounded-lg p-3">
            <div className="text-xs font-medium mb-2 flex items-center gap-1.5">
              <PackageOpen className="w-3.5 h-3.5 text-muted-foreground" />
              注入 Slot 预算
            </div>
            <div className="flex items-center gap-2">
              {slot?.enabled ? (
                <Badge className="font-normal border-profit/40 bg-profit/10 text-profit">
                  <span className="w-1.5 h-1.5 rounded-full bg-profit shadow-[0_0_6px_currentColor]" />
                  已启用
                </Badge>
              ) : (
                <Badge className="font-normal border-amber-500/40 bg-amber-500/10 text-warning">
                  <span className="w-1.5 h-1.5 rounded-full bg-warning shadow-[0_0_6px_currentColor]" />
                  未启用
                </Badge>
              )}
              <span className="text-xs text-muted-foreground">
                {slot?.enabled
                  ? `上限 ${slot.max_slots ?? 0} / 已用 ${slot.used ?? 0}`
                  : (slot?.note ?? "v6 8.2 阶段2 内容，当前不启用")}
              </span>
            </div>
          </div>

          <div
            className={cn(
              "rounded-lg border p-3",
              retrieval.ready ? "border-profit/30 bg-profit/5" : "border-amber-500/40 bg-amber-50/50 dark:bg-amber-950/20"
            )}
          >
            <div className="text-xs font-medium mb-2 flex items-center gap-1.5">
              <Database className="w-3.5 h-3.5 text-muted-foreground" />
              检索注入（RAG 语义检索载体）
            </div>
            {retrieval.ready ? (
              <div className="flex items-center gap-2">
                <Badge className="font-normal border-profit/40 bg-profit/10 text-profit">
                  <span className="w-1.5 h-1.5 rounded-full bg-profit shadow-[0_0_6px_currentColor]" />
                  语义检索可用
                </Badge>
                <span className="text-xs text-muted-foreground">
                  trading_wisdom 索引 {retrieval.trading_wisdom_docs ?? 0} 条 ·{" "}
                  {retrieval.embedding_model ?? "—"}
                </span>
              </div>
            ) : (
              <div className="flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
                <div className="text-xs">
                  <div className="font-medium text-amber-700 dark:text-amber-300">
                    RAG 未激活（not_initialized）
                  </div>
                  <div className="text-muted-foreground mt-0.5">
                    全库 {retrieval.total_documents?.toLocaleString() ?? 0} 条文档在库但嵌入模型未加载；
                    注入仍走最近 N 条退化逻辑，语义检索升级路径待激活（后端 10.2.4）
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </SectionCard>

      {/* 质量闸门通过样本（proposal wisdom，与 9.2 看板对齐） */}
      <SectionCard
        title="闸门通过样本（proposal wisdom）"
        description={`净扣费 tanh(|pnl|/50) 加权 + 质量闸门沉淀；已入库 ${steps.gate?.total ?? 0} 条`}
        action={null}
      >
        {ranked.length === 0 ? (
          <p className="text-sm text-warning py-6 text-center flex items-center justify-center gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
            暂无智慧样本（回测产出后自动注入）
          </p>
        ) : (
          <div className="space-y-2">
            {ranked.map((w: any, idx: number) => (
              <div
                key={w.id}
                className="flex items-center justify-between rounded-md border px-3 py-2 text-sm"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-muted-foreground w-6 text-right tabular-nums">{idx + 1}</span>
                  <Badge variant="secondary" className="font-normal shrink-0">
                    {w.type ?? "unknown"}
                  </Badge>
                  <span className="text-muted-foreground shrink-0">{w.tier ?? "-"}</span>
                  <span className="truncate font-mono text-xs">{w.template_id}</span>
                </div>
                <div className="flex items-center gap-3 shrink-0 tabular-nums">
                  <span className="text-xs text-muted-foreground">
                    命中 {w.quality_hit_count ?? 0}/{w.evaluation_count ?? 0} · 应用 {w.applied_count ?? 0}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </SectionCard>
    </div>
  );
}

/** 注入前后对照区：无验证样本时显示"样本不足"空态 */
function ValidationChartArea({ validated, qualityHit }: { validated: number; qualityHit: number }) {
  if (validated === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border/60 p-4 mb-4 text-center">
        <p className="text-sm text-warning flex items-center justify-center gap-1.5">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          注入前后命中率 / PF 对照：样本不足（已评估样本 0）——注入环节未启动，无对照数据
        </p>
      </div>
    );
  }
  const hitRate = (qualityHit / validated) * 100;
  return (
    <div className="grid grid-cols-2 gap-3 mb-4">
      <StatCard label="注入后命中率" value={`${hitRate.toFixed(1)}%`} hint={`${qualityHit}/${validated} 个质量样本盈利`} tone="good" />
      <StatCard label="注入前基线" value="n/a" hint="对照实验未启用" tone="default" />
    </div>
  );
}

export default WisdomLifecyclePanel;
