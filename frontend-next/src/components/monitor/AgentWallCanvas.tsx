/**
 * Agent Wall —— 画布式多 Agent 分析墙（**已并入「Agent 监控」页**，本文件是它的画布实现）。
 *
 * 路由：`/agent-monitor?tab=wall`（旧路由 `/agent-wall` 保留为跳转桩，见 `src/app/agent-wall/page.tsx`）。
 * 设计：`docs/Agent画布模块设计_20260919.md`；排查：`docs/Agent运行排查_20260919.md`
 *
 * 用户诉求（原话）：「每个 agent 都有一个（滚屏），不是一个大的滚屏窗口；按边界分组；
 * 有大有小；画布一样可缩放、没有窗口大小限制、拖拽排列；用线链接看到关联关系。」
 *
 * 实现要点
 * - **自研轻量画布**：`transform: translate+scale` 容器 + SVG 边层（前端无任何画布/图库依赖）。
 * - **每节点一个独立滚屏**：各自的 ring buffer（≤200 行）、自动跟随、悬停暂停、级别过滤。
 * - **单页只有两个请求**：`/state`（3s）+ `/tail`（2s，一次覆盖全部订阅节点）
 *   —— 后端单进程 GIL 长期贴 1 核，禁止每节点一个请求（见 gil_watch 实测）。
 * - **状态口径来自后端**（`job_registry` 的 stale 判定），前端不另算：
 *   ok / stale / dead / never / disabled / unknown。
 * - 布局（位置/尺寸/缩放/平移）存 localStorage，刷新不丢。
 *
 * [2026-10-01 合并] 原 `/agent-wall` 独立页与 `/agent-monitor` 合并为一个入口「Agent 监控」
 *   （侧栏归入「市场 & 分析」组），本组件即合并后的画布 Tab；工具栏由本组件自带，
 *   页头（PageHeader）由宿主页面统一渲染，避免同一页出现两个页头。
 */
"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { fetchPublic } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import {
  LayoutGrid, Filter, Pause, Play, AlertTriangle,
  CircleDot, Activity, ChevronRight,
} from "lucide-react";

// ─────────────────────────── 类型 ───────────────────────────

type Status = "ok" | "stale" | "dead" | "never" | "disabled" | "unknown";
type SizeKey = "XL" | "L" | "M" | "S";

interface WallNode {
  id: string;
  group: string;
  size: SizeKey;
  label: string;
  role: string;
  cadence_label?: string;
  status: Status;
  observe_mode?: string | null;
  status_detail: { reason?: string; run_count?: number | null; last_success_ms?: number | null;
                   declared_interval_s?: number | null; cadence?: string };
  source: { kind: string; path?: string; agent?: string; reason?: string };
}
interface WallEdge {
  from: string; to: string; kind: string; label: string;
  ineffective?: boolean;
  health: { state: "ok" | "stale" | "broken" | "ineffective"; reason?: string };
}
interface WallState {
  generated_at_ms: number;
  groups: { group: string; total: number; [k: string]: number | string }[];
  nodes: WallNode[];
  edges: WallEdge[];
  qaa_enabled?: boolean;
  retired_layers?: { id: string; since: string; doc: string; note: string }[];
  warnings?: string[];
  note: string;
}
interface AuditFinding {
  id: string; severity: "high" | "medium" | "low" | "info";
  what: string; evidence: string; kind: string; measured_at?: string;
  nodes?: string[];
}
interface TailLine { ts: string; text: string; raw?: string }

// ─────────────────────────── 常量 ───────────────────────────

