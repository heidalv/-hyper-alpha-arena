/**
 * 三通道健康看板（对齐 v6 9.2 IlcOperationsTab 重建）
 *
 * 证据通道（wisdom 提取/注入时间）+ 参数通道（runtime_tuning.json 更新时间）
 * + 因子通道（factor_evolution_log 最新时间）+ 学习闭环 5 job 状态表。
 * 语义统一复用后端 learning_health_service 的 ok/warn/dead 三段判定，
 * 断链/停摆一律标红，不做假健康。
 */
"use client";

import { useEffect, useState } from "react";
import {
  getLearningHealth,
  getLoopStatus,
  getWisdomStats,
  type LearningHealthResponse,
  type LearningHealthItem,
  type LoopStatusResponse,
  type WisdomStatsResponse,
} from "@/lib/intelligentLearningApi";
import { getEvolutionStatus, type EvolutionStatus } from "@/lib/api/compute";
import { SectionCard, RefreshButton, StatCard } from "../operations/IlcUi";
import { cn } from "@/lib/utils";
import { Activity, Database, Gauge, TrendingUp, Clock, AlertTriangle } from "lucide-react";
import { Badge } from "@/components/ui/badge";

const LOOP_ORDER = [
  "loop_heartbeat",
  "loop_outcome_batch",
  "loop_paper_backfill",
  "loop_kelly",
  "loop_coordinator",
] as const;

const STATUS_TONE: Record<string, "good" | "warn" | "bad"> = {
  ok: "good",
  warn: "warn",
  dead: "bad",
};

