"use client";

/**
 * 学习进化链路总览（[2026-10-03 用户需求]）
 *
 * 用户原话：「智能学习里前端功能应该后端对不上了，需要重新校对、功能重排、重新设计这个模块的前端，
 * 和现在运行的后端对齐，并补齐功能显示…如果有断链或设计逻辑缺陷需要补齐，也看看现在的学习进化链路
 * 是不是真的起作用」。
 *
 * 本面板是这次重校的**结论页**：把学习进化链路的 9 个环节按真实接口逐个点亮，
 * 每环给出「计数 + 状态图标（✅在跑 / ⚠️停滞 / ❌断链）+ 判据」，点一下跳到对应 Tab 深挖。
 *
 * 判据（全部来自实测，不猜）：
 *   · 决策→血缘账本：`/api/learning/events` 条数（实测仅 4 条，来源全是 selftest/backtest ⇒ ⚠️）
 *   · 复盘回溯 / 策略记忆 / 进化事件：`/api/learning/health.items[].detail` 的累计量
 *   · Hermes L1-L4：`/api/hermes/dashboard`
 *     - L3 验收闭环：pending>0 且 accepted+rejected==0 ⇒ ❌（实测 286 pending / 0 验收）
 *     - L4 晋升闭环：validated/promoted_live==0 且 failed>0 ⇒ ❌（实测 345 孵化 / 0 晋升 / 123 失败）
 *     - L2 实验闭环：running_ab_tests==0 ⇒ ⚠️
 *   · 回放：合成样本占比 >90% ⇒ ⚠️（实测 300/302 合成）
 *   · RL：shadow_only ⇒ ⚠️（设计如此，需在界面上说清不是故障）
 */
import { useCallback, useEffect, useState } from "react";
import {
  Activity, AlertTriangle, CheckCircle2, XCircle, RefreshCw, Loader2, ArrowRight, HeartPulse,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getBackendUrl } from "@/lib/backend-config";
import { SectionCard, RefreshButton } from "@/components/operations/IlcUi";

const BACKEND = getBackendUrl().replace(/\/$/, "");

type Level = "ok" | "warn" | "bad" | "unknown";

const LEVEL_STYLE: Record<Level, { icon: any; cls: string; text: string; label: string }> = {
  ok: { icon: CheckCircle2, cls: "text-profit border-profit/40 bg-profit/10", text: "text-profit", label: "在跑" },
  warn: { icon: AlertTriangle, cls: "text-amber-300 border-amber-400/40 bg-amber-400/10", text: "text-amber-300", label: "停滞" },
  bad: { icon: XCircle, cls: "text-loss border-loss/40 bg-loss/10", text: "text-loss", label: "断链" },
  unknown: { icon: Activity, cls: "text-muted-foreground border-border bg-muted/20", text: "text-muted-foreground", label: "无数据" },
};

interface Stage {
  key: string;
  name: string;
  tab?: string;
  value: string;
  detail: string;
  level: Level;
}

const num = (v: unknown): number => {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
};

/** 从 /api/learning/health 的 items 里取某个环节的累计量文字（后端 detail 已写"累计 N 条…"）。 */
function healthDetail(items: any[], name: string): { detail: string; status: string } {
  const it = (items || []).find((x) => String(x?.name) === name);
  return { detail: String(it?.detail || "—"), status: String(it?.status || "unknown") };
}

