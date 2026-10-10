"use client";

/**
 * 高频学习入口面板（`/intelligent-learning?tab=inlet`）
 *
 * [2026-10-10 前端升级] 把「PP 平仓腿 → 主脑」这条入口的**进度与断链**摆出来。
 *
 * 为什么必须有这一页：入口 tick 是**后端重启后**才注册的（`register_tasks` 在启动时跑），
 * 而游标文件是"跑成功过一轮"的唯一硬证据。两者都不看的话，操作者只能看到
 * 「0」——分不清"没数据 / 没跑 / 没重启"，正是本仓反复吃过的亏
 * （见 `learning-evidence` 页的"样本不足只显示门槛"同源原则）。
 *
 * 判据全部由后端给（`health` / `health_reason`），前端只上色，不重算 —— 避免
 * 面板与后端各算一套、口径漂移。
 */
import { useCallback, useEffect, useState } from "react";
import {
  Activity, AlertTriangle, CheckCircle2, Info, Loader2, PlayCircle, PauseCircle, XCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { SectionCard, RefreshButton, StatCard } from "@/components/operations/IlcUi";
import { Button } from "@/components/ui/button";
import { fmtNum, fmtTime } from "@/lib/format";
import {
  getInletStatus,
  setLoopPaused,
  triggerLoopJob,
  type InletHealth,
  type InletStatus,
  type LoopTriggerResult,
} from "@/lib/learningLaneApi";

const HEALTH_STYLE: Record<InletHealth, { icon: unknown; cls: string; label: string }> = {
  ok: { icon: CheckCircle2, cls: "text-profit border-profit/40 bg-profit/10", label: "在跑" },
  warn: { icon: AlertTriangle, cls: "text-amber-300 border-amber-400/40 bg-amber-400/10", label: "需注意" },
  bad: { icon: XCircle, cls: "text-loss border-loss/40 bg-loss/10", label: "断链" },
  off: { icon: PauseCircle, cls: "text-muted-foreground border-border bg-muted/20", label: "按开关关闭" },
  unknown: { icon: Activity, cls: "text-muted-foreground border-border bg-muted/20", label: "未知" },
};

/** 数值缺失一律显示 `—` 而不是 0：0 会被读成"跑了但没数据"。 */
function num(v: unknown): string {
  return v === null || v === undefined || Number.isNaN(Number(v)) ? "—" : fmtNum(Number(v), 0);
}

export function LaneInletPanel() {
  const [data, setData] = useState<InletStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [triggering, setTriggering] = useState(false);
  const [pausing, setPausing] = useState(false);
  const [lastRun, setLastRun] = useState<LoopTriggerResult | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      setData(await getInletStatus());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  // 首屏走宏任务触发（本仓约定：effect 体内不直接同步 setState）
  useEffect(() => {
    const t = window.setTimeout(() => void load(), 0);
    const iv = window.setInterval(() => void load(), 30000);
    return () => {
      window.clearTimeout(t);
      window.clearInterval(iv);
    };
  }, [load]);

  const onTrigger = async () => {
    if (!window.confirm(
      "手动补扫一次高频入口？\n\n" +
      "它把 lane_ledger 里尚未进主脑的 PP 平仓腿按往返补进统一学习入口。\n" +
      "只写学习记忆（strategy_memories / regime_scores），**不改任何交易参数**。"
    )) return;
    setTriggering(true);
    setErr(null);
    try {
      const r = await triggerLoopJob("hft_brain");
      setLastRun(r);
      if (!r.ok) setErr(r.error || "触发失败");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setTriggering(false);
    }
  };

  const onTogglePause = async () => {
    const paused = data?.tick?.loop_paused === true;
    const verb = paused ? "恢复" : "暂停";
    if (!window.confirm(
      `${verb}整个学习循环？\n\n` +
      "暂停会停掉**全部**学习 tick（结果批处理 / Kelly / 协调器 / 高频入口 / 因子衰减…），" +
      "但交易链路不受影响。运行时生效，重启后回到启用状态。"
    )) return;
    setPausing(true);
    try {
      await setLoopPaused(!paused);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setPausing(false);
    }
  };

  const health: InletHealth = (data?.health as InletHealth) || "unknown";
  const hs = HEALTH_STYLE[health];
  const Icon = hs.icon as React.ComponentType<{ className?: string }>;
  const total = data?.ledger?.roundtrips_since_era ?? null;
  const ingested = data?.cursor?.ingested_total ?? null;
  const pct = total && ingested !== null ? Math.min(100, (ingested / total) * 100) : null;
  const lastExtra = (data?.tick?.last_extra || {}) as Record<string, unknown>;

  return (
    <div className="space-y-4">
      {/* 结论条 */}
      <div className="glass rounded-lg p-4 flex flex-wrap items-center gap-3">
        <Icon className={cn("w-4 h-4", hs.cls.split(" ")[0])} />
        <span className={cn("text-xs px-1.5 py-0.5 rounded border", hs.cls)}>{hs.label}</span>
        <span className="text-sm">{data?.health_reason || "加载中…"}</span>
        <span className="text-xs text-muted-foreground ml-auto">
          {data?.as_of ? `数据截止 ${fmtTime(data.as_of)}` : ""}
        </span>
        <RefreshButton onClick={() => void load()} loading={busy} />
      </div>

      {data?.tick?.loop_paused ? (
        <div className="rounded-md border border-amber-400/40 bg-amber-400/10 px-3 py-2 text-xs text-amber-300 flex items-center gap-2">
          <PauseCircle className="w-3.5 h-3.5" />
          学习循环已暂停 —— 所有 tick（含本入口）都不会自动跑；恢复后按原间隔继续。
        </div>
      ) : null}

      {err ? (
        <div className="rounded-md border border-loss/40 bg-loss/10 px-3 py-2 text-xs text-loss flex items-center gap-2">
          <AlertTriangle className="w-3.5 h-3.5" />{err}
        </div>
      ) : null}

      {/* 进度 */}
      <SectionCard
        title="高频入口进度（PP 往返 → 主脑）"
        description="口径：lane_ledger 里 meta_json->>'rt_bp' 非空的平仓腿，按往返汇成一笔交给 unified_learning.process_outcome（persist_trade=False）"
        action={
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => void onTrigger()} disabled={triggering}>
              {triggering ? <Loader2 className="h-4 w-4 mr-1.5 animate-spin" />
                : <PlayCircle className="h-4 w-4 mr-1.5" />}
              手动补扫一次
            </Button>
            <Button variant="outline" size="sm" onClick={() => void onTogglePause()} disabled={pausing}>
              {data?.tick?.loop_paused ? <PlayCircle className="h-4 w-4 mr-1.5" />
                : <PauseCircle className="h-4 w-4 mr-1.5" />}
              {data?.tick?.loop_paused ? "恢复学习循环" : "暂停学习循环"}
            </Button>
          </div>
        }
      >
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <StatCard
            label="era 起往返"
            value={num(total)}
            hint={data?.ledger?.era_start_iso ? `era 起点 ${fmtTime(data.ledger.era_start_iso)}` : undefined}
          />
          <StatCard
            label="已进主脑"
            value={data?.cursor?.present ? num(ingested) : "—"}
            tone={data?.cursor?.present ? "good" : "default"}
            hint={data?.cursor?.present
              ? `跳过 ${num(data.cursor.skipped_total)} · 失败 ${num(data.cursor.failed_total)}`
              : "游标文件不存在 ⇒ 入口还没跑过一轮"}
          />
          <StatCard
            label="待补"
            value={data?.pending_from_cursor === null || data?.pending_from_cursor === undefined
              ? "—" : num(data.pending_from_cursor)}
            tone={(data?.pending_from_cursor ?? 0) > 0 ? "warn" : "good"}
            hint={`每次上限 ${num((data?.budget as Record<string, unknown>)?.limit_per_tick)} 笔（有界）`}
          />
          <StatCard
            label="最近推进"
            value={data?.cursor?.applied_at_iso ? fmtTime(data.cursor.applied_at_iso) : "—"}
            tone={data?.cursor?.applied_at_iso ? "good" : "default"}
            hint={data?.cursor?.present
              ? `游标 ts=${num(data.cursor.ts)} id=${num(data.cursor.id)}`
              : "未开始"}
          />
        </div>

        {/* 进度条 */}
        <div className="mb-4">
          <div className="h-2 rounded bg-muted/40 overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-cyan-400 to-violet-500"
              style={{ width: `${pct ?? 0}%` }}
            />
          </div>
          <div className="mt-1 text-[11px] text-muted-foreground">
            {pct === null
              ? "进度未知（缺游标或账本读取失败）"
              : `已进主脑 ${num(ingested)} / ${num(total)}（${pct.toFixed(1)}%）`}
            {" · "}
            {data?.ledger?.legs_last_10min !== null && data?.ledger?.legs_last_10min !== undefined
              ? `最近 10 分钟账本新增 ${num(data.ledger.legs_last_10min)} 腿`
              : ""}
            {" · "}
            最近一笔平仓 {data?.ledger?.last_close_ts ? fmtTime(data.ledger.last_close_ts) : "—"}
          </div>
        </div>

        {lastRun ? (
          <div
            className={cn(
              "mb-3 rounded-md border px-3 py-2 text-xs",
              lastRun.ok ? "border-profit/40 bg-profit/10 text-profit"
                : "border-loss/40 bg-loss/10 text-loss",
            )}
          >
            上次手动补扫：{lastRun.ok ? "成功" : `失败（${lastRun.error ?? "?"}）`}
            {lastRun.ok ? (
              <>
                {" · 扫描 "}{num(lastRun.scanned)}
                {" · 进主脑 "}{num(lastRun.ingested)}
                {" · 跳过 "}{num(lastRun.skipped_rows)}
                {" · 失败 "}{num(lastRun.failed)}
                {typeof lastRun.elapsed_ms === "number" ? ` · 耗时 ${lastRun.elapsed_ms}ms` : ""}
              </>
            ) : null}
          </div>
        ) : null}

        <div className="rounded-md border border-border/50 px-3 py-2 text-[11px] text-muted-foreground flex gap-2">
          <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <div className="space-y-0.5">
            <div>
              入口以 <code className="font-mono">tier=&quot;hft&quot;</code> /
              <code className="font-mono"> nature=&quot;scalp&quot;</code> 记账：
              <b>不挂中/长线论题</b>、<b>不写 strategy_trades</b>、<b>不记短线熔断</b>
              （隔离开关 <code className="font-mono">P4_HFT_ISOLATION</code>）。
            </div>
            <div>
              开关 <code className="font-mono">MM_PP_TO_BRAIN</code>
              {" = "}<b>{data?.enabled ? "1（开）" : "0（关）"}</b>
              {" · 隔离 "}
              <b>{data?.isolation_on === null || data?.isolation_on === undefined
                ? "—" : (data.isolation_on ? "开" : "关(会污染熔断)")}</b>
            </div>
          </div>
        </div>
      </SectionCard>

      {/* 心跳 */}
      <SectionCard
        title="入口 tick 心跳"
        description="学习循环里的 learning_loop_hft_brain —— 默认 5 分钟一次（快速试单模式 60s）"
        action={
          <span className="text-[11px] text-muted-foreground">
            {data?.tick?.registered_in_loop ? "已注册" : "未注册（后端未重启）"}
          </span>
        }
      >
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
          <StatCard label="上次 tick" value={data?.tick?.last_tick_at ? fmtTime(data.tick.last_tick_at) : "—"}
            tone={data?.tick?.has_heartbeat ? "good" : "bad"} />
          <StatCard label="下次 tick" value={data?.tick?.next_tick_at ? fmtTime(data.tick.next_tick_at) : "—"} />
          <StatCard label="间隔" value={data?.tick?.interval_sec ? `${data.tick.interval_sec}s` : "—"} />
          <StatCard label="耗时 p50/p95"
            value={data?.tick?.p50_ms !== null && data?.tick?.p50_ms !== undefined
              ? `${num(data.tick.p50_ms)}/${num(data.tick.p95_ms)}ms` : "—"} />
        </div>
        {Object.keys(lastExtra).length > 0 ? (
          <div className="text-[11px] text-muted-foreground font-mono break-all">
            最近一次 extra：{JSON.stringify(lastExtra)}
          </div>
        ) : (
          <div className="text-[11px] text-muted-foreground">
            还没有 tick 摘要（该 job 未跑过或本次进程刚重启）
          </div>
        )}
        {data?.ledger?.error || data?.tick?.error ? (
          <div className="mt-2 text-[11px] text-loss">
            {data?.ledger?.error ? `账本读取：${data.ledger.error} ` : ""}
            {data?.tick?.error ? `循环状态：${data.tick.error}` : ""}
          </div>
        ) : null}
      </SectionCard>
    </div>
  );
}

export default LaneInletPanel;
