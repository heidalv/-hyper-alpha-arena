"use client";

/**
 * 并行回测加速卡 — 单层面板 + SubSection
 */
import { useMemo, useState } from "react";
import { Play, Gauge as GaugeIcon, GitBranch, ListTree, Timer } from "lucide-react";
import {
  getEvolutionStatus,
  getMetrics,
  getConfigs,
  triggerEvolution,
  putConfigs,
  type ConfigItem,
} from "@/lib/api/compute";
import {
  ComputePanel,
  EmptyBox,
  LoadingBox,
  PanelError,
  RefreshButton,
  StatusBadge,
  SubSection,
  fmtDt,
  usePolling,
} from "./common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

function ConfigRow({ c }: { c: ConfigItem | undefined }) {
  if (!c) {
    return (
      <div className="flex items-center justify-between text-xs py-1 text-muted-foreground">
        <span>—</span>
        <span>—</span>
      </div>
    );
  }
  return (
    <div className="flex items-center justify-between text-xs py-1 gap-2">
      <span className="text-muted-foreground truncate" title={c.desc}>
        {c.label}
      </span>
      <span className="tabular-nums flex items-center gap-2 flex-shrink-0">
        {c.source === "env" && (
          <span className="text-[10px] px-1 py-0.5 rounded bg-blue-500/10 text-blue-600 dark:text-blue-400 border border-blue-500/20">
            覆盖
          </span>
        )}
        <b>{String(c.value)}</b>
      </span>
    </div>
  );
}

function findConfig(configs: ConfigItem[], key: string): ConfigItem | undefined {
  return configs.find((c) => c.key === key);
}

function GpMinerPanel({
  configs,
  activities,
}: {
  configs: ConfigItem[];
  activities: Array<{
    phase: string;
    action: string;
    factor_id: string;
    source?: string;
    reason: string;
    created_at: string;
  }>;
}) {
  const mine = activities
    .filter((a) => a.phase === "mine" && a.source?.includes("gp"))
    .slice(0, 3);
  return (
    <SubSection
      title="GP 挖掘器"
      icon={<ListTree className="w-3.5 h-3.5 text-muted-foreground" />}
      badge={
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-profit/10 text-green-600 dark:text-profit border border-green-500/20">
          joblib 并行
        </span>
      }
    >
      <ConfigRow c={findConfig(configs, "FACTOR_GP_POPULATION")} />
      <ConfigRow c={findConfig(configs, "FACTOR_GP_GENERATIONS")} />
      <ConfigRow c={findConfig(configs, "FACTOR_GP_SEEDS")} />
      {mine.length > 0 && (
        <div className="pt-1 border-t border-border/60 space-y-0.5">
          <p className="text-[11px] text-muted-foreground">最近挖掘</p>
          {mine.map((m, i) => (
            <p key={i} className="text-[11px] truncate">
              <span className="text-muted-foreground">{fmtDt(m.created_at)}</span>{" "}
              {m.factor_id.slice(0, 12)}
              <span className="text-muted-foreground"> — {m.reason}</span>
            </p>
          ))}
        </div>
      )}
    </SubSection>
  );
}

function MctsMinerPanel({
  configs,
  activities,
}: {
  configs: ConfigItem[];
  activities: Array<{
    phase: string;
    action: string;
    factor_id: string;
    source?: string;
    reason: string;
    created_at: string;
  }>;
}) {
  const chains = activities
    .filter((a) => a.phase === "mine" && a.source === "mcts_chain")
    .slice(0, 3);
  const enabled = findConfig(configs, "FACTOR_MCTS_ENABLED");
  return (
    <SubSection
      title="MCTS 挖掘器"
      icon={<GitBranch className="w-3.5 h-3.5 text-muted-foreground" />}
      badge={enabled ? <StatusBadge status={enabled.value ? "ok" : "stopped"} /> : undefined}
    >
      <ConfigRow c={findConfig(configs, "FACTOR_MCTS_ITERATIONS")} />
      <ConfigRow c={findConfig(configs, "FACTOR_MCTS_ROOTS")} />
      <ConfigRow c={findConfig(configs, "FACTOR_MCTS_CHILDREN")} />
      {chains.length > 0 && (
        <div className="pt-1 border-t border-border/60 space-y-0.5">
          <p className="text-[11px] text-muted-foreground">最近进化链</p>
          {chains.map((m, i) => (
            <p key={i} className="text-[11px] truncate">
              <span className="text-muted-foreground">{fmtDt(m.created_at)}</span>{" "}
              {m.factor_id.slice(0, 12)}
              <span className="text-muted-foreground"> — {m.reason}</span>
            </p>
          ))}
        </div>
      )}
    </SubSection>
  );
}

