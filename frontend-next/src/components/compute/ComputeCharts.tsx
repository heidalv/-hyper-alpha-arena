"use client";

/**
 * 历史趋势区（第十章 §2 ComputeCharts）
 *  时间范围 1h/24h/7d/30d 切换（zustand timeWindow）→ GET /api/compute/metrics?window=
 *   - ResourceTrendChart CPU/内存/GPU 利用率 AreaChart（gpu_temp 叠加 Line）
 *   - TaskDurationChart 任务耗时 BarChart（点选下钻 drillTaskId → 右侧详情）
 *   - SuccessRateChart 成功率 LineChart
 * 空态策略：无历史数据显示"待积累"，不造假。
 */
import { useMemo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Activity, Clock, Gauge as GaugeIcon } from "lucide-react";
import { getMetrics, type MetricsSeries } from "@/lib/api/compute";
import { useComputeStore, type TimeWindow } from "@/lib/stores/compute";
import {
  ComputePanel,
  EmptyBox,
  LoadingBox,
  PanelError,
  RefreshButton,
  fmtNum,
  fmtTime,
  usePolling,
} from "./common";
import { cn } from "@/lib/utils";

const WINDOWS: TimeWindow[] = ["1h", "24h", "7d", "30d"];

const AXIS_TICK = { fontSize: 10, fill: "#6b7280" };

// 合并多序列到同一时间轴
function mergeByTs(
  series: Record<string, Array<{ ts: number; value: number }>>,
  keys: string[]
): Array<Record<string, number | string>> {
  const map = new Map<number, Record<string, number | string>>();
  for (const key of keys) {
    for (const p of series[key] ?? []) {
      if (!map.has(p.ts)) map.set(p.ts, { ts: p.ts });
      map.get(p.ts)![key] = p.value;
    }
  }
  return [...map.values()].sort((a, b) => Number(a.ts) - Number(b.ts));
}

const RESOURCE_SERIES: { key: string; label: string; color: string }[] = [
  { key: "cpu_usage_pct", label: "CPU 使用率", color: "#3b82f6" },
  { key: "mem_usage_pct", label: "内存使用率", color: "#22c55e" },
  { key: "gpu_util_pct", label: "GPU 利用率", color: "#eab308" },
];

// ───────────────────────────── 资源趋势 ─────────────────────────────

