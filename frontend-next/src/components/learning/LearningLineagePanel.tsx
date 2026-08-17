"use client";

/**
 * 学习血缘面板（迁移自旧 /evolution「学习血缘」Tab，数据源不变）
 * - 血缘统计（ledger 账本 + 假设生成/晋升）
 * - 特性开关（/api/learning/flags，内存级运行时开关）
 * - 血缘事件流（/api/learning/events 最新 30 条）
 * 数据源全部真实：/api/learning/overview + /api/learning/lineages + /api/learning/events + /api/learning/flags
 */
import { useEffect, useState, useCallback } from "react";
import { GitBranch, Layers, FlaskConical, Rocket, RefreshCw, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { getBackendUrl } from "@/lib/backend-config";
import { StatCard, SectionCard, RefreshButton, EmptyState } from "@/components/operations/IlcUi";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const BACKEND = getBackendUrl().replace(/\/$/, "");

function MetricCard({
  label,
  value,
  icon: Icon,
  color,
}: {
  label: string;
  value: number;
  icon: React.ComponentType<{ className?: string }>;
  color?: string;
}) {
  return (
    <div className="glass rounded-lg p-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Icon className={cn("w-3.5 h-3.5", color)} />
        {label}
      </div>
      <div className={cn("mt-1 text-xl font-bold tabular-nums", color === "text-profit" ? "grad-text-green" : color || "grad-text")}>{value}</div>
    </div>
  );
}

export default function LearningLineagePanel() {
  const [events, setEvents] = useState<any[]>([]);
  const [flags, setFlags] = useState<Record<string, boolean>>({});
  const [overview, setOverview] = useState<any>({});
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ev, fl, ov] = await Promise.allSettled([
        fetch(`${BACKEND}/api/learning/events`).then(r => r.json()),
        fetch(`${BACKEND}/api/learning/flags`).then(r => r.json()),
        fetch(`${BACKEND}/api/learning/overview`).then(r => r.json()),
      ]);
      if (ev.status === "fulfilled") setEvents(ev.value?.events ?? []);
      if (fl.status === "fulfilled") setFlags(fl.value?.flags ?? {});
      if (ov.status === "fulfilled") setOverview(ov.value ?? {});
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, [load]);

  const toggleFlag = async (key: string, val: boolean) => {
    setToggling(key);
    try {
      await fetch(`${BACKEND}/api/learning/flags`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ flag: key, value: !val }),
      });
      setFlags(f => ({ ...f, [key]: !val }));
    } finally {
      setToggling(null);
    }
  };

  const core = overview.core ?? {};
  const ledger = core.ledger ?? {};
  const hypothesis = overview.hypothesis ?? {};
  const flagEntries = Object.entries(flags);

  return (
    <div className="space-y-4">
      <SectionCard
        title="学习血缘"
        description="统一进化学习内核（/api/learning/*）血缘账本：假设生成 → 验证 → 晋升全链路事件"
        action={<RefreshButton onClick={load} loading={loading} />}
      >
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <MetricCard label="血缘事件" value={ledger.total_envelopes ?? 0} icon={GitBranch} />
          <MetricCard label="链路数" value={ledger.total_lineages ?? 0} icon={Layers} />
          <MetricCard label="假设生成" value={hypothesis.total_generated ?? 0} icon={FlaskConical} />
          <MetricCard label="假设晋升" value={hypothesis.total_promoted ?? 0} icon={Rocket} color="text-profit" />
        </div>
      </SectionCard>

      <SectionCard title="特性开关" description="内核特性开关（仅内存生效，重启后恢复默认/.env）">
        {flagEntries.length === 0 ? (
          <p className="text-sm text-warning py-8 text-center">开关数据加载失败或为空</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {flagEntries.map(([key, val]) => (
              <div key={key} className="flex items-center justify-between px-3 py-2 rounded bg-muted/10">
                <span className="text-xs font-mono truncate mr-2">{key}</span>
                <button
                  onClick={() => toggleFlag(key, val)}
                  disabled={toggling === key}
                  className={cn(
                    "w-10 h-5 rounded-full transition-colors relative shrink-0",
                    val ? "bg-profit/60 shadow-[0_0_8px_rgba(52,211,153,0.35)]" : "bg-muted/50",
                    toggling === key && "opacity-60"
                  )}
                >
                  {toggling === key ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin text-white absolute top-0.5 left-3" />
                  ) : (
                    <span
                      className={cn(
                        "absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform",
                        val ? "translate-x-5" : "translate-x-0.5"
                      )}
                    />
                  )}
                </button>
              </div>
            ))}
          </div>
        )}
      </SectionCard>

      <SectionCard
        title="血缘事件流"
        description="最近事件（最多 30 条，来自 /api/learning/events）"
      >
        {events.length === 0 ? (
          <EmptyState message="暂无血缘事件" />
        ) : (
          <div className="max-h-72 overflow-y-auto divide-y divide-border/20">
            {events.slice(0, 30).map((e: any, i: number) => (
              <div key={i} className="flex items-center gap-2 px-1 py-2 text-xs">
                <Badge variant="outline" className="text-[9px] font-mono shrink-0">
                  {e.stage ?? e.event_type ?? "—"}
                </Badge>
                <span className="flex-1 truncate">
                  {e.lineage_id ?? e.summary ?? JSON.stringify(e).slice(0, 80)}
                </span>
                {e.status && <Badge variant="secondary" className="text-[9px]">{e.status}</Badge>}
              </div>
            ))}
          </div>
        )}
      </SectionCard>

      <p className="text-[10px] text-muted-foreground">
        数据源：/api/learning/overview + /api/learning/flags + /api/learning/events（learning_core 统一内核，30s 轮询）
      </p>
    </div>
  );
}