export function ChainOverviewPanel({ onGoTab }: { onGoTab?: (tab: string) => void }) {
  const [health, setHealth] = useState<any>({});
  const [loop, setLoop] = useState<any>({});
  const [dash, setDash] = useState<any>({});
  const [replay, setReplay] = useState<any>({});
  const [rl, setRl] = useState<any>({});
  const [events, setEvents] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const rs = await Promise.allSettled([
        fetch(`${BACKEND}/api/learning/health`).then((r) => r.json()),
        fetch(`${BACKEND}/api/learning/loop/status`).then((r) => r.json()),
        fetch(`${BACKEND}/api/hermes/dashboard`).then((r) => r.json()),
        fetch(`${BACKEND}/api/learning/replay/stats`).then((r) => r.json()),
        fetch(`${BACKEND}/api/learning/rl/status`).then((r) => r.json()),
        fetch(`${BACKEND}/api/learning/events`).then((r) => r.json()),
      ]);
      const [h, l, d, rp, r, ev] = rs;
      if (h.status === "fulfilled") setHealth(h.value ?? {});
      if (l.status === "fulfilled") setLoop(l.value ?? {});
      if (d.status === "fulfilled") setDash(d.value ?? {});
      if (rp.status === "fulfilled") setReplay(rp.value ?? {});
      if (r.status === "fulfilled") setRl(r.value ?? {});
      if (ev.status === "fulfilled") setEvents(ev.value?.events ?? []);
      const failed = rs.filter((x) => x.status === "rejected").length;
      if (failed) setErr(`${failed}/6 个接口请求失败（详见下方缺失环节）`);
    } finally {
      setLoading(false);
    }
  }, []);
  // [2026-10-03] 首屏加载改为宏任务触发：load() 开头会同步 setLoading(true)，react-hooks/set-state-in-effect 不允许在 effect 体内同步 setState。
  useEffect(() => {
    const t = window.setTimeout(() => { void load(); }, 0);
    return () => window.clearTimeout(t);
  }, [load]);

  const items: any[] = health?.items ?? [];
  const retro = healthDetail(items, "retrospective");
  const memory = healthDetail(items, "strategy_memory");
  const evolution = healthDetail(items, "evolution");
  const loopOutcome = healthDetail(items, "loop_outcome_batch");

  const l1 = dash?.l1_wisdom ?? {};
  const l2 = dash?.l2_prompt ?? {};
  const l3 = dash?.l3_architecture ?? {};
  const l4 = dash?.l4_genesis ?? {};
  const maturity = dash?.maturity ?? {};

  const abTests = num(l2.running_ab_tests);
  const l3Pending = num(l3.pending);
  // [2026-10-03 自我纠正] L3 的 `accept_proposal` 直接落 `implemented`（实测：
  // POST /api/hermes/architecture/967/accept → {"status":"implemented"}，pending 286→285、
  // implemented 681→682），所以 `accepted`/`rejected` 计数**恒为 0 是状态词汇设计，
  // 不能当作"无人裁决"的证据**。真正可判定的是 pending 积压占比 + 已实施数。
  const l3Verified = num(l3.accepted) + num(l3.rejected) + num(l3.implemented);
  const l3PendingPct = num(l3.total) > 0 ? (l3Pending / num(l3.total)) * 100 : 0;
  const l4Validated = num(l4.validated);
  const l4Promoted = num(l4.promoted_live);
  const l4Failed = num(l4.failed);
  const replayTotal = num(replay.total);
  const synth = num(replay.by_source?.synthetic);
  const synthPct = replayTotal > 0 ? (synth / replayTotal) * 100 : 0;
  const shadowOnly = rl?.shadow?.shadow_only === true;
  // [2026-10-04 工作流③] 后端 `shadow_service.status()` 已返回 live_block_reason
  // （哪一道门拦住了实盘接管：shadow_only / governor_not_approved / paper_samples_insufficient），
  // 面板此前只显示布尔值 ⇒ 用户看不到"为什么还是影子"。这里如实展示。
  const shadowBlock = String((rl?.shadow as { live_block_reason?: string } | undefined)?.live_block_reason ?? "");
  const loopEnabled = loop?.enabled === true && loop?.paused !== true;

  const stages: Stage[] = [
    {
      key: "ledger", name: "① 决策 → 血缘账本", tab: "lineage",
      value: `${events.length} 条事件`,
      detail: events.length < 20
        ? "账本几乎空转：仅自测/回测来源入库，真实交易决策未入账（设计缺陷，待补）"
        : "生产决策正在入账",
      level: events.length === 0 ? "bad" : events.length < 20 ? "warn" : "ok",
    },
    {
      key: "retro", name: "② 复盘回溯", tab: "channels",
      value: retro.detail.replace(/^累计\s*/, ""),
      detail: "LearningLoop outcome 批处理（5min）→ 绩效矩阵反哺",
      level: retro.status === "ok" ? "ok" : retro.status === "warn" ? "warn" : "bad",
    },
    {
      key: "memory", name: "③ 策略记忆", tab: "lineage",
      value: memory.detail.replace(/^累计\s*/, ""),
      detail: "按 (策略, 市况) 记忆胜负，供选币/风控反哺",
      level: memory.status === "ok" ? "ok" : "warn",
    },
    {
      key: "evolution", name: "④ 进化事件", tab: "lifecycle",
      value: evolution.detail.replace(/^累计\s*/, ""),
      detail: "RuntimeGovernor 统一下发，决策核心 60s 内生效",
      level: evolution.status === "ok" ? "ok" : "warn",
    },
    {
      key: "l1", name: "⑤ Hermes L1 · 智慧", tab: "hermes",
      value: `${num(l1.total_records)} 条 / ${num(l1.patterns)} 模式`,
      detail: "参数级成功率统计 → 智慧库（/api/hermes/wisdom）",
      level: num(l1.total_records) > 0 ? "ok" : "bad",
    },
    {
      key: "l2", name: "⑥ Hermes L2 · 提示词", tab: "hermes",
      value: `${num(l2.active_versions)} 个 active 版本`,
      detail: abTests === 0 ? "无 A/B 实验在跑（版本只增不验，闭环缺失）" : `${abTests} 个 A/B 实验在跑`,
      level: abTests === 0 ? "warn" : "ok",
    },
    {
      key: "l3", name: "⑦ Hermes L3 · 架构", tab: "hermes",
      value: `${num(l3.total)} 提案 / ${l3Pending} 待审`,
      detail: l3PendingPct > 25
        ? `积压 ${l3Pending} 条（占 ${l3PendingPct.toFixed(0)}%）—— 驱动停摆期间无人裁决；已实施 ${num(l3.implemented)}（accept 直接落 implemented，故 accepted 计数恒为 0）`
        : `已裁决/实施 ${l3Verified} 条，待审 ${l3Pending} 条（占 ${l3PendingPct.toFixed(0)}%）`,
      level: num(l3.total) === 0 ? "bad" : (l3PendingPct > 25 && num(l3.implemented) === 0) ? "bad" : l3PendingPct > 25 ? "warn" : "ok",
    },
    {
      key: "l4", name: "⑧ Hermes L4 · 起源", tab: "hermes",
      value: `${num(l4.total)} 候选 / 晋升 ${l4Promoted}`,
      detail: l4Validated === 0 && l4Promoted === 0
        ? `晋升闭环断：${num(l4.incubating)} 孵化中、0 validated、0 promoted、失败 ${l4Failed}`
        : `已验证 ${l4Validated} / 晋升 ${l4Promoted} / 失败 ${l4Failed}`,
      level: num(l4.total) === 0 ? "bad" : (l4Validated === 0 && l4Promoted === 0) ? "bad" : "ok",
    },
    {
      key: "replay", name: "⑨ 回放与 RL", tab: "compute",
      value: `回放 ${replayTotal} 样本`,
      detail: `合成占比 ${synthPct.toFixed(0)}%${shadowOnly ? ` · RL 仅影子（${shadowBlock || "shadow_only，未上实盘"}）` : shadowBlock ? ` · RL 可接管但被拦：${shadowBlock}` : ""}`,
      level: replayTotal === 0 ? "bad" : synthPct > 90 ? "warn" : "ok",
    },
  ];

  const badCount = stages.filter((s) => s.level === "bad").length;
  const warnCount = stages.filter((s) => s.level === "warn").length;
  const okCount = stages.filter((s) => s.level === "ok").length;

  const loopJobs = loop?.last_tick_at && typeof loop.last_tick_at === "object"
    ? Object.entries(loop.last_tick_at as Record<string, unknown>)
    : [];

  return (
    <div className="space-y-4">
      {/* 结论条 */}
      <div className="glass rounded-lg p-4 flex flex-wrap items-center gap-3">
        <HeartPulse className={cn("w-4 h-4", badCount > 0 ? "text-loss" : warnCount > 0 ? "text-amber-300" : "text-profit")} />
        <span className="text-sm font-medium">
          链路体检：{okCount} 环在跑 · {warnCount} 环停滞 · <span className={badCount ? "text-loss" : ""}>{badCount} 环断链</span>
        </span>
        <span className="text-xs text-muted-foreground">
          学习循环 {loopEnabled ? "已启用" : "未启用"} · 健康总评 <span className={health?.overall === "ok" ? "text-profit" : "text-amber-300"}>{String(health?.overall ?? "—")}</span>
          {" "}（{String(health?.checked_at ?? "").slice(11, 19)}）
        </span>
        <span className="text-xs text-muted-foreground ml-auto">
          Hermes 成熟度 <span className="grad-text font-bold">{num(maturity.maturity_score)}</span>
          {" "}（L1 {num(maturity.l1_wisdom)} / L2 {num(maturity.l2_prompt)} / L3 {num(maturity.l3_architecture)} / L4 {num(maturity.l4_genesis)}）
        </span>
        <RefreshButton onClick={() => void load()} loading={loading} />
      </div>

      {err && (
        <div className="rounded-md border border-loss/40 bg-loss/10 px-3 py-2 text-xs text-loss flex items-center gap-2">
          <AlertTriangle className="w-3.5 h-3.5" />{err}
        </div>
      )}

      {/* 九环链路 */}
      <SectionCard title="学习进化链路（点击任一环跳到对应页签深挖）" action={<Activity className="w-3.5 h-3.5 text-primary" />}>
        {loading && stages.every((s) => s.level === "unknown") ? (
          <div className="flex justify-center py-10"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>
        ) : (
          <div className="space-y-2">
            {stages.map((s) => {
              const st = LEVEL_STYLE[s.level];
              const Icon = st.icon;
              return (
                <button
                  key={s.key}
                  data-testid={`chain-stage-${s.key}`}
                  onClick={() => s.tab && onGoTab?.(s.tab)}
                  className={cn(
                    "w-full text-left rounded-lg border px-3 py-2 flex items-center gap-3 transition-colors",
                    st.cls, s.tab ? "hover:brightness-125 cursor-pointer" : "cursor-default",
                  )}
                >
                  <Icon className="w-4 h-4 shrink-0" />
                  <span className="text-xs font-medium w-[168px] shrink-0">{s.name}</span>
                  <span className={cn("text-xs font-mono w-[190px] shrink-0", st.text)}>{s.value}</span>
                  <span className="text-[11px] text-muted-foreground flex-1">{s.detail}</span>
                  <span className={cn("text-[10px] px-1.5 py-0.5 rounded border shrink-0", st.cls)}>{st.label}</span>
                  {s.tab && <ArrowRight className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />}
                </button>
              );
            })}
          </div>
        )}
      </SectionCard>

      {/* 循环心跳 */}
      <SectionCard
        title={`学习循环心跳（注册 ${
          Array.isArray(loop?.registered)
            ? loop.registered.length
            : loop?.registered && typeof loop.registered === "object"
              ? Object.keys(loop.registered).length
              : num(loop?.registered)
        } 个任务 · 心跳 ${loopJobs.length} 条）`}
        action={<Activity className="w-3.5 h-3.5 text-primary" />}
      >
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
          {loopJobs.length === 0 ? (
            <div className="text-xs text-muted-foreground col-span-full">无作业心跳数据</div>
          ) : (
            loopJobs.slice(0, 12).map(([job, ts]) => (
              <div key={job} className="rounded border border-border/50 px-2 py-1.5">
                <div className="text-[10px] text-muted-foreground font-mono truncate">{job}</div>
                <div className="text-[11px] tabular-nums">{String(ts).slice(0, 19).replace("T", " ") || "—"}</div>
              </div>
            ))
          )}
        </div>
        {loopOutcome.detail !== "—" && (
          <div className="mt-2 text-[11px] text-muted-foreground">{loopOutcome.detail}</div>
        )}
      </SectionCard>
    </div>
  );
}

export default ChainOverviewPanel;