function ThreadConfig({ configs }: { configs: ConfigItem[] }) {
  const current = findConfig(configs, "FACTOR_GP_MAX_WORKERS");
  const [draft, setDraft] = useState<string>(String(current?.value ?? 32));
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const onSave = async () => {
    const n = Number(draft);
    if (!Number.isInteger(n) || n < 1 || n > 64) {
      setMsg({ ok: false, text: "线程数需为 1~64 的整数" });
      return;
    }
    setSaving(true);
    setMsg(null);
    try {
      const res = await putConfigs({ FACTOR_GP_MAX_WORKERS: n });
      if (res.ok) {
        setMsg({ ok: true, text: `已下发（${res.applied?.[0]?.source ?? "overrides"}）` });
      } else {
        const first = Object.values(res.errors ?? {})[0];
        setMsg({ ok: false, text: typeof first === "string" ? first : "下发失败" });
      }
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : "下发失败" });
    } finally {
      setSaving(false);
      setTimeout(() => setMsg(null), 6000);
    }
  };

  return (
    <SubSection
      title="GP 并行线程"
      icon={<GaugeIcon className="w-3.5 h-3.5 text-muted-foreground" />}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Input
          type="number"
          min={1}
          max={64}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          className="w-24 h-8 text-xs"
        />
        <Button size="sm" variant="outline" onClick={onSave} disabled={saving}>
          {saving ? "下发中…" : "保存并下发"}
        </Button>
        <span className="text-[11px] text-muted-foreground">
          当前 <b className="tabular-nums">{String(current?.value ?? "—")}</b>
        </span>
      </div>
      {msg && (
        <p className={cn("text-[11px]", msg.ok ? "text-green-600 dark:text-profit" : "text-red-500")}>
          {msg.text}
        </p>
      )}
    </SubSection>
  );
}

function SpeedupChart() {
  const { data, loading, error, refresh } = usePolling(() => getMetrics("7d"), 30000);
  const taskSeries = data?.tasks ?? {};
  const hasData = Object.keys(taskSeries).some((k) => (taskSeries[k] ?? []).length > 0);

  return (
    <SubSection
      title="任务耗时 / 加速比"
      icon={<Timer className="w-3.5 h-3.5 text-muted-foreground" />}
      action={
        <button
          onClick={refresh}
          className="text-[11px] text-primary hover:underline disabled:opacity-50"
          disabled={loading}
        >
          {loading ? "刷新中…" : "刷新"}
        </button>
      }
    >
      <PanelError error={error} />
      {!hasData ? (
        <EmptyBox message="待积累：任务耗时统计落库后自动显示" />
      ) : (
        <div className="text-xs text-muted-foreground">
          已积累 {Object.keys(taskSeries).length} 类任务序列
        </div>
      )}
    </SubSection>
  );
}

export function ParallelBacktestCard() {
  const { data: evo, loading: evoLoading, error: evoError, refresh: evoRefresh } =
    usePolling(getEvolutionStatus, 30000);
  const { data: cfgData, loading: cfgLoading, error: cfgError } =
    usePolling(getConfigs, 30000);

  const [triggering, setTriggering] = useState(false);
  const [triggerMsg, setTriggerMsg] = useState<string | null>(null);

  const configs = useMemo(() => cfgData?.configs ?? [], [cfgData]);
  const activities = evo?.recent_activity ?? [];
  const hasActivity = activities.length > 0;

  const onTrigger = async () => {
    if (!window.confirm("确认手动触发因子进化？（后台线程 + 单飞锁，重复触发将被拒绝）")) return;
    setTriggering(true);
    setTriggerMsg(null);
    try {
      const res = await triggerEvolution();
      setTriggerMsg(res.message || (res.running ? "进化已在运行" : "已触发"));
    } catch (e) {
      setTriggerMsg(e instanceof Error ? e.message : "触发失败");
    } finally {
      setTriggering(false);
      setTimeout(() => setTriggerMsg(null), 8000);
    }
  };

  const status =
    evoLoading && !evo ? undefined : evo?.running ? "running" : hasActivity ? "ok" : "stopped";

  return (
    <ComputePanel
      title="并行进化"
      description="GP / MCTS 挖掘与并行线程配置"
      status={status}
      action={
        <div className="flex items-center gap-2">
          <RefreshButton onClick={evoRefresh} loading={evoLoading} />
          <Button size="sm" onClick={onTrigger} disabled={triggering || evo?.running}>
            <Play className="w-3.5 h-3.5 mr-1.5" />
            {triggering ? "触发中…" : "触发进化"}
          </Button>
        </div>
      }
    >
      <PanelError error={evoError || cfgError} />
      {triggerMsg && <p className="text-xs text-primary mb-3">{triggerMsg}</p>}

      {evo && (
        <div className="text-[11px] text-muted-foreground mb-3 grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-0.5">
          <p>
            最近活动 {fmtDt(evo.last_activity_at)}｜调度 {evo.schedule.daily_cron}
          </p>
          <p>
            活跃因子 {evo.active_factors.total}（
            {Object.entries(evo.active_factors.state_dist ?? {})
              .map(([k, v]) => `${k} ${v}`)
              .join(" / ")}
            ）
          </p>
        </div>
      )}

      {cfgLoading && !cfgData ? (
        <LoadingBox text="读取配置…" />
      ) : (
        <div className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <GpMinerPanel configs={configs} activities={activities} />
            <MctsMinerPanel configs={configs} activities={activities} />
          </div>
          <ThreadConfig configs={configs} />
          <SpeedupChart />
        </div>
      )}
    </ComputePanel>
  );
}