function ResourceTrendChart({ data }: { data: MetricsSeries | null }) {
  const rows = useMemo(
    () => mergeByTs(data?.resource ?? {}, RESOURCE_SERIES.map((s) => s.key)),
    [data]
  );

  return (
    <ComputePanel title="资源趋势" description="CPU / 内存 / GPU 利用率（60s 采样落库）">
      {rows.length < 2 ? (
        <EmptyBox message="待积累：60s 采样数据落库后自动绘图" />
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
            <defs>
              {RESOURCE_SERIES.map((s) => (
                <linearGradient key={s.key} id={`grad-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={s.color} stopOpacity={0.3} />
                  <stop offset="100%" stopColor={s.color} stopOpacity={0.02} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.15)" />
            <XAxis
              dataKey="ts"
              tickFormatter={(v: number) => fmtTime(v)}
              tick={AXIS_TICK}
              stroke="rgba(148,163,184,0.3)"
            />
            <YAxis tick={AXIS_TICK} stroke="rgba(148,163,184,0.3)" domain={[0, 100]} unit="%" />
            <Tooltip
              labelFormatter={(v) => fmtTime(Number(v))}
              formatter={(value, name) => [
                `${fmtNum(Number(value), 1)}%`,
                RESOURCE_SERIES.find((s) => s.key === name)?.label ?? String(name),
              ]}
              contentStyle={{ fontSize: 11 }}
            />
            {RESOURCE_SERIES.map((s) => (
              <Area
                key={s.key}
                type="monotone"
                dataKey={s.key}
                stroke={s.color}
                strokeWidth={1.5}
                fill={`url(#grad-${s.key})`}
                dot={false}
                isAnimationActive={false}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      )}
    </ComputePanel>
  );
}

// ───────────────────────────── 任务耗时（点选下钻） ─────────────────────────────

function TaskDurationChart({ data }: { data: MetricsSeries | null }) {
  const drillTaskId = useComputeStore((s) => s.drillTaskId);
  const setDrillTask = useComputeStore((s) => s.setDrillTask);

  const allRows = useMemo(() => {
    const tasks = data?.tasks ?? {};
    return Object.entries(tasks).flatMap(([type, pts]) =>
      (pts ?? []).map((p) => ({
        ts: p.ts,
        type,
        value: p.value,
        extra: p.extra,
      }))
    );
  }, [data]);

  const rows = useMemo(() => allRows.slice(0, 40), [allRows]);
  const drilled = useMemo(
    () => allRows.find((r) => String(r.ts) === String(drillTaskId)),
    [allRows, drillTaskId]
  );

  return (
    <ComputePanel title="任务耗时" description="点选柱子查看任务详情（compute_metrics 事件记录）">
      {rows.length === 0 ? (
        <EmptyBox message="待积累：任务耗时事件落库后自动绘图" />
      ) : (
        <>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.15)" />
              <XAxis
                dataKey="ts"
                tickFormatter={(v: number) => fmtTime(v)}
                tick={AXIS_TICK}
                stroke="rgba(148,163,184,0.3)"
              />
              <YAxis tick={AXIS_TICK} stroke="rgba(148,163,184,0.3)" unit="s" />
              <Tooltip
                labelFormatter={(v) => fmtTime(Number(v))}
                formatter={(value) => [`${fmtNum(Number(value), 1)}s`, "耗时"]}
                contentStyle={{ fontSize: 11 }}
              />
              <Bar dataKey="value" radius={[3, 3, 0, 0]} isAnimationActive={false} onClick={(d) => setDrillTask(d?.payload?.ts ?? null)}>
                {rows.map((r) => (
                  <Cell
                    key={String(r.ts)}
                    cursor="pointer"
                    fill={String(r.ts) === String(drillTaskId) ? "#eab308" : "#3b82f6"}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          {drilled && (
            <div className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs">
              <div className="flex items-center justify-between">
                <span className="font-medium">任务 {drilled.type}</span>
                <button className="text-primary hover:underline" onClick={() => setDrillTask(null)}>
                  关闭
                </button>
              </div>
              <p className="text-muted-foreground mt-1">
                时间 {fmtTime(Number(drilled.ts))}｜耗时 {fmtNum(drilled.value, 1)}s
              </p>
              {drilled.extra != null ? (
                <pre className="text-[10px] mt-1 overflow-x-auto text-muted-foreground">
                  {JSON.stringify(drilled.extra, null, 2)}
                </pre>
              ) : null}
            </div>
          )}
        </>
      )}
    </ComputePanel>
  );
}

// ───────────────────────────── 成功率 ─────────────────────────────

function SuccessRateChart({ data }: { data: MetricsSeries | null }) {
  const rows = useMemo(() => {
    const tasks = data?.tasks ?? {};
    // 事件 extra 带 success_rate / hit_rate 的点位
    return Object.entries(tasks).flatMap(([type, pts]) =>
      (pts ?? [])
        .map((p) => {
          const rate =
            (p.extra as Record<string, unknown> | null | undefined)?.success_rate ??
            (p.extra as Record<string, unknown> | null | undefined)?.hit_rate;
          return rate != null
            ? { ts: p.ts, type, value: Number(rate) }
            : null;
        })
        .filter((x): x is { ts: number; type: string; value: number } => x != null)
    );
  }, [data]);

  return (
    <ComputePanel title="成功率 / 命中率" description="任务事件成功率（extra.success_rate / hit_rate）">
      {rows.length < 2 ? (
        <EmptyBox message="待积累：含成功率的事件落库后自动绘图" />
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: -20 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.15)" />
            <XAxis
              dataKey="ts"
              tickFormatter={(v: number) => fmtTime(v)}
              tick={AXIS_TICK}
              stroke="rgba(148,163,184,0.3)"
            />
            <YAxis tick={AXIS_TICK} stroke="rgba(148,163,184,0.3)" domain={[0, 100]} unit="%" />
            <Tooltip
              labelFormatter={(v) => fmtTime(Number(v))}
              formatter={(value) => [`${fmtNum(Number(value), 1)}%`, "成功率"]}
              contentStyle={{ fontSize: 11 }}
            />
            <Line
              type="monotone"
              dataKey="value"
              stroke="#22c55e"
              strokeWidth={1.5}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </ComputePanel>
  );
}

// ───────────────────────────── 主组件 ─────────────────────────────

export function ComputeCharts() {
  const timeWindow = useComputeStore((s) => s.timeWindow);
  const setTimeWindow = useComputeStore((s) => s.setTimeWindow);

  const { data, loading, error, refresh } = usePolling<MetricsSeries>(
    () => getMetrics(timeWindow),
    30000,
    [timeWindow]
  );

  return (
    <ComputePanel
      title="历史趋势"
      description={`GET /api/compute/metrics?window=${timeWindow}（60s 采样落库）`}
      action={
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-0.5 rounded-md border border-border p-0.5">
            {WINDOWS.map((w) => (
              <button
                key={w}
                onClick={() => setTimeWindow(w)}
                className={cn(
                  "px-2 py-0.5 text-[11px] rounded transition-colors",
                  timeWindow === w
                    ? "bg-primary/15 text-primary font-medium"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                {w}
              </button>
            ))}
          </div>
          <RefreshButton onClick={refresh} loading={loading} />
        </div>
      }
    >
      <PanelError error={error} />
      {loading && !data ? (
        <LoadingBox text="读取历史指标…" />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
          <ResourceTrendChart data={data} />
          <TaskDurationChart data={data} />
          <SuccessRateChart data={data} />
        </div>
      )}
      <p className="text-[10px] text-muted-foreground mt-2 flex items-center gap-1">
        <GaugeIcon className="w-3 h-3" />
        采样线程：backend/services/compute/compute_metrics.py（60s）；资源键：cpu_usage_pct / mem_usage_pct / gpu_util_pct / gpu_temp_c / gpu_mem_free_mb
      </p>
    </ComputePanel>
  );
}