/** 尺寸四档（"有大有小"）；节点可拖拽右下角自由改尺寸（"没有窗口的大小限制"）。 */
const SIZE_PX: Record<SizeKey, { w: number; h: number }> = {
  XL: { w: 520, h: 430 }, L: { w: 430, h: 340 }, M: { w: 330, h: 250 }, S: { w: 250, h: 170 },
};
const STATUS_STYLE: Record<Status, { dot: string; text: string; ring: string; label: string }> = {
  ok:       { dot: "bg-emerald-400", text: "text-emerald-300", ring: "border-emerald-500/30", label: "正常" },
  stale:    { dot: "bg-amber-400",   text: "text-amber-300",   ring: "border-amber-500/40",   label: "陈旧" },
  dead:     { dot: "bg-rose-500",    text: "text-rose-300",    ring: "border-rose-500/40",    label: "断链" },
  never:    { dot: "bg-orange-500",  text: "text-orange-300",  ring: "border-orange-500/40",  label: "从未运行" },
  disabled: { dot: "bg-slate-500",   text: "text-slate-400",   ring: "border-slate-600/40",   label: "已停用" },
  unknown:  { dot: "bg-slate-600",   text: "text-slate-500",   ring: "border-slate-700/40",   label: "未知" },
};
const EDGE_STYLE: Record<string, { stroke: string; dash?: string; label: string }> = {
  ok:           { stroke: "#22d3ee", label: "正常" },
  stale:        { stroke: "#fbbf24", label: "陈旧" },
  broken:       { stroke: "#f43f5e", dash: "6 4", label: "断链" },
  ineffective:  { stroke: "#64748b", dash: "2 4", label: "未生效(observe/开关)" },
};
const GROUP_META: Record<string, { title: string; hint: string }> = {
  G0: { title: "数据底座", hint: "行情/采集/因子/选币" },
  G1: { title: "主脑（LLM 论题）", hint: "主脑内部环节 + 六分析师信号 + 辩论 + 风控官" },
  G2: { title: "观察型 Agent 群", hint: "anomaly/signal_review/event_impact/execution_qa…" },
  // G3（QAA 卡片族）已于 2026-09-19 正式退役，画布不再列出；见 docs/ADR_QAA退役_20260919.md
  G4: { title: "车道与执行", hint: "主线执行/方向审计/车道 Agent" },
  // [轮139 2026-09-20] 新增因子区（用户指令「画布现在开始做 因子这个大模块」）
  G5: { title: "因子区", hint: "计算/缓存 · 挖掘演化/退役 · 暴露快照 · 三条路线（AB 已停用，只产证据）" },
};
const LS_KEY = "arena_agent_wall_layout_v1";
/** 订阅上限：只给"视口内最活跃"的若干节点拉流，避免一次拉爆（后端 tail 也限 12）。 */
const MAX_SUBSCRIBE = 8;
const MAX_LINES = 200;
/** 画布可视高度：宿主页（页头 + Tab 条 + 工具栏）之外的剩余视口。 */
const CANVAS_H = "calc(100vh - 250px)";

interface Layout { x: number; y: number; w?: number; h?: number }
type LayoutMap = Record<string, Layout>;

/** 默认自动排布：G0→G4 五列，组内纵向堆叠（按尺寸累加高度）。 */
function autoLayout(nodes: WallNode[]): LayoutMap {
  const out: LayoutMap = {};
  const groups = ["G0", "G1", "G2", "G4", "G5"];   // G3(QAA) 已退役，见 ADR；[轮139] G5=因子区
  let x = 40;
  for (const g of groups) {
    const inGroup = nodes.filter((n) => n.group === g);
    if (!inGroup.length) continue;
    let y = 90;
    let colW = 0;
    for (const n of inGroup) {
      const px = SIZE_PX[n.size] ?? SIZE_PX.M;
      out[n.id] = { x, y, w: px.w, h: px.h };
      y += px.h + 18;
      colW = Math.max(colW, px.w);
    }
    x += colW + 60;
  }
  return out;
}

function loadLayout(): LayoutMap {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(window.localStorage.getItem(LS_KEY) || "{}") as LayoutMap;
  } catch {
    return {};
  }
}