export function HealthChannelsPanel() {
  const [health, setHealth] = useState<LearningHealthResponse | null>(null);
  const [loop, setLoop] = useState<LoopStatusResponse | null>(null);
  const [wisdom, setWisdom] = useState<WisdomStatsResponse | null>(null);
  const [evo, setEvo] = useState<EvolutionStatus | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = () => {
    setLoading(true);
    Promise.allSettled([
      getLearningHealth(),
      getLoopStatus(),
      getWisdomStats(),
      getEvolutionStatus(),
    ])
      .then(([h, l, w, e]) => {
        if (h.status === "fulfilled") setHealth(h.value);
        if (l.status === "fulfilled") setLoop(l.value);
        if (w.status === "fulfilled") setWisdom(w.value);
        if (e.status === "fulfilled") setEvo(e.value);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30000);
    return () => clearInterval(t);
  }, []);

  const items = health?.items ?? [];
  const byName = Object.fromEntries(items.map((i) => [i.name, i]));
  const runtimeGates = byName.runtime_gates;
  const evolutionItem = byName.evolution;
  const strategyMemory = byName.strategy_memory;
  const loopItems = LOOP_ORDER.map((name) => byName[name]).filter(Boolean) as LearningHealthItem[];

  const extract = wisdom?.steps?.extract;
  const inject = wisdom?.steps?.inject;

  return (
    <div className="space-y-4">
      <SectionCard
        title="三通道健康看板"
        description="ok ≤ 阈值 / warn ≤ 2× 阈值 / dead 超阈值——数据源 /api/learning/health + /api/learning/wisdom/stats + /api/compute/evolution/status"
        action={<RefreshButton onClick={refresh} loading={loading} />}
      >
        {health?.error && <p className="text-sm text-red-500 mb-3">{health.error}</p>}

        {/* 三通道卡 */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
          <ChannelCard
            icon={<Database className="w-4 h-4" />}
            title="证据通道 · Wisdom"
            desc="平仓 → 提取 → 注入使用的真实时间戳"
          >
            <div className="space-y-1.5 text-xs">
              <ChannelRow label="提取" value={fmtCount(extract?.total)} time={extract?.latest} tone={ageTone(extract?.latest, 48)} />
              <ChannelRow label="注入使用" value={fmtCount(inject?.total)} time={inject?.cumulative_count ? undefined : null} tone={inject?.total ? "ok" : "dead"} />
              <p className="text-[10px] text-muted-foreground pt-1">注入 = 决策实际应用次数；当前 0 则如实展示（使用率 0%）</p>
            </div>
          </ChannelCard>

          <ChannelCard
            icon={<Gauge className="w-4 h-4" />}
            title="参数通道 · RuntimeGovernor"
            desc="runtime_tuning.json 统一下发，决策核心 60s 内生效"
          >
            <div className="space-y-1.5 text-xs">
              <ChannelRow label="门槛下发" value={runtimeGates?.status ?? "—"} time={runtimeGates?.last_activity} tone={STATUS_TONE[runtimeGates?.status ?? "dead"]} />
              <ChannelRow label="参数进化(NSGA-II)" value={evolutionItem?.status ?? "—"} time={evolutionItem?.last_activity} tone={STATUS_TONE[evolutionItem?.status ?? "dead"]} />
              <p className="text-[10px] text-muted-foreground pt-1">{evolutionItem?.detail}</p>
            </div>
          </ChannelCard>

          <ChannelCard
            icon={<TrendingUp className="w-4 h-4" />}
            title="因子通道 · FactorEvo"
            desc="factor_evolution_log 最新产出（每日 03:00 挖掘 + 每小时在线权重）"
          >
            <div className="space-y-1.5 text-xs">
              <ChannelRow
                label="最近产出"
                value={evo?.running ? "运行中" : ageLabel(evo?.last_activity_at ?? null, 24)}
                time={evo?.last_activity_at}
                tone={evo?.running ? "ok" : ageTone(evo?.last_activity_at ?? null, 24)}
              />
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">活跃因子</span>
                <span className="tabular-nums font-medium">{evo?.active_factors?.total ?? 0}</span>
              </div>
              {evo?.last_error && (
                <p className="text-[10px] text-loss">上次错误：{evo.last_error}</p>
              )}
            </div>
          </ChannelCard>
        </div>

        {/* 断链警示：策略记忆/教训反哺 */}
        {strategyMemory && (strategyMemory.status === "dead" || (strategyMemory.detail ?? "").includes("0 条")) && (
          <div className="rounded-lg border border-loss/30 bg-loss/5 p-3 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 text-loss shrink-0 mt-0.5" />
            <div className="text-xs">
              <div className="font-medium text-loss">教训反哺断链（{strategyMemory.label}）</div>
              <div className="text-muted-foreground mt-0.5">
                {strategyMemory.detail ?? "strategy_memories=0，平仓复盘 → LLM 上下文链路未产出"}
              </div>
            </div>
          </div>
        )}
      </SectionCard>

      {/* 学习闭环 5 job 状态表 */}
      <SectionCard
        title="学习闭环 5 Job 状态"
        description="LearningLoop 真实 tick 记录（/api/learning/loop/status + /api/learning/health），超时标红"
        action={null}
      >
        {loopItems.length === 0 ? (
          <p className="text-sm text-warning py-4 text-center flex items-center justify-center gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
            暂无闭环状态
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr className="border-b border-border/50 text-left text-muted-foreground">
                  <th className="py-2 pr-3 font-medium">闭环</th>
                  <th className="py-2 pr-3 font-medium">状态</th>
                  <th className="py-2 pr-3 font-medium">上次 tick</th>
                  <th className="py-2 pr-3 font-medium">下次 tick</th>
                  <th className="py-2 pr-3 font-medium">间隔</th>
                  <th className="py-2 font-medium">说明</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/20">
                {loopItems.map((it) => {
                  const next = loop?.next_tick_at?.[it.name];
                  const intervalSec = loop?.intervals?.[it.name];
                  return (
                    <tr key={it.name}>
                      <td className="py-2 pr-3 font-medium whitespace-nowrap">{it.label}</td>
                      <td className="py-2 pr-3">
                        <StatusChip status={it.status ?? "dead"} />
                      </td>
                      <td className="num">
                        {it.last_activity ? fmtTime(it.last_activity) : "—"}
                        {it.age_hours != null && (
                          <span className={cn("ml-1.5 text-[10px]", ageClass(it.status))}>{it.age_hours}h</span>
                        )}
                      </td>
                      <td className="num">
                        {next ? fmtTime(next) : "—"}
                      </td>
                      <td className="num">
                        {intervalSec ? `${fmtInterval(intervalSec)}` : it.threshold_hours != null ? `${it.threshold_hours}h` : "—"}
                      </td>
                      <td className="text-muted-foreground" style={{ whiteSpace: "normal" }}>{it.detail}</td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr className="border-t border-border/40">
                  <td colSpan={2} className="num px-3 py-2 text-xs text-muted-foreground">
                    共 {loopItems.length} 个闭环
                  </td>
                  <td colSpan={4} className="num px-3 py-2 text-xs">
                    <span className="text-profit">{loopItems.filter((i) => i.status === "ok").length} 正常</span>
                    <span className="text-muted-foreground"> · </span>
                    <span className="text-warning">{loopItems.filter((i) => i.status === "warn").length} 迟滞</span>
                    <span className="text-muted-foreground"> · </span>
                    <span className="text-loss">{loopItems.filter((i) => i.status === "dead").length} 瘫痪</span>
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}

        <div className="flex items-center gap-4 mt-3 text-[10px] text-muted-foreground">
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-profit text-profit shadow-[0_0_6px_currentColor] inline-block" /> ok（阈值内）</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-warning text-warning shadow-[0_0_6px_currentColor] inline-block" /> warn（1×~2× 阈值）</span>
          <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-loss text-loss shadow-[0_0_6px_currentColor] inline-block" /> dead（超 2× 阈值）</span>
        </div>
      </SectionCard>
    </div>
  );
}

/** 三通道单卡 */
function ChannelCard({
  icon,
  title,
  desc,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  desc: string;
  children: React.ReactNode;
}) {
  return (
    <div className="glass rounded-lg p-3 space-y-2">
      <div className="flex items-center gap-1.5">
        <span className="text-primary">{icon}</span>
        <span className="text-sm font-medium">{title}</span>
      </div>
      <p className="text-[10px] text-muted-foreground -mt-1">{desc}</p>
      {children}
    </div>
  );
}

function ChannelRow({
  label,
  value,
  time,
  tone,
}: {
  label: string;
  value: string;
  time?: string | null;
  tone?: "good" | "warn" | "bad" | "ok" | "dead" | "default";
}) {
  const t = tone === "ok" ? "good" : tone === "dead" ? "bad" : tone;
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-muted-foreground shrink-0">{label}</span>
      <span className="flex items-center gap-1.5 min-w-0">
        {time && <Clock className="w-3 h-3 text-muted-foreground shrink-0" />}
        <span
          className={cn(
            "w-1.5 h-1.5 rounded-full shrink-0",
            t === "good" && "bg-profit text-profit shadow-[0_0_6px_currentColor]",
            t === "warn" && "bg-warning text-warning shadow-[0_0_6px_currentColor]",
            t === "bad" && "bg-loss text-loss shadow-[0_0_6px_currentColor]",
            (!t || t === "default") && "bg-muted-foreground/40"
          )}
        />
        <span className={cn("tabular-nums truncate font-medium", t === "good" && "text-profit", t === "warn" && "text-warning", t === "bad" && "text-loss")}>
          {value}
        </span>
      </span>
    </div>
  );
}

function StatusChip({ status }: { status: string }) {
  const cls =
    status === "ok"
      ? "bg-profit/15 text-profit"
      : status === "warn"
        ? "bg-warning/15 text-warning"
        : "bg-loss/15 text-loss";
  return (
    <Badge variant="outline" className={cn("font-normal", cls)}>
      <span
        className={cn(
          "w-1.5 h-1.5 rounded-full",
          status === "ok"
            ? "bg-profit text-profit shadow-[0_0_6px_currentColor]"
            : status === "warn"
              ? "bg-warning text-warning shadow-[0_0_6px_currentColor]"
              : "bg-loss text-loss shadow-[0_0_6px_currentColor]"
        )}
      />
      {status === "ok" ? "正常" : status === "warn" ? "迟滞" : "瘫痪"}
    </Badge>
  );
}

function ageClass(status?: string) {
  if (status === "dead") return "text-loss";
  if (status === "warn") return "text-warning";
  return "text-muted-foreground";
}

/** 距今天数判定：ok ≤ th；warn ≤ 2×th；dead 超 2×th（与后端语义一致） */
function ageTone(iso: string | null | undefined, thresholdHours: number): "ok" | "warn" | "bad" {
  if (!iso) return "bad";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "bad";
  const ageH = (Date.now() - t) / 3600000;
  if (ageH <= thresholdHours) return "ok";
  if (ageH <= thresholdHours * 2) return "warn";
  return "bad";
}

function ageLabel(iso: string | null, thresholdHours: number): string {
  if (!iso) return "无产出记录";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const ageH = (Date.now() - t) / 3600000;
  if (ageH < 24) return `${ageH.toFixed(1)}h 前`;
  return `${(ageH / 24).toFixed(1)}d 前`;
}

function fmtCount(v?: number) {
  return v == null ? "0" : v.toLocaleString();
}

function fmtTime(iso: string) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

function fmtInterval(sec: number) {
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}min`;
  return `${Math.round(sec / 3600)}h`;
}

export default HealthChannelsPanel;