/** 相对时间：用**服务端给的 generated_at_ms** 做基准，避免 render 期调 Date.now()（lint/纯度）。 */
function agoLabel(ms: number | null | undefined, nowMs: number): string {
  if (!ms || !nowMs) return "—";
  const s = Math.max(0, Math.round((nowMs - ms) / 1000));
  if (s < 60) return `${s}s 前`;
  if (s < 3600) return `${Math.round(s / 60)}m 前`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h 前`;
  return `${(s / 86400).toFixed(1)}d 前`;
}

// ─────────────────────────── 数据轮询 ───────────────────────────

function useWallState(): { state: WallState | null; error: string | null } {
  const [state, setState] = useState<WallState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const d = await fetchPublic<WallState>("/agent-wall/state");
      setState(d);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  usePolling(load, 3000);
  return { state, error };
}

function useWallTail(ids: string[]): Record<string, TailLine[]> {
  const [buf, setBuf] = useState<Record<string, TailLine[]>>({});
  const idsRef = useRef<string[]>([]);
  // ref 只在 effect 里写（render 期访问 ref 会触发 react-hooks/refs）
  useEffect(() => {
    idsRef.current = ids;
  }, [ids]);
  const load = useCallback(async () => {
    const want = idsRef.current.slice(0, MAX_SUBSCRIBE);
    if (!want.length) return;
    try {
      const d = await fetchPublic<{ lines: Record<string, TailLine[]> }>(
        `/agent-wall/tail?nodes=${encodeURIComponent(want.join(","))}`,
      );
      const incoming = d?.lines || {};
      if (!Object.keys(incoming).some((k) => (incoming[k] || []).length)) return;
      setBuf((prev) => {
        const next: Record<string, TailLine[]> = { ...prev };
        for (const [k, lines] of Object.entries(incoming)) {
          if (!lines?.length) continue;
          const merged = [...(prev[k] || []), ...lines];
          next[k] = merged.length > MAX_LINES ? merged.slice(-MAX_LINES) : merged;
        }
        return next;
      });
    } catch {
      /* 轮询失败保留旧值 */
    }
  }, []);
  usePolling(load, 2000);
  return buf;
}

// ─────────────────────────── 画布（合并后作为「Agent 监控」的一个 Tab） ───────────────────────────

export function AgentWallCanvas() {
  const { state, error } = useWallState();
  const [layout, setLayout] = useState<LayoutMap>({});
  const [view, setView] = useState({ x: 0, y: 0, k: 0.8 });
  const [onlyBroken, setOnlyBroken] = useState(false);
  const [paused, setPaused] = useState(false);
  const [showAudit, setShowAudit] = useState(false);
  const [focused, setFocused] = useState<string | null>(null);
  const [audit, setAudit] = useState<{ findings: AuditFinding[]; counts: Record<string, number> } | null>(null);
  const dragRef = useRef<{ id: string | null; sx: number; sy: number; ox: number; oy: number;
                            resize?: { w: number; h: number } } | null>(null);
  const panRef = useRef<{ sx: number; sy: number; ox: number; oy: number } | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const hydrated = useRef(false);

  // ── 首次拿到节点后：合并 localStorage 布局（一次） ──
  useEffect(() => {
    if (!state || hydrated.current) return;
    hydrated.current = true;
    const saved = loadLayout();
    const base = autoLayout(state.nodes);
    const merged: LayoutMap = { ...base };
    for (const [k, v] of Object.entries(saved)) if (merged[k]) merged[k] = { ...merged[k], ...v };
    setLayout(merged);
  }, [state]);

  // ── 布局持久化 ──
  useEffect(() => {
    if (!hydrated.current || typeof window === "undefined") return;
    try {
      window.localStorage.setItem(LS_KEY, JSON.stringify(layout));
    } catch { /* ignore */ }
  }, [layout]);

  // ── 审计面板（打开时拉一次） ──
  useEffect(() => {
    if (!showAudit || audit) return;
    let alive = true;
    void (async () => {
      try {
        const d = await fetchPublic<{ findings: AuditFinding[]; counts: Record<string, number> }>("/agent-wall/audit");
        if (alive) setAudit(d);
      } catch { /* ignore */ }
    })();
    return () => { alive = false; };
  }, [showAudit, audit]);

  const nodes = useMemo(() => state?.nodes ?? [], [state]);
  const edges = useMemo(() => state?.edges ?? [], [state]);
  const nowMs = state?.generated_at_ms ?? 0;

  /** 订阅（拉流）的节点：视口内 + 未过滤掉，按"重要度/活跃度"取前 N */
  const visibleIds = useMemo(() => {
    const list = nodes.filter((n) => !onlyBroken || ["dead", "never", "stale"].includes(n.status));
    const rank: Record<string, number> = { XL: 0, L: 1, M: 2, S: 3 };
    return [...list].sort((a, b) => (rank[a.size] ?? 9) - (rank[b.size] ?? 9)).map((n) => n.id);
  }, [nodes, onlyBroken]);
  const tail = useWallTail(paused ? [] : visibleIds);

  const brokenCount = edges.filter((e) => e.health.state === "broken").length;

  // ── 画布交互 ──
  const onWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const rect = wrapRef.current?.getBoundingClientRect();
    const mx = e.clientX - (rect?.left ?? 0);
    const my = e.clientY - (rect?.top ?? 0);
    setView((v) => {
      const k = Math.min(2.5, Math.max(0.25, v.k * (e.deltaY < 0 ? 1.1 : 0.9)));
      const ratio = k / v.k;
      return { k, x: mx - (mx - v.x) * ratio, y: my - (my - v.y) * ratio };
    });
  }, []);

  const onBgDown = useCallback((e: React.PointerEvent) => {
    if (e.button !== 0) return;
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    panRef.current = { sx: e.clientX, sy: e.clientY, ox: view.x, oy: view.y };
  }, [view.x, view.y]);

  const onNodeDown = useCallback((e: React.PointerEvent, id: string, resize?: { w: number; h: number }) => {
    e.stopPropagation();
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    const l = layout[id];
    if (!l) return;
    dragRef.current = { id, sx: e.clientX, sy: e.clientY, ox: l.x, oy: l.y, resize };
  }, [layout]);

  const onMove = useCallback((e: React.PointerEvent) => {
    if (panRef.current) {
      const p = panRef.current;
      setView((v) => ({ ...v, x: p.ox + (e.clientX - p.sx), y: p.oy + (e.clientY - p.sy) }));
      return;
    }
    const d = dragRef.current;
    if (!d?.id) return;
    const dx = (e.clientX - d.sx) / view.k;
    const dy = (e.clientY - d.sy) / view.k;
    setLayout((prev) => {
      const cur = prev[d.id!];
      if (!cur) return prev;
      const bump = d.resize
        ? { w: Math.max(180, d.resize.w + dx), h: Math.max(120, d.resize.h + dy) }
        : {};
      return { ...prev, [d.id!]: { ...cur, x: Math.round(d.ox + dx), y: Math.round(d.oy + dy), ...bump } };
    });
  }, [view.k]);

  const onUp = useCallback(() => { panRef.current = null; dragRef.current = null; }, []);

  const resetLayout = useCallback(() => {
    setLayout(autoLayout(nodes));
    setView({ x: 0, y: 0, k: 0.8 });
  }, [nodes]);

  const focusOn = useCallback((id: string) => {
    const l = layout[id];
    if (!l) return;
    setView((v) => ({ ...v, k: 1, x: -l.x + 60, y: -l.y + 60 }));
    setFocused(id);
  }, [layout]);

  const groupBoxes = useMemo(() => {
    const byGroup: Record<string, { x1: number; y1: number; x2: number; y2: number }> = {};
    for (const n of nodes) {
      const l = layout[n.id];
      if (!l) continue;
      const w = l.w ?? SIZE_PX[n.size].w;
      const h = l.h ?? SIZE_PX[n.size].h;
      const b = byGroup[n.group] ?? { x1: 1e9, y1: 1e9, x2: -1e9, y2: -1e9 };
      b.x1 = Math.min(b.x1, l.x); b.y1 = Math.min(b.y1, l.y);
      b.x2 = Math.max(b.x2, l.x + w); b.y2 = Math.max(b.y2, l.y + h);
      byGroup[n.group] = b;
    }
    return Object.entries(byGroup).map(([g, b]) => ({ group: g, ...b }));
  }, [nodes, layout]);

  const bounds = useMemo(() => {
    let w = 1600, h = 1100;
    for (const n of nodes) {
      const l = layout[n.id];
      if (!l) continue;
      w = Math.max(w, l.x + (l.w ?? SIZE_PX[n.size].w) + 120);
      h = Math.max(h, l.y + (l.h ?? SIZE_PX[n.size].h) + 120);
    }
    return { w, h };
  }, [nodes, layout]);

  return (
    <div className="space-y-3">
      {/* ── 画布工具栏（合并前是独立页的页头操作区；现在挂在画布上方） ── */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <Button variant="outline" size="sm" onClick={resetLayout}>
          <LayoutGrid className="w-3.5 h-3.5 mr-1" />重置布局
        </Button>
        <Button variant={onlyBroken ? "default" : "outline"} size="sm" onClick={() => setOnlyBroken((v) => !v)}>
          <Filter className="w-3.5 h-3.5 mr-1" />只看异常({brokenCount + nodes.filter((n) => n.status !== "ok").length})
        </Button>
        <Button variant="outline" size="sm" onClick={() => setPaused((v) => !v)}>
          {paused ? <Play className="w-3.5 h-3.5 mr-1" /> : <Pause className="w-3.5 h-3.5 mr-1" />}
          {paused ? "继续" : "暂停全部"}
        </Button>
        <Button variant={showAudit ? "default" : "outline"} size="sm" onClick={() => setShowAudit((v) => !v)}>
          <AlertTriangle className="w-3.5 h-3.5 mr-1" />审计
        </Button>
        <Badge variant="secondary" className="text-xs">缩放 {view.k.toFixed(2)}×</Badge>
        {(state?.retired_layers ?? []).map((r) => (
          <Badge key={r.id} variant="secondary" className="text-xs text-muted-foreground"
            title={`${r.note}（自 ${r.since}，依据 ${r.doc}）`}>
            已退役：{r.id}
          </Badge>
        ))}
        <span className="ml-auto text-[11px] text-muted-foreground">
          每节点独立滚屏 · 拖拽改位置/尺寸 · 滚轮缩放 · 画布 3s / 分析流 2s
        </span>
      </div>

      {error && (
        <Card className="p-3 border-loss/40 text-xs text-loss">
          画布数据获取失败：{error}（后端需含 `/api/agent-wall/*`，见 docs/Agent画布模块设计_20260919.md）
        </Card>
      )}
      {(state?.warnings ?? []).map((w) => (
        <Card key={w} className="p-2 border-warning/40 text-xs text-warning">降级告警：{w}</Card>
      ))}

      <div className="flex gap-3 items-start">
        {/* ── 画布 ── */}
        <Card
          ref={wrapRef as never}
          className="relative flex-1 overflow-hidden border-border cursor-grab active:cursor-grabbing"
          style={{ height: CANVAS_H, minHeight: 520 }}
          onWheel={onWheel}
          onPointerDown={onBgDown}
          onPointerMove={onMove}
          onPointerUp={onUp}
          onPointerLeave={onUp}
        >
          <div
            className="absolute origin-top-left"
            style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})`, width: bounds.w, height: bounds.h }}
          >
            {/* 分组框 */}
            {groupBoxes.map((b) => (
              <div
                key={b.group}
                className="absolute rounded-xl border border-white/5 bg-white/[0.015]"
                style={{ left: b.x1 - 16, top: b.y1 - 46, width: b.x2 - b.x1 + 32, height: b.y2 - b.y1 + 62 }}
              >
                <div className="px-3 pt-1.5 text-[11px] tracking-wide text-slate-400">
                  <span className="font-semibold text-slate-300">{GROUP_META[b.group]?.title ?? b.group}</span>
                  <span className="ml-2 opacity-70">{GROUP_META[b.group]?.hint}</span>
                  <span className="ml-2 opacity-70">
                    {state?.groups.find((g) => g.group === b.group)?.total ?? 0} 个节点
                  </span>
                </div>
              </div>
            ))}

            {/* 边层 */}
            <svg className="absolute inset-0 pointer-events-none" width={bounds.w} height={bounds.h}>
              {edges.map((e, i) => {
                const a = layout[e.from], b = layout[e.to];
                if (!a || !b) return null;
                const na = nodes.find((n) => n.id === e.from);
                const nb = nodes.find((n) => n.id === e.to);
                if (!na || !nb) return null;
                if (onlyBroken && e.health.state === "ok") return null;
                const aw = a.w ?? SIZE_PX[na.size].w, ah = a.h ?? SIZE_PX[na.size].h;
                const x1 = a.x + aw, y1 = a.y + ah / 2;
                const x2 = b.x, y2 = b.y + (b.h ?? SIZE_PX[nb.size].h) / 2;
                const st = EDGE_STYLE[e.health.state] ?? EDGE_STYLE.ok;
                const mx = (x1 + x2) / 2;
                return (
                  <g key={`${e.from}-${e.to}-${i}`}>
                    <path
                      d={`M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`}
                      fill="none" stroke={st.stroke} strokeWidth={e.health.state === "broken" ? 2 : 1.4}
                      strokeDasharray={st.dash} opacity={0.9}
                    />
                    <circle cx={x2} cy={y2} r={2.5} fill={st.stroke} />
                    <text x={mx} y={(y1 + y2) / 2 - 4} fill={st.stroke} fontSize={10} textAnchor="middle" opacity={0.85}>
                      {e.label}
                    </text>
                    {e.health.state === "broken" && (
                      <text x={mx} y={(y1 + y2) / 2 + 12} fill="#f43f5e" fontSize={11} textAnchor="middle">❗{e.health.reason}</text>
                    )}
                  </g>
                );
              })}
            </svg>

            {/* 节点 */}
            {nodes.map((n) => {
              const l = layout[n.id];
              if (!l) return null;
              if (onlyBroken && n.status === "ok") return null;
              return (
                <NodeCard
                  key={n.id}
                  node={n}
                  layout={l}
                  lines={tail[n.id] || []}
                  nowMs={nowMs}
                  paused={paused}
                  focused={focused === n.id}
                  onDown={(e) => onNodeDown(e, n.id)}
                  onResize={(e) => onNodeDown(e, n.id, { w: l.w ?? SIZE_PX[n.size].w, h: l.h ?? SIZE_PX[n.size].h })}
                  onFocus={() => focusOn(n.id)}
                />
              );
            })}
          </div>
        </Card>

        {/* ── 审计侧栏 ── */}
        {showAudit && (
          <Card className="w-[420px] shrink-0 p-3" style={{ maxHeight: CANVAS_H, overflowY: "auto" }}>
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium flex items-center gap-1.5">
                <AlertTriangle className="w-4 h-4 text-warning" />断链与逻辑错误审计
              </span>
              <Badge variant="secondary" className="text-xs">
                高 {audit?.counts?.high ?? 0} · 中 {audit?.counts?.medium ?? 0}
              </Badge>
            </div>
            <div className="text-[11px] text-muted-foreground mb-2">
              kind=live 现场重算；kind=static_verified 为 2026-09-19 排查实测（带证据）；kind=fixed 为本轮已修。
            </div>
            {!audit && <div className="text-xs text-muted-foreground">加载中…</div>}
            <div className="space-y-2">
              {(audit?.findings ?? []).map((f) => (
                <div key={f.id} className={cn(
                  "rounded border p-2 text-xs",
                  f.severity === "high" ? "border-loss/40 bg-loss/5"
                    : f.severity === "medium" ? "border-warning/40 bg-warning/5"
                    : "border-border bg-muted/10",
                )}>
                  <div className="flex items-start gap-1.5">
                    <CircleDot className={cn("w-3.5 h-3.5 mt-0.5 shrink-0",
                      f.severity === "high" ? "text-loss" : f.severity === "medium" ? "text-warning" : "text-muted-foreground")} />
                    <div className="min-w-0">
                      <div className="font-medium">{f.what}</div>
                      <div className="text-muted-foreground mt-0.5 break-words">{f.evidence}</div>
                      <div className="text-[10px] text-muted-foreground/70 mt-0.5">
                        {f.kind}{f.measured_at ? ` · ${f.measured_at}` : ""}
                        {f.nodes?.length ? ` · 关联 ${f.nodes.slice(0, 3).join(", ")}${f.nodes.length > 3 ? "…" : ""}` : ""}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────── 节点卡（每个 agent 一个独立滚屏） ───────────────────────────

function NodeCard({
  node, layout, lines, nowMs, paused, focused, onDown, onResize, onFocus,
}: {
  node: WallNode;
  layout: Layout;
  lines: TailLine[];
  nowMs: number;
  paused: boolean;
  focused: boolean;
  onDown: (e: React.PointerEvent) => void;
  onResize: (e: React.PointerEvent) => void;
  onFocus: () => void;
}) {
  const [hover, setHover] = useState(false);
  const [level, setLevel] = useState<"all" | "warn">("all");
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const px = SIZE_PX[node.size] ?? SIZE_PX.M;
  const w = layout.w ?? px.w;
  const h = layout.h ?? px.h;
  const st = STATUS_STYLE[node.status] ?? STATUS_STYLE.unknown;

  const shown = useMemo(
    () => (level === "all" ? lines : lines.filter((l) => /WARN|ERROR|失败|异常/i.test(l.text))),
    [lines, level],
  );

  // 自动跟随（悬停暂停；暂停按钮在父级）
  useEffect(() => {
    const el = bodyRef.current;
    if (!el || hover || paused) return;
    el.scrollTop = el.scrollHeight;
  }, [shown.length, hover, paused]);

  return (
    <div
      className={cn(
        "absolute rounded-lg border bg-[#0b1220]/95 shadow-[0_6px_20px_rgba(0,0,0,0.35)] flex flex-col",
        st.ring, focused && "ring-2 ring-cyan-400/60",
      )}
      style={{ left: layout.x, top: layout.y, width: w, height: h }}
      onPointerDown={onDown}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      {/* 头部（拖拽把手） */}
      <div className="flex items-start gap-1.5 px-2 py-1.5 border-b border-white/5 cursor-move select-none">
        <span className={cn("w-2 h-2 rounded-full mt-1 shrink-0", st.dot)} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="text-xs font-semibold truncate">{node.label}</span>
            <Badge variant="secondary" className="text-[9px] px-1 py-0">{node.group}</Badge>
            <span className={cn("text-[10px]", st.text)}>{st.label}</span>
            {node.observe_mode === "observe" && (
              <span className="text-[9px] px-1 rounded bg-slate-500/20 text-slate-300" title="observe 模式：建议仅记录不执行">
                observe
              </span>
            )}
          </div>
          <div className="text-[10px] text-muted-foreground truncate" title={node.role}>{node.role}</div>
          <div className="text-[10px] text-muted-foreground/80 flex items-center gap-2 mt-0.5">
            <span>{node.status_detail?.cadence || node.cadence_label || "—"}</span>
            <span>最近 {agoLabel(node.status_detail?.last_success_ms, nowMs)}</span>
            {node.status_detail?.run_count != null && <span>#{node.status_detail.run_count}</span>}
            <button className="ml-auto text-[10px] text-cyan-300 hover:underline" onClick={(e) => { e.stopPropagation(); onFocus(); }}>
              <ChevronRight className="w-3 h-3 inline" />聚焦
            </button>
          </div>
        </div>
      </div>

      {/* 状态理由（断链/停用必显示依据） */}
      {node.status !== "ok" && node.status_detail?.reason && (
        <div className={cn("px-2 py-1 text-[10px] border-b border-white/5", st.text)}>
          {node.status_detail.reason}
        </div>
      )}

      {/* 独立滚屏 */}
      <div className="flex items-center gap-1 px-2 py-1 border-b border-white/5">
        {(["all", "warn"] as const).map((k) => (
          <button key={k} onClick={(e) => { e.stopPropagation(); setLevel(k); }}
            className={cn("px-1.5 py-0.5 rounded text-[10px] cursor-pointer",
              level === k ? "bg-cyan-400/15 text-cyan-300" : "text-muted-foreground hover:bg-white/5")}>
            {k === "all" ? "全部" : "告警"}
          </button>
        ))}
        <span className="ml-auto text-[10px] text-muted-foreground/70">
          {shown.length} 行{hover ? " · 悬停已暂停跟随" : ""}
        </span>
      </div>
      <div ref={bodyRef} className="flex-1 overflow-y-auto px-2 py-1 font-mono text-[10px] leading-[1.45] text-slate-300/90">
        {shown.length === 0 ? (
          <div className="text-muted-foreground/60 py-2">
            {node.source?.kind === "none"
              ? `无分析流：${node.source?.reason ?? "结构性停用"}`
              : "暂无新行（等待该 agent 产出，或在下方拖动子进程/任务以触发）"}
          </div>
        ) : (
          shown.map((l, i) => (
            <div key={i} className="whitespace-pre-wrap break-words" title={l.raw ?? l.text}>
              {l.ts && <span className="text-slate-500 mr-1">{l.ts.slice(11)}</span>}
              {l.text}
            </div>
          ))
        )}
      </div>

      {/* 右下角自由缩放（"没有窗口的大小限制"） */}
      <div
        className="absolute right-0 bottom-0 w-3.5 h-3.5 cursor-nwse-resize"
        onPointerDown={onResize}
        title="拖拽改变面板大小"
        style={{ background: "linear-gradient(135deg, transparent 50%, rgba(148,163,184,0.5) 50%)" }}
      />
      <div className="absolute left-2 bottom-0.5 text-[9px] text-muted-foreground/50 flex items-center gap-1">
        <Activity className="w-2.5 h-2.5" />{node.id}
      </div>
    </div>
  );
}
