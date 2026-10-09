"use client";

import { useState, useEffect, useRef, useCallback, Fragment, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Radar, Activity, BarChart3, Radio, Clock, Terminal,
  RefreshCw, Loader2, Cpu, Zap, TrendingUp, Boxes,
  CheckCircle2, XCircle, AlertTriangle, Pause, Play,
  Server, Brain, Gauge, Layers, Wallet, ArrowUpRight, ArrowDownRight,
  Network,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getBackendUrl } from "@/lib/backend-config";
import { apiRequest } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import { PageHeader } from "@/components/layout/PageHeader";
import { AgentWallCanvas } from "@/components/monitor/AgentWallCanvas";
import {
  LineChart as RLineChart, Line as RLine, BarChart as RBarChart, Bar as RBar,
  PieChart as RPieChart, Pie as RPie, Cell as RCell,
  XAxis, YAxis, Tooltip as RTooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";

// 后端基地址(开发=localhost:8000, 生产=配置的域名)
const BACKEND = getBackendUrl().replace(/\/$/, "");

type Tab = "wall" | "overview" | "stats" | "decisions" | "scheduler" | "logs";
const TAB_KEYS: Tab[] = ["wall", "overview", "stats", "decisions", "scheduler", "logs"];

// ═══════════════════════════════════════════════════════════════════
// 主页面
//
// [2026-10-01 合并] 原「Agent Wall」独立页（`/agent-wall`）并入本页，成为第一个 Tab
// 「分析墙（画布）」；侧栏入口只保留一个「Agent 监控」，并归入「市场 & 分析」组。
// 旧路由 `/agent-wall` 保留为跳转桩 → `/agent-monitor?tab=wall`（外部书签/文档不失效）。
// 因此本页首次进入默认落在画布 Tab；`?tab=overview|stats|decisions|scheduler|logs`
// 仍可直达原来的监视视图（深链由 useSearchParams 读取）。
// ═══════════════════════════════════════════════════════════════════

export default function AgentMonitorPage() {
  return (
    <Suspense fallback={<LoadingSpinner />}>
      <AgentMonitorPageInner />
    </Suspense>
  );
}

function AgentMonitorPageInner() {
  const searchParams = useSearchParams();
  // 深链：`?tab=` 决定首屏 Tab（旧 /agent-wall 跳转桩带的就是 ?tab=wall），非法值回落画布。
  // 用惰性初始化而不是 useEffect+setState：后者属于 react-hooks/set-state-in-effect，
  // 且本页是纯客户端渲染（Suspense 边界内），首帧就能读到真实 query。
  const [tab, setTab] = useState<Tab>(() => {
    const t = (searchParams.get("tab") || "").toLowerCase();
    return (TAB_KEYS as string[]).includes(t) ? (t as Tab) : "wall";
  });

  // 账户/会话两级选择（全局，供所有 Tab 跟随）
  const [selectedAccountId, setSelectedAccountId] = useState<number | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const sessionsPoll = usePoll<any>(`${BACKEND}/api/full-auto/sessions`, 15000);

  const sessionsList: any[] = sessionsPoll.data ?? [];

  // 默认自动选中：首次加载后未选择时，取第一个 running/defensive 会话（保持单账户默认行为）
  useEffect(() => {
    if (selectedSessionId || selectedAccountId) return;
    const firstActive = sessionsList.find(
      (s: any) => s.status === "running" || s.status === "defensive",
    );
    if (firstActive) {
      setSelectedAccountId(firstActive.account_id ?? null);
      setSelectedSessionId(firstActive.session_id ?? null);
    } else if (sessionsList.length > 0) {
      const latest = sessionsList[0];
      setSelectedAccountId(latest.account_id ?? null);
      setSelectedSessionId(latest.session_id ?? null);
    }
  }, [sessionsList, selectedSessionId, selectedAccountId]);

  const handleAccountChange = (accountId: number | null) => {
    setSelectedAccountId(accountId);
    if (accountId == null) {
      setSelectedSessionId(null);
      return;
    }
    const accSessions = sessionsList.filter((s: any) => s.account_id === accountId);
    const firstActive = accSessions.find(
      (s: any) => s.status === "running" || s.status === "defensive",
    );
    setSelectedSessionId((firstActive ?? accSessions[0])?.session_id ?? null);
  };

  const tabs: { key: Tab; label: string; icon: any }[] = [
    // [2026-10-01] 合并自原「Agent Wall」页：画布式多 Agent 分析墙（每节点独立滚屏 + 链路）
    { key: "wall", label: "分析墙（画布）", icon: Network },
    { key: "overview", label: "运行总览", icon: Activity },
    { key: "stats", label: "执行统计", icon: BarChart3 },
    { key: "decisions", label: "决策流", icon: Radio },
    { key: "scheduler", label: "调度监控", icon: Clock },
    { key: "logs", label: "实时日志", icon: Terminal },
  ];

  /** 切 Tab 时把 `?tab=` 同步进地址栏（刷新/复制链接保持同一视图）。
   *  用 history.replaceState 而非 router.replace：本页全客户端，不需要重新拉 RSC 载荷，
   *  静态导出（Electron out/）下也最稳。 */
  const switchTab = useCallback((k: Tab) => {
    setTab(k);
    if (typeof window === "undefined") return;
    const url = `${window.location.pathname}?tab=${k}`;
    if (`${window.location.pathname}${window.location.search}` !== url) {
      window.history.replaceState(null, "", url);
    }
  }, []);

  return (
    <div className="p-4 space-y-4">
      <PageHeader
        icon={<Radar className="w-4 h-4" />}
        title="Agent 监控"
        subtitle="画布式多 Agent 分析墙（每节点独立滚屏 · 有向连线看链路健康）· LLM 论题主脑 · 日内波段(=中线) / 长线趋势 两车道 · 哨兵盯盘"
        refreshHint={tab === "wall" ? "画布 3s 轮询" : "会话状态 15s 轮询"}
        breadcrumb={[{ label: "市场 & 分析" }, { label: "Agent 监控" }]}
        actions={
          // 账户/会话选择器只对监视类 Tab 有意义（画布 Tab 是全局的，不跟账户走）
          tab === "wall" ? undefined : (
            <AccountSessionSelector
              sessions={sessionsList}
              selectedAccountId={selectedAccountId}
              selectedSessionId={selectedSessionId}
              onAccountChange={handleAccountChange}
              onSessionChange={setSelectedSessionId}
            />
          )
        }
      />

      {/* Tab 导航（Aurora 渐变激活态） */}
      <div className="flex items-center gap-1 flex-wrap border-b border-border/50 pb-2">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => switchTab(t.key)}
            className={cn(
              "relative flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md transition-colors cursor-pointer",
              tab === t.key
                ? "bg-gradient-to-r from-cyan-400/15 to-violet-500/15 text-cyan-300 font-medium after:absolute after:left-2 after:right-2 after:-bottom-[9px] after:h-0.5 after:rounded-full after:bg-gradient-to-r after:from-cyan-400 after:to-violet-500 after:shadow-[0_0_8px_rgba(34,211,238,0.6)]"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
            )}
          >
            <t.icon className="w-3.5 h-3.5" />
            {t.label}
          </button>
        ))}
      </div>

      {tab === "wall" && <AgentWallCanvas />}
      {tab === "overview" && <OverviewTab sessionsData={sessionsList} selectedSessionId={selectedSessionId} />}
      {tab === "stats" && <StatsTab selectedAccountId={selectedAccountId} />}
      {tab === "decisions" && <DecisionsTab selectedAccountId={selectedAccountId} />}
      {tab === "scheduler" && <SchedulerTab selectedSessionId={selectedSessionId} />}
      {tab === "logs" && <LogsTab />}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// 账户 / 会话两级选择器
// ═══════════════════════════════════════════════════════════════════

function AccountSessionSelector({
  sessions,
  selectedAccountId,
  selectedSessionId,
  onAccountChange,
  onSessionChange,
}: {
  sessions: any[];
  selectedAccountId: number | null;
  selectedSessionId: string | null;
  onAccountChange: (accountId: number | null) => void;
  onSessionChange: (sessionId: string | null) => void;
}) {
  // 按 account_id 聚合账户；有活跃会话的排前
  const accountMap = new Map<number, { account: any; sessions: any[]; hasActive: boolean }>();
  for (const s of sessions) {
    const key = s.account_id ?? 0;
    if (!accountMap.has(key)) {
      accountMap.set(key, { account: s, sessions: [], hasActive: false });
    }
    const entry = accountMap.get(key)!;
    entry.sessions.push(s);
    if (s.status === "running" || s.status === "defensive") entry.hasActive = true;
  }
  const accounts = Array.from(accountMap.entries()).sort((a, b) => {
    if (a[1].hasActive !== b[1].hasActive) return a[1].hasActive ? -1 : 1;
    return (b[0] ?? 0) - (a[0] ?? 0);
  });

  const curSessions = accountMap.get(selectedAccountId ?? 0)?.sessions ?? [];
  const sortedSessions = [...curSessions].sort((a, b) => {
    const rank = (s: any) => (s.status === "running" || s.status === "defensive" ? 0 : 1);
    if (rank(a) !== rank(b)) return rank(a) - rank(b);
    return new Date(b.started_at ?? b.created_at ?? 0).getTime() - new Date(a.started_at ?? a.created_at ?? 0).getTime();
  });

  const statusLabel: Record<string, string> = {
    running: "运行中", defensive: "防守", paused: "已暂停", stopped: "已停止",
  };

  return (
    <div className="flex items-center gap-2">
      <select
        value={selectedAccountId ?? ""}
        onChange={(e) => onAccountChange(e.target.value === "" ? null : Number(e.target.value))}
        className="h-8 px-2 rounded-md border border-border/60 bg-background text-xs max-w-44 cursor-pointer"
        aria-label="选择账户"
      >
        {accounts.length === 0 && <option value="">无会话</option>}
        {accounts.map(([id, entry]) => (
          <option key={id} value={id}>
            {entry.account.account_name ?? `账户#${id}`}
            {entry.account.paper_account_name ? ` (模拟: ${entry.account.paper_account_name})` : ""}
            {entry.hasActive ? " · 运行中" : ""}
          </option>
        ))}
      </select>
      {sortedSessions.length > 0 && (
        <select
          value={selectedSessionId ?? ""}
          onChange={(e) => onSessionChange(e.target.value || null)}
          className="h-8 px-2 rounded-md border border-border/60 bg-background text-xs max-w-52 cursor-pointer"
          aria-label="选择会话"
        >
          {sortedSessions.map((s: any) => (
            <option key={s.session_id} value={s.session_id}>
              {statusLabel[s.status] ?? s.status}
              {s.active_exchange ? ` · ${s.active_exchange}` : ""}
              {s.started_at ? ` · ${new Date(s.started_at).toLocaleTimeString("zh-CN", { hour12: false })}` : ""}
            </option>
          ))}
        </select>
      )}
      {sortedSessions.length === 0 && selectedAccountId != null && (
        <span className="text-xs text-muted-foreground">该账户暂无会话</span>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// 通用 Hook：轮询 fetch（必须带 JWT，否则 RLS 返回空数组）
// ═══════════════════════════════════════════════════════════════════

function usePoll<T>(url: string | null, interval: number): { data: T | null; loading: boolean; error: string | null; refetch: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 代际号：url 变化后丢弃旧请求的迟到结果，避免串数据
  const genRef = useRef(0);

  // [2026-09-09] 原来这里手搓 fetch：401 只 setError 然后按 5~15s 继续打，
  // 且不参与单飞续期。改走 apiRequest 后统一获得：
  //   - 请求前 ensureFreshAccessToken 主动续期；
  //   - 401 → 单飞 refresh → 重试一次；refresh 无效 → 立即登出（循环终止）；
  //   - 统一超时/错误语义（原手写 AbortController 10s 保留）。
  // 调用点仍传 `${BACKEND}/api/xxx` 全 URL，这里剥前缀得到 apiRequest 需要的 /xxx。
  const load = useCallback(async () => {
    if (!url) return;
    const gen = ++genRef.current;
    const path = url.startsWith(`${BACKEND}/api`) ? url.slice(BACKEND.length + 4) : url;
    try {
      const json = await apiRequest<T>(path, { timeout: 10_000 });
      if (gen !== genRef.current) return;
      setData(json);
      setError(null);
    } catch (e) {
      if (gen !== genRef.current) return;
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg.includes("abort") || /timeout/i.test(msg) ? "请求超时" : msg);
    } finally {
      if (gen === genRef.current) setLoading(false);
    }
  }, [url]);

  const refetch = useCallback(() => { void load(); }, [load]);

  // 可见性感知轮询：隐藏时暂停、回到前台立即补一次、慢响应期间不叠加请求；
  // reloadKey=url → 切换会话/账户时立即重拉（不等下一个 tick）。
  usePolling(load, interval, { enabled: !!url, reloadKey: url ?? "" });

  return { data, loading: url ? loading : false, error, refetch };
}

// ═══════════════════════════════════════════════════════════════════
// Tab 1: 运行总览（2026-09-07 重构：全部接实时活数据）
// ═══════════════════════════════════════════════════════════════════

function OverviewTab({ sessionsData, selectedSessionId }: { sessionsData: any[]; selectedSessionId: string | null }) {
  const runningSession = sessionsData.find((s: any) => s.session_id === selectedSessionId)
    ?? sessionsData.find((s: any) => s.status === "running" || s.status === "defensive");
  const sessionId = runningSession?.session_id ?? selectedSessionId;
  const acctId = runningSession?.paper_account_id ?? runningSession?.account_id ?? null;

  const tickIntervals = usePoll<any>(`${BACKEND}/api/full-auto/tick-intervals`, 15000);
  const tierStatus = usePoll<any>(sessionId ? `${BACKEND}/api/full-auto/tier-status/${sessionId}` : null, 10000);
  const scheduler = usePoll<any>(`${BACKEND}/api/full-auto/debug/scheduler-state`, 15000);
  // slim=1：精简模式（跳过逐条 gate/学习指标富化），全量 30s+ → 亚秒，10s 轮询不再超时
  const mltoThesis = usePoll<any>(sessionId ? `${BACKEND}/api/mlto/sessions/${sessionId}/thesis/summary?slim=1` : null, 10000);
  const positions = usePoll<any>(acctId ? `${BACKEND}/api/paper/positions/${acctId}?status=open` : null, 5000);
  const balance = usePoll<any>(acctId ? `${BACKEND}/api/paper/balance/${acctId}` : null, 10000);
  // 组合预算冻结状态（风控止血：全局/账户/策略/交易对四级）
  const pbState = usePoll<any>(`${BACKEND}/api/full-auto/debug/portfolio-budget`, 15000);
  // 长线 V2 规则化 L1 状态（证据）
  const longV2 = usePoll<any>(sessionId ? `${BACKEND}/api/ops/long-trend-v2?session_id=${sessionId}` : null, 15000);

  const intervals = tickIntervals.data?.intervals ?? { coordinator: 30, mid: 45, long: 240 };
  const labels = tickIntervals.data?.labels ?? {};
  const jobs = scheduler.data?.jobs ?? [];
  const theses: any[] = mltoThesis.data?.theses ?? [];
  const openPositions: any[] = Array.isArray(positions.data) ? positions.data : (positions.data?.positions ?? []);

  // ── 车道数据聚合：每个 tier 的论题统计 + 最近刷新 + 持仓 ──
  const laneOf = (tier: string) => {
    const tierTheses = theses.filter((t: any) => t.tier === tier);
    const accepted = tierTheses.filter((t: any) => t.accepted);
    const recOpen = tierTheses.filter((t: any) => t.accepted && t.recommend_open);
    const lastUpdate = tierTheses.length
      ? new Date(Math.max(...tierTheses.map((t: any) => new Date(t.updated_at ?? 0).getTime())))
      : null;
    const tierPositions = openPositions.filter((p: any) =>
      (p.timeframe_tier ?? "").toLowerCase() === tier ||
      // 日内(=日内波段)持仓归入中线车道（轮55口径：日内波段就是中线）
      (tier === "mid" && (p.trade_nature ?? "").toLowerCase() === "intraday")
    );
    const tierPnl = tierPositions.reduce((s: number, p: any) => s + (Number(p.unrealized_pnl) || 0), 0);
    return { theses: tierTheses, accepted, recOpen, lastUpdate, positions: tierPositions, pnl: tierPnl };
  };
  // [轮55 2026-09-17] 只聚合 mid/long 两条车道（short 已判负期望关停）
  const lanes = { mid: laneOf("mid"), long: laneOf("long") };

  // 心跳：positions 轮询成功 = 后端活
  const backendAlive = !positions.error && positions.data != null;
  const equity = balance.data?.total_equity ?? tierStatus.data?.total_equity ?? 0;
  const upnl = balance.data?.unrealized_pnl ?? 0;
  const realized = balance.data?.realized_pnl ?? 0;
  const fees = balance.data?.total_fee_paid ?? 0;

  const findJob = (pattern: string) => jobs.find((j: any) => j.id?.includes(pattern));
  const midlongJob = findJob("midlong");

  // [轮55 2026-09-17 用户口径] 「日内波段」**就是中线**，两者是同一件事，不能拆成两张卡。
  // 依据：中线车道实测中位持仓 3.2h、94% < 24h（= 日内），唯一真长周期的是长线车道。
  // 因此本页只展示**两条**车道：日内波段(=mid) 与 长线趋势(long)。
  // 原 `short`(短线/scalp) 车道已判负期望关停（SCALP_OPEN_DISABLED=true /
  // SCALP_RESEARCH_ENABLED=false），其卡片不再展示 —— 此前前端错把它命名为
  // 「日内波段」，造成「日内波段」与「中线波段」两张卡并存、语义打架。
  const laneCards = [
    {
      key: "mid", name: "日内波段", icon: Boxes, color: "profit" as const,
      engine: labels.mid ?? "日内波段（中线）LLM 论题主脑",
      interval: intervals.mid, lane: lanes.mid,
      desc: "持仓 12h-48h · 论题 4h TTL · 18h 无进展复盘",
      aiPool: (tierStatus.data?.lanes?.ai_mid ?? []) as string[],
    },
    {
      key: "long", name: "长线趋势", icon: TrendingUp, color: "warning" as const,
      engine: labels.long ?? "长线 LLM 论题主脑",
      interval: intervals.long, lane: lanes.long,
      desc: "持仓 3-7 天 · Chandelier 追踪 · 浮盈≥2% 推保本",
      aiPool: (tierStatus.data?.lanes?.ai_long ?? []) as string[],
    },
  ];

  return (
    <div className="space-y-3">
      {/* ── 系统心跳条 ── */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <StatusPill label="后端" ok={backendAlive} detail={backendAlive ? ":8000 运行中" : "连接异常"} />
        <StatusPill label="会话" ok={!!runningSession} detail={runningSession ? `${runningSession.account_name} · ${runningSession.status}` : "无活跃会话"} />
        <StatusPill label="调度任务" ok={jobs.length > 0} detail={`${jobs.length} 个 job`} />
        <StatusPill label="主脑" ok={tickIntervals.data?.brain_mode === "llm"} detail={tickIntervals.data?.brain_mode === "llm" ? "LLM 论题驱动" : "未启用"} />
        <StatusPill label="日内波段" ok={!!runningSession} detail={`${intervals.mid}s/tick`} />
      </div>

      {/* 风控冻结状态（组合预算止血可见性：全局/账户/策略/交易对四级） */}
      <PortfolioBudgetBanner state={pbState.data} />

      {/* ── 两车道卡片：日内波段(=中线) 与 长线趋势 ── */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {laneCards.map((t) => {
          const lane = t.lane;
          const isLive = true;   // [轮55] 已无"依赖 intraday_enabled 才存活"的车道（短线卡已移除）
          return (
            <Card key={t.key} className="p-4 space-y-2.5 relative overflow-hidden">
              <div className={cn("absolute top-0 right-0 w-20 h-20 rounded-full blur-3xl opacity-10",
                t.color === "profit" ? "bg-profit" : "bg-warning")} />
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className={cn("w-8 h-8 rounded-lg flex items-center justify-center",
                    t.color === "profit" ? "bg-profit/10" : "bg-warning/10")}>
                    <t.icon className={cn("w-4 h-4", t.color === "profit" ? "text-profit" : "text-warning")} />
                  </div>
                  <div>
                    <div className="text-sm font-medium flex items-center gap-1.5">
                      {t.name}
                      {/* 车道活性灯 */}
                      <span className={cn("inline-block w-1.5 h-1.5 rounded-full", isLive ? "bg-profit animate-pulse" : "bg-loss")} />
                    </div>
                    <div className="text-xs text-muted-foreground">{t.engine}</div>
                  </div>
                </div>
                <Badge variant="secondary" className="text-xs tabular-nums">{t.interval}s/tick</Badge>
              </div>

              {/* 下次 tick 倒计时 */}
              <NextTickCountdown job={midlongJob} interval={t.interval} label="下次哨兵" />

              {/* 三问：论题 / 持仓 / 盈亏 */}
              <div className="grid grid-cols-3 gap-2 pt-0.5">
                <Stat label="论题" value={`${lane.accepted.length}/${lane.theses.length}`} sub="accepted/总" />
                <Stat label="持仓" value={String(lane.positions.length)} sub="当前" />
                <Stat
                  label="浮盈"
                  value={`${lane.pnl >= 0 ? "+" : ""}${lane.pnl.toFixed(1)}`}
                  sub="$"
                  tone={lane.pnl > 0 ? "profit" : lane.pnl < 0 ? "loss" : undefined}
                />
              </div>

              <div className="text-xs text-muted-foreground space-y-0.5 pt-1 border-t border-border/30">
                <div>{t.desc}</div>
                {/* [2026-09-17] AI 选币池与后端决策循环同源（消除前后端割裂） */}
                <div className="flex items-center gap-1">
                  <span>AI 池:</span>
                  <span className="font-mono">
                    {t.aiPool.length ? t.aiPool.join(" / ") : "—"}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span>最近论题刷新</span>
                  <span className="tabular-nums">{lane.lastUpdate ? lane.lastUpdate.toLocaleTimeString("zh-CN", { hour12: false }) : "—"}</span>
                </div>
                {lane.recOpen.length > 0 && (
                  <div className="text-profit">待开仓信号：{lane.recOpen.map((x: any) => x.symbol).join(" / ")}</div>
                )}
              </div>
            </Card>
          );
        })}
      </div>

      {/* ── 当前持仓实时表 ── */}
      <Card className="p-0 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border/50 flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Wallet className="w-3.5 h-3.5 text-cyan-300" />
            <span className="text-xs font-semibold">当前持仓</span>
            <Badge variant="secondary" className="text-xs">{openPositions.length}</Badge>
          </div>
          <span className="text-xs text-muted-foreground">5s 轮询 · 杠杆=币种档</span>
        </div>
        {openPositions.length === 0 ? (
          <div className="py-6 text-center text-xs text-muted-foreground">当前无持仓</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-muted-foreground border-b border-border/50">
                  <th className="px-4 py-2 font-medium">币种</th>
                  <th className="px-2 py-2 font-medium">周期</th>
                  <th className="px-2 py-2 font-medium text-right">杠杆</th>
                  <th className="px-2 py-2 font-medium text-right">入场</th>
                  <th className="px-2 py-2 font-medium text-right">现价</th>
                  <th className="px-2 py-2 font-medium text-right">浮盈</th>
                  <th className="px-2 py-2 font-medium text-right">距止损</th>
                  <th className="px-2 py-2 font-medium text-right">距止盈</th>
                  <th className="px-4 py-2 font-medium text-right">持仓</th>
                </tr>
              </thead>
              <tbody>
                {openPositions.map((p: any) => {
                  const entry = Number(p.entry_price) || 0;
                  const mark = Number(p.mark_price) || 0;
                  const isLong = (p.side ?? "").toLowerCase() === "long";
                  const pnl = Number(p.unrealized_pnl) || 0;
                  const dist = (px: number | null) => {
                    if (!px || !mark) return "—";
                    const d = ((Number(px) - mark) / mark) * 100;
                    return `${d >= 0 ? "+" : ""}${d.toFixed(1)}%`;
                  };
                  const holdH = Number(p.hold_age_hours);
                  const holdLabel = Number.isFinite(holdH)
                    ? (holdH >= 24 ? `${(holdH / 24).toFixed(1)}天` : `${holdH.toFixed(1)}h`)
                    : "—";
                  const tierLabel: Record<string, string> = { short: "短线(已停)", mid: "日内波段", long: "长线趋势" };
                  return (
                    <tr key={p.id} className="border-b border-border/20 hover:bg-muted/10 transition-colors">
                      <td className="px-4 py-2 font-medium">
                        <span className="flex items-center gap-1">
                          {isLong
                            ? <ArrowUpRight className="w-3 h-3 text-profit" />
                            : <ArrowDownRight className="w-3 h-3 text-loss" />}
                          {p.symbol}
                        </span>
                      </td>
                      <td className="px-2 py-2">{tierLabel[(p.timeframe_tier ?? "").toLowerCase()] ?? p.timeframe_tier}</td>
                      <td className="px-2 py-2 text-right tabular-nums">{p.leverage}x</td>
                      <td className="px-2 py-2 text-right tabular-nums">{entry ? String(entry).slice(0, 10) : "—"}</td>
                      <td className="px-2 py-2 text-right tabular-nums">{mark ? String(mark).slice(0, 10) : "—"}</td>
                      <td className={cn("px-2 py-2 text-right tabular-nums font-medium", pnl >= 0 ? "text-profit" : "text-loss")}>
                        {pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}
                      </td>
                      <td className="px-2 py-2 text-right tabular-nums text-loss/80">{dist(p.sl_price)}</td>
                      <td className="px-2 py-2 text-right tabular-nums text-profit/80">{p.tp_price ? dist(p.tp_price) : "追踪"}</td>
                      <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">{holdLabel}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* ── 全局 KPI ── */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <KPICard label="总权益" value={`$${Number(equity).toFixed(2)}`} icon={Gauge} />
        <KPICard label="浮动盈亏" value={`${upnl >= 0 ? "+" : ""}$${Number(upnl).toFixed(2)}`} icon={Activity} color={upnl >= 0 ? "text-profit" : "text-loss"} />
        <KPICard label="已实现盈亏" value={`${realized >= 0 ? "+" : ""}$${Number(realized).toFixed(2)}`} icon={CheckCircle2} color={realized >= 0 ? "text-profit" : "text-loss"} />
        <KPICard label="累计手续费" value={`$${Number(fees).toFixed(2)}`} icon={Wallet} color="text-warning" />
        <KPICard label="调度任务" value={jobs.length} icon={Clock} />
      </div>

      {/* ── 分周期策略活动摘要（开仓/平仓/减仓记录） ── */}
      <TierActivityPanels sessionId={sessionId} />

      {/* ── 论题台账：钱谁说了算 ── */}
      <ThesisLedger theses={theses} longV2={longV2.data} brainMode={tickIntervals.data?.brain_mode} />
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// 论题台账（抽成组件，结构不变）
// ═══════════════════════════════════════════════════════════════════

function ThesisLedger({ theses, longV2, brainMode }: { theses: any[]; longV2: any; brainMode?: string }) {
  const [tierFilter, setTierFilter] = useState<string>("all");
  // [2026-09-19 M1] 论题正文展开态。背景（排查实证）：`thesis_summary`（中位 280~285 字）
  // 与 `reasoning_content`（中位 2853~3809 字、最长 4472）**早已随 slim=1 响应到达浏览器**，
  // 但本表只渲染 8 个状态列、正文被丢弃；用户看到的"主脑分析"因此只剩状态数字。
  const [openId, setOpenId] = useState<string | null>(null);
  const filtered = tierFilter === "all" ? theses : theses.filter((t: any) => t.tier === tierFilter);
  const lastUpdate = theses.length
    ? new Date(Math.max(...theses.map((t: any) => new Date(t.updated_at ?? 0).getTime()))).toLocaleTimeString("zh-CN", { hour12: false })
    : "--";
  const tierName: Record<string, string> = { short: "短线(已停)", mid: "日内波段", long: "长线趋势" };

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-medium flex items-center gap-1.5">
          <Brain className="w-4 h-4 text-warning" /> 论题台账 · LLM 主脑
        </span>
        <div className="flex items-center gap-1.5">
          {["all", "mid", "long"].map((f) => (
            <button key={f} onClick={() => setTierFilter(f)}
              className={cn("px-2 py-0.5 text-xs rounded cursor-pointer transition-colors",
                tierFilter === f ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-muted/30")}>
              {f === "all" ? "全部" : tierName[f]}
            </button>
          ))}
          <Badge variant="secondary" className="text-xs ml-1">
            {brainMode === "llm" ? "brain=llm" : "brain=off"} · 更新 {lastUpdate}
          </Badge>
        </div>
      </div>
      <div className="text-xs text-muted-foreground mb-2">
        没有新鲜 accepted 论题 = 不开。因子 / V2 / 图审只是证据。
      </div>
      {filtered.length === 0 ? (
        <div className="text-xs text-muted-foreground py-2">暂无论题</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-muted-foreground border-b border-border/50">
                <th className="text-left py-1 pr-2">币</th>
                <th className="text-left py-1 pr-2">档</th>
                <th className="text-left py-1 pr-2">方向</th>
                <th className="text-left py-1 pr-2">开?</th>
                <th className="text-left py-1 pr-2">平?</th>
                <th className="text-right py-1 pr-2">失效价</th>
                <th className="text-left py-1 pr-2">watch</th>
                <th className="text-left py-1 pr-2">run</th>
                <th className="text-left py-1">正文</th>
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, 24).map((t: any) => {
                const rowKey = `${t.symbol}-${t.tier}-${t.thesis_id}`;
                const isOpen = openId === rowKey;
                const body = String(t.thesis_summary ?? "");
                const reasoning = String(t.reasoning_content ?? "");
                return (
                  <Fragment key={rowKey}>
                    <tr className="border-b border-border/30">
                      <td className="py-1 pr-2 font-medium">{t.symbol}</td>
                      <td className="py-1 pr-2">{tierName[t.tier] ?? t.tier}</td>
                      <td className={cn(
                        "py-1 pr-2",
                        t.direction === "long" ? "text-profit" : t.direction === "short" ? "text-loss" : "",
                      )}>{t.direction ?? "—"}</td>
                      <td className="py-1 pr-2">{t.accepted && t.recommend_open ? "是" : t.accepted ? "观望" : "未过门"}</td>
                      <td className="py-1 pr-2">{t.should_close ? "要平" : "—"}</td>
                      <td className="py-1 pr-2 text-right num">{t.inv_price ?? "—"}</td>
                      <td className="py-1 pr-2 truncate max-w-[8rem]" title={t.watch_reason ?? ""}>{t.watch_reason ?? (t.is_fresh ? "持有" : "过期")}</td>
                      <td className="py-1 truncate max-w-[6rem]" title={t.analysis_run_id ?? ""}>{(t.analysis_run_id || "—").slice(0, 8)}</td>
                      <td className="py-1">
                        <button
                          type="button"
                          onClick={() => setOpenId(isOpen ? null : rowKey)}
                          className={cn(
                            "px-1.5 py-0.5 rounded text-xs cursor-pointer transition-colors",
                            isOpen ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-muted/40",
                          )}
                          title={body || "无正文"}
                        >
                          {isOpen ? "▾ 收起" : `▸ 展开${body ? ` (${body.length})` : ""}`}
                        </button>
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="bg-muted/10">
                        <td colSpan={9} className="py-2 pl-2 pr-2">
                          <div className="text-xs text-muted-foreground mb-1">
                            主脑论题正文（thesis_summary · {body.length} 字）
                          </div>
                          <div className="whitespace-pre-wrap leading-relaxed text-xs bg-background/40 rounded p-2 max-h-64 overflow-y-auto">
                            {body || "（本论题无 thesis_summary）"}
                          </div>
                          {reasoning && (
                            <details className="mt-2">
                              <summary className="text-xs text-muted-foreground cursor-pointer">
                                展开主脑推理全文（reasoning_content · {reasoning.length} 字）
                              </summary>
                              <div className="whitespace-pre-wrap leading-relaxed text-xs bg-background/40 rounded p-2 mt-1 max-h-96 overflow-y-auto">
                                {reasoning}
                              </div>
                            </details>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {(longV2?.symbols ?? []).length > 0 && (
        <div className="mt-3 pt-2 border-t border-border/40">
          <div className="text-xs text-muted-foreground mb-1">V2 L1（证据，不能自己开仓）</div>
          <div className="flex gap-1.5 flex-wrap">
            {(longV2?.symbols ?? []).map((s: any) => (
              <span key={s.symbol} className={cn(
                "px-1.5 py-0.5 rounded text-xs",
                s.state === "up" ? "bg-profit/10 text-profit" :
                s.state === "down" ? "bg-loss/10 text-loss" : "bg-muted/30 text-muted-foreground"
              )}>
                {s.symbol} {s.state} ({s.score})
              </span>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

// ═══════════════════════════════════════════════════════════════════
// 分周期策略活动摘要（开仓/平仓/减仓记录）
// ═══════════════════════════════════════════════════════════════════

function TierActivityPanels({ sessionId }: { sessionId?: string }) {
  const { data } = usePoll<any>(sessionId ? `${BACKEND}/api/full-auto/tier-activity/${sessionId}` : null, 10000);
  // [轮55 修正] 日内波段=中线（同一车道），此前把 acts.mid 重复渲染成两列、
  // acts.short 取了不用。现固定两列：日内波段(mid) + 长线趋势(long)。
  const acts = data ?? { mid: [], long: [] };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <TierActivityColumn title="日内波段" items={acts.mid ?? []} color="profit" icon={Boxes} />
      <TierActivityColumn title="长线趋势" items={acts.long ?? []} color="warning" icon={TrendingUp} />
    </div>
  );
}

function TierActivityColumn({ title, items, color, icon: Icon }: {
  title: string; items: any[]; color: string; icon: any;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    // 后端按 id.desc() 返回，最新记录排在数组最前面；
    // 自动滚屏应保持顶部可见，而不是滚到底部（那里是最老的记录）
    if (!paused && scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
  }, [items, paused]);

  return (
    <Card className="p-3 flex flex-col">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <Icon className={cn("w-3.5 h-3.5",
            color === "primary" ? "text-primary" : color === "profit" ? "text-profit" : "text-warning")} />
          <span className="text-xs font-medium">{title}</span>
        </div>
        <div className="flex items-center gap-1">
          <Badge variant="secondary" className="text-xs">{items.length}</Badge>
          <button onClick={() => setPaused(!paused)} className="text-muted-foreground hover:text-foreground cursor-pointer transition-colors">
            {paused ? <Play className="w-3 h-3" /> : <Pause className="w-3 h-3" />}
          </button>
        </div>
      </div>
      <div ref={scrollRef} className="text-xs h-[300px] overflow-y-auto bg-black/20 rounded p-1.5 space-y-0.5">
        {items.length === 0 ? (
          <div className="text-muted-foreground text-center py-4 text-xs">暂无{title}策略记录</div>
        ) : (
          items.map((item: any, i: number) => {
            const isAction = item.action !== "观望";
            const isBlocked = item.allowed === false;
            const isExecuted = item.executed;
            return (
              <div key={item.id || `${item.time}-${item.symbol}-${item.action}-${i}`} className={cn(
                "flex items-center gap-1 py-0.5 px-1 rounded leading-tight",
                isExecuted ? "bg-primary/5" : isBlocked ? "bg-loss/5" : ""
              )}>
                <span className="text-muted-foreground shrink-0 tabular-nums">{item.time}</span>
                <span className={cn("shrink-0 font-medium",
                  isExecuted ? "text-primary" : isBlocked ? "text-loss" : isAction ? "text-warning" : "text-muted-foreground")}>
                  {item.action}
                </span>
                {item.symbol && <span className="shrink-0 font-medium">{item.symbol}</span>}
                {item.direction && item.direction !== "neutral" && (
                  <span className={cn("shrink-0 text-xs",
                    item.direction === "long" ? "text-profit" : "text-loss")}>
                    {item.direction === "long" ? "多" : "空"}
                  </span>
                )}
                {item.lane_note && (
                  <span className="text-warning shrink-0 text-xs">{item.lane_note}</span>
                )}
                {item.confidence > 0 && (
                  <span className="text-muted-foreground shrink-0 tabular-nums">{item.confidence}%</span>
                )}
                {isBlocked && item.block_reason && (
                  <span className="text-loss text-xs truncate" title={item.block_reason}>
                    {item.block_reason}
                  </span>
                )}
                {isExecuted && (
                  <span className="text-profit text-xs shrink-0">已执行</span>
                )}
                {item.reasoning && (
                  <span className="text-muted-foreground text-xs truncate ml-auto" title={item.reasoning}>
                    {item.reasoning}
                  </span>
                )}
              </div>
            );
          })
        )}
      </div>
    </Card>
  );
}

function NextTickCountdown({ job, interval, label = "下次 tick" }: { job: any; interval: number; label?: string }) {
  const [remaining, setRemaining] = useState(interval);
  useEffect(() => {
    // 无 job（如日内波段车道不走 APScheduler）时本地按 interval 倒数，到 0 重置——
    // 此前恒显示 interval 不动，被用户感知为"数据不更新"。
    const calc = () => {
      if (job?.next_run) {
        const next = new Date(job.next_run).getTime();
        const diff = Math.max(0, Math.floor((next - Date.now()) / 1000));
        setRemaining(diff);
      } else {
        setRemaining((r) => (r <= 1 ? interval : r - 1));
      }
    };
    calc();
    const id = setInterval(calc, 1000);
    return () => clearInterval(id);
  }, [job?.next_run, interval]);

  const pct = interval > 0 ? ((interval - remaining) / interval) * 100 : 0;
  return (
    <div className="flex items-center gap-2 text-xs">
      <Clock className="w-3 h-3 text-muted-foreground" />
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular-nums font-medium text-primary">{remaining}s</span>
      <div className="flex-1 h-1 rounded-full bg-muted/20 overflow-hidden">
        <div className="h-full bg-primary/40 rounded-full transition-all" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Tab 2: 执行统计
// ═══════════════════════════════════════════════════════════════════

function StatsTab({ selectedAccountId }: { selectedAccountId: number | null }) {
  const metrics = usePoll<any>(`${BACKEND}/api/learning/loop/metrics`, 30000);
  const agentStats = usePoll<any>(`${BACKEND}/api/analytics/by-agent?days=7`, 60000);
  const decisions = usePoll<any>(
    `${BACKEND}/api/atas/decisions?limit=100${selectedAccountId != null ? `&account_id=${selectedAccountId}` : ""}`,
    30000,
  );

  const loading = metrics.loading && !metrics.data;

  if (loading) return <LoadingSpinner />;

  // 构建图表数据
  const tickMetrics = metrics.data ?? {};
  const barData = Object.entries(tickMetrics).map(([name, m]: [string, any]) => ({
    name: name.replace(/_/g, " ").slice(0, 15),
    p50: Math.round(m.p50_ms ?? 0),
    p95: Math.round(m.p95_ms ?? 0),
    successRate: Math.round((m.success_rate ?? 0) * 100),
    count: m.count ?? 0,
  }));

  // 决策饼图
  const allDecisions = decisions.data?.decisions ?? decisions.data ?? [];
  const decStats = { buy: 0, sell: 0, hold: 0 };
  allDecisions.forEach((d: any) => {
    const op = (d.operation || "hold").toLowerCase();
    if (op === "buy" || op === "add") decStats.buy++;
    else if (op === "sell" || op === "reduce" || op === "close") decStats.sell++;
    else decStats.hold++;
  });
  const pieData = [
    { name: "买入", value: decStats.buy, color: "#00C896" },
    { name: "卖出", value: decStats.sell, color: "#FF4D6D" },
    { name: "观望", value: decStats.hold, color: "#6B7785" },
  ];

  const agents = agentStats.data?.agents ?? {};

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={() => { metrics.refetch(); agentStats.refetch(); decisions.refetch(); }}>
          <RefreshCw className="w-3.5 h-3.5" /> 刷新
        </Button>
      </div>

      {/* 耗时 + 成功率 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Card className="p-4">
          <CardHead
            icon={<BarChart3 className="w-3.5 h-3.5 text-cyan-300" />}
            title="Tick 耗时分布"
            hint="P50 / P95 · ms"
            className="mb-3"
          />
          {barData.length === 0 ? (
            <EmptyChart />
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <RBarChart data={barData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1E2530" />
                <XAxis dataKey="name" tick={{ fill: "#6B7785", fontSize: 12 }} />
                <YAxis tick={{ fill: "#6B7785", fontSize: 12 }} />
                <RTooltip contentStyle={{ background: "#11161D", border: "1px solid #1E2530", borderRadius: 6, fontSize: 12 }} />
                <RBar dataKey="p50" fill="#5B8DEF" radius={[3, 3, 0, 0]} name="P50" />
                <RBar dataKey="p95" fill="#FFB938" radius={[3, 3, 0, 0]} name="P95" />
              </RBarChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card className="p-4">
          <CardHead
            icon={<TrendingUp className="w-3.5 h-3.5 text-profit" />}
            title="执行成功率"
            hint="近 7 天 · %"
            className="mb-3"
          />
          {barData.length === 0 ? (
            <EmptyChart />
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <RLineChart data={barData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1E2530" />
                <XAxis dataKey="name" tick={{ fill: "#6B7785", fontSize: 12 }} />
                <YAxis domain={[0, 100]} tick={{ fill: "#6B7785", fontSize: 12 }} />
                <RTooltip contentStyle={{ background: "#11161D", border: "1px solid #1E2530", borderRadius: 6, fontSize: 12 }} />
                <RLine type="monotone" dataKey="successRate" stroke="#00C896" strokeWidth={2} dot={{ fill: "#00C896", r: 3 }} />
              </RLineChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>

      {/* 决策分布 + Agent 绩效 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Card className="p-4">
          <CardHead
            icon={<Radio className="w-3.5 h-3.5 text-violet-400" />}
            title="决策分布"
            hint={`${allDecisions.length} 条`}
            badge={selectedAccountId != null ? <Badge variant="secondary" className="ml-1.5">账户 {selectedAccountId}</Badge> : null}
            className="mb-3"
          />
          {allDecisions.length === 0 ? (
            <EmptyChart />
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <RPieChart>
                <RPie data={pieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={70} innerRadius={40}>
                  {pieData.map((e, i) => <RCell key={i} fill={e.color} />)}
                </RPie>
                <RTooltip contentStyle={{ background: "#11161D", border: "1px solid #1E2530", borderRadius: 6, fontSize: 12 }} />
              </RPieChart>
            </ResponsiveContainer>
          )}
          <div className="flex justify-center gap-3 mt-2">
            {pieData.map((p) => (
              <div key={p.name} className="flex items-center gap-1 text-xs">
                <div className="w-2 h-2 rounded-full" style={{ background: p.color }} />
                <span className="text-muted-foreground">{p.name}</span>
                <span className="tabular-nums font-medium">{p.value}</span>
              </div>
            ))}
          </div>
        </Card>

        <Card className="p-4">
          <CardHead
            icon={<Gauge className="w-3.5 h-3.5 text-cyan-300" />}
            title="Agent 绩效"
            hint="近 7 天"
            badge={<Badge variant="secondary" className="ml-1.5">全局</Badge>}
            className="mb-3"
          />
          <div className="space-y-2">
            {Object.entries(agents).map(([name, a]: [string, any]) => (
              <div key={name} className="p-2 rounded bg-muted/10 text-xs space-y-1">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{name}</span>
                  <span className={cn("tabular-nums font-medium", (a.net_pnl ?? 0) >= 0 ? "text-profit" : "text-loss")}>
                    {(a.net_pnl ?? 0) >= 0 ? "+" : ""}${(a.net_pnl ?? 0).toFixed(2)}
                  </span>
                </div>
                <div className="flex gap-3 text-xs text-muted-foreground">
                  <span>交易 {a.trades ?? 0}</span>
                  <span>胜率 {((a.win_rate ?? 0) * 100).toFixed(0)}%</span>
                  <span>PF {a.profit_factor?.toFixed(2) ?? "—"}</span>
                  <span>持仓 {(a.avg_hold_hours ?? 0).toFixed(1)}h</span>
                </div>
              </div>
            ))}
            {Object.keys(agents).length === 0 && <div className="text-center text-xs text-muted-foreground py-4">暂无绩效数据</div>}
          </div>
        </Card>
      </div>

      {/* Tick 指标明细表 */}
      <Card className="p-0 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border/50 flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Activity className="w-3.5 h-3.5 text-cyan-300" />
            <span className="text-xs font-semibold">Tick 执行明细</span>
          </div>
          <span className="text-xs text-muted-foreground tabular-nums">10s 循环 · 全任务</span>
        </div>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-muted-foreground border-b border-border/50">
              <th className="px-4 py-2 font-medium">任务</th>
              <th className="px-4 py-2 font-medium text-right">次数</th>
              <th className="px-4 py-2 font-medium text-right">P50</th>
              <th className="px-4 py-2 font-medium text-right">P95</th>
              <th className="px-4 py-2 font-medium text-right">成功率</th>
              <th className="px-4 py-2 font-medium text-right">上次耗时</th>
            </tr>
          </thead>
          <tbody>
            {barData.map((row) => (
              <tr key={row.name} className="border-b border-border/20 hover:bg-muted/10">
                <td className="px-4 py-2 font-mono">{row.name}</td>
                <td className="px-4 py-2 text-right tabular-nums">{row.count}</td>
                <td className="px-4 py-2 text-right tabular-nums">{row.p50}ms</td>
                <td className="px-4 py-2 text-right tabular-nums text-warning">{row.p95}ms</td>
                <td className="px-4 py-2 text-right tabular-nums">
                  <span className={row.successRate >= 95 ? "text-profit" : row.successRate >= 80 ? "text-warning" : "text-loss"}>
                    {row.successRate}%
                  </span>
                </td>
                <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">
                  {Math.round(tickMetrics[Object.keys(tickMetrics)[barData.indexOf(row)]]?.last_elapsed_ms ?? 0)}ms
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Tab 3: 决策流
// ═══════════════════════════════════════════════════════════════════

function DecisionsTab({ selectedAccountId }: { selectedAccountId: number | null }) {
  const { data, loading, refetch } = usePoll<any>(
    `${BACKEND}/api/atas/decisions?limit=50${selectedAccountId != null ? `&account_id=${selectedAccountId}` : ""}`,
    15000,
  );
  const [filter, setFilter] = useState<string>("all");

  const decisions = data?.decisions ?? (Array.isArray(data) ? data : []);
  const filtered = filter === "all" ? decisions : decisions.filter((d: any) => {
    const r = d.reasoning || "";
    if (filter === "long") return r.includes("long_trend_v2") || r.includes("L1") || r.includes("长线") || r.includes("tier=long") || r.includes("结构破坏") || r.includes("Chandelier");
    if (filter === "buy") return ["buy", "add"].includes((d.operation || "").toLowerCase());
    if (filter === "sell") return ["sell", "reduce", "close"].includes((d.operation || "").toLowerCase());
    return true;
  });

  const stats = {
    buy: decisions.filter((d: any) => ["buy", "add"].includes((d.operation || "").toLowerCase())).length,
    sell: decisions.filter((d: any) => ["sell", "reduce", "close"].includes((d.operation || "").toLowerCase())).length,
    hold: decisions.filter((d: any) => (d.operation || "").toLowerCase() === "hold").length,
    executed: decisions.filter((d: any) => d.executed).length,
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1">
          {["all", "long", "buy", "sell"].map(f => (
            <button key={f} onClick={() => setFilter(f)}
              className={cn("px-2 py-1 text-xs rounded cursor-pointer transition-colors",
                filter === f ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-muted/30")}>
              {f === "all" ? "全部" : f === "long" ? "长线" : f === "buy" ? "买入" : "卖出"}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-profit">买 {stats.buy}</span>
          <span className="text-xs text-loss">卖 {stats.sell}</span>
          <span className="text-xs text-muted-foreground">观望 {stats.hold}</span>
          <Button variant="outline" size="sm" onClick={refetch} disabled={loading}>
            <RefreshCw className={cn("w-3 h-3", loading && "animate-spin")} />
          </Button>
        </div>
      </div>

      <Card className="p-0 overflow-hidden">
        <div className="max-h-[600px] overflow-y-auto divide-y divide-border/20">
          {loading && decisions.length === 0 ? (
            <LoadingSpinner />
          ) : filtered.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground text-sm">暂无决策</div>
          ) : filtered.map((d: any, i: number) => {
            const op = d.operation || "hold";
            const isBuy = ["buy", "add"].includes(op);
            const isSell = ["sell", "reduce", "close"].includes(op);
            return (
              <div key={d.id || i} className="px-4 py-2.5 hover:bg-muted/10 transition-colors">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs text-muted-foreground font-mono tabular-nums shrink-0">
                    {d.created_at ? new Date(d.created_at).toLocaleTimeString("zh-CN", { hour12: false }) : "--"}
                  </span>
                  <span className="text-xs font-bold shrink-0">{d.symbol}</span>
                  <Badge className={cn("text-xs shrink-0",
                    isBuy ? "bg-profit/20 text-profit" : isSell ? "bg-loss/20 text-loss" : "bg-muted text-muted-foreground")}>
                    {isBuy ? "买入" : isSell ? "卖出" : "观望"}
                  </Badge>
                  {d.target_portion > 0 && (
                    <span className="text-xs text-muted-foreground tabular-nums">目标 {(d.target_portion * 100).toFixed(0)}%</span>
                  )}
                  {d.executed ? (
                    <Badge variant="outline" className="text-xs text-profit border-profit/30">已执行</Badge>
                  ) : null}
                </div>
                {d.reasoning && <p className="text-xs text-muted-foreground line-clamp-2 pl-1">{d.reasoning}</p>}
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Tab 4: 调度监控
// ═══════════════════════════════════════════════════════════════════

function SchedulerTab({ selectedSessionId }: { selectedSessionId: string | null }) {
  const { data, loading, refetch } = usePoll<any>(`${BACKEND}/api/full-auto/debug/scheduler-state`, 15000);
  const tickIntervals = usePoll<any>(`${BACKEND}/api/full-auto/tick-intervals`, 30000);

  if (loading && !data) return <LoadingSpinner />;

  const jobs = data?.jobs ?? [];
  const intervals = tickIntervals.data?.intervals ?? { coordinator: 30, mid: 45, long: 240 };
  const labels = tickIntervals.data?.labels ?? {};
  const running = data?.scheduler_running ?? false;
  const runningSessions: string[] = data?.running_sessions ?? [];

  // 分类 jobs
  const fullAutoJobs = jobs.filter((j: any) => j.id?.includes("fullauto"));
  const otherJobs = jobs.filter((j: any) => !j.id?.includes("fullauto"));

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Server className="w-4 h-4 text-primary" />
          <span className="text-sm font-medium">APScheduler ({jobs.length} 个任务)</span>
          <Badge variant={running ? "default" : "secondary"} className={cn("text-xs", running && "bg-profit/20 text-profit")}>
            {running ? "运行中" : "已停止"}
          </Badge>
        </div>
        <Button variant="outline" size="sm" onClick={refetch}>
          <RefreshCw className="w-3.5 h-3.5" /> 刷新
        </Button>
      </div>

      {/* 运行中的会话（多账户，高亮当前所选） */}
      <Card className="p-4">
        <CardHead
          icon={<Server className="w-3.5 h-3.5 text-cyan-300" />}
          title="运行中的会话"
          hint={`${runningSessions.length} 个`}
          className="mb-2"
        />
        <div className="flex gap-1.5 flex-wrap">
          {runningSessions.length === 0 && <div className="text-xs text-muted-foreground">无</div>}
          {runningSessions.map((sid: string) => (
            <span key={sid} className={cn("px-2 py-1 rounded text-xs font-mono",
              sid === selectedSessionId ? "bg-primary/15 text-primary font-medium" : "bg-muted/20 text-muted-foreground")}>
              {sid}
            </span>
          ))}
        </div>
      </Card>

      {/* 两车道 tick 配置 vs 实际（mid 不再重复展示两卡） */}
      <Card className="p-4">
        <CardHead
          icon={<Clock className="w-3.5 h-3.5 text-cyan-300" />}
          title="Tick 间隔（运行时真实值）"
          hint="秒 / tick"
          className="mb-3"
        />
        <div className="grid grid-cols-2 gap-3">
          {[
            { label: labels.mid ?? "日内波段（中线）", val: intervals.mid, color: "text-profit" },
            { label: labels.long ?? "长线趋势", val: intervals.long, color: "text-warning" },
          ].map(t => (
            <div key={t.label} className="text-center p-3 rounded-lg bg-muted/10">
              <div className="text-xs text-muted-foreground mb-1 truncate" title={t.label}>{t.label}</div>
              <div className={cn("text-2xl font-bold tabular-nums", t.color)}>{t.val}</div>
              <div className="text-xs text-muted-foreground">秒/tick</div>
            </div>
          ))}
        </div>
      </Card>

      {/* FullAuto 任务 */}
      <Card className="p-0 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border/50 flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <RefreshCw className="w-3.5 h-3.5 text-cyan-300" />
            <span className="text-xs font-semibold">FullAuto 调度任务</span>
          </div>
          <span className="text-xs text-muted-foreground tabular-nums">{fullAutoJobs.length} 项</span>
        </div>
        <div className="divide-y divide-border/20">
          {fullAutoJobs.map((j: any) => {
            const nextRun = j.next_run ? new Date(j.next_run) : null;
            const isOverdue = nextRun && nextRun < new Date();
            return (
              <div key={j.id} className="flex items-center gap-3 px-4 py-2 text-xs">
                <CheckCircle2 className={cn("w-3.5 h-3.5 shrink-0", isOverdue ? "text-warning" : "text-profit")} />
                <span className="font-mono flex-1 truncate">{j.id}</span>
                <span className="text-muted-foreground tabular-nums shrink-0">
                  {nextRun ? nextRun.toLocaleTimeString("zh-CN", { hour12: false }) : "—"}
                </span>
                {isOverdue && <Badge className="bg-warning/20 text-warning text-xs">超时</Badge>}
              </div>
            );
          })}
          {fullAutoJobs.length === 0 && <div className="py-4 text-center text-xs text-muted-foreground">暂无 FullAuto 任务</div>}
        </div>
      </Card>

      {/* 其他系统任务 */}
      <Card className="p-0 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border/50 flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Clock className="w-3.5 h-3.5 text-muted-foreground" />
            <span className="text-xs font-semibold">系统调度任务</span>
          </div>
          <span className="text-xs text-muted-foreground tabular-nums">{otherJobs.length} 项</span>
        </div>
        <div className="divide-y divide-border/20 max-h-60 overflow-y-auto">
          {otherJobs.map((j: any) => {
            const nextRun = j.next_run ? new Date(j.next_run) : null;
            return (
              <div key={j.id} className="flex items-center gap-3 px-4 py-1.5 text-xs">
                <Clock className="w-3 h-3 text-muted-foreground shrink-0" />
                <span className="font-mono flex-1 truncate text-muted-foreground">{j.id}</span>
                <span className="text-xs text-muted-foreground tabular-nums shrink-0">
                  {nextRun ? nextRun.toLocaleTimeString("zh-CN", { hour12: false }) : "—"}
                </span>
              </div>
            );
          })}
        </div>
      </Card>

      {/* 统一循环状态 */}
      {data && (
        <Card className="p-4">
          <CardHead
            icon={<Activity className="w-3.5 h-3.5 text-cyan-300" />}
            title="统一循环状态"
            hint="FullAuto Loop"
            className="mb-2"
          />
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <Detail label="运行中" value={data.unified_loop_running ? "是" : "否"} />
            <Detail label="tick 计数" value={String(Object.values(data.unified_tick_count ?? {})[0] ?? 0)} />
            <Detail label="DB 健康检查" value={data.db_last_health_check ? new Date(data.db_last_health_check).toLocaleTimeString("zh-CN", { hour12: false }) : "—"} />
            <Detail label="事件日志数" value={String(data.db_event_log_count ?? 0)} />
          </div>
        </Card>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Tab 5: 实时日志（2026-09-07 重构：tail 真实 backend.log，3s 滚屏）
// ═══════════════════════════════════════════════════════════════════

function LogsTab() {
  const [paused, setPaused] = useState(false);
  const [levelFilter, setLevelFilter] = useState<string>("all");
  const [keyword, setKeyword] = useState("");
  const [showAccess, setShowAccess] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  const levelParam = levelFilter === "all" ? "" : levelFilter === "info" ? "INFO" : levelFilter === "warn" ? "WARNING" : "ERROR";
  const { data, loading, refetch } = usePoll<any>(
    `${BACKEND}/api/system-logs/tail?lines=300${levelParam ? `&level=${levelParam}` : ""}${keyword ? `&q=${encodeURIComponent(keyword)}` : ""}${showAccess ? "&access=true" : ""}`,
    3000,
  );

  const lines: any[] = Array.isArray(data?.logs) ? data.logs : [];
  const mtime = data?.file_mtime ? new Date(data.file_mtime * 1000) : null;
  const logAlive = mtime ? (Date.now() - mtime.getTime()) < 15000 : false;

  useEffect(() => {
    // tail 返回时间正序（旧→新），自动滚到底部看最新
    if (!paused && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [lines, paused]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          <Button size="sm" variant={paused ? "default" : "outline"} onClick={() => setPaused(!paused)}>
            {paused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
            {paused ? "继续滚屏" : "暂停"}
          </Button>
          <div className="flex items-center gap-1">
            {["all", "info", "warn", "error"].map(l => (
              <button key={l} onClick={() => setLevelFilter(l)}
                className={cn("px-2 py-1 text-xs rounded cursor-pointer transition-colors",
                  levelFilter === l ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-muted/30")}>
                {l === "all" ? "全部" : l === "info" ? "INFO+" : l === "warn" ? "WARN+" : "ERROR"}
              </button>
            ))}
          </div>
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="关键字过滤（如 MidLongBrain / short / ERROR）"
            className="h-7 px-2 rounded-md border border-border/60 bg-background text-xs w-64"
          />
          <label className="flex items-center gap-1 text-xs text-muted-foreground cursor-pointer">
            <input type="checkbox" checked={showAccess} onChange={(e) => setShowAccess(e.target.checked)} />
            含访问日志
          </label>
        </div>
        <div className="flex items-center gap-2">
          <span className={cn("flex items-center gap-1 text-xs", logAlive ? "text-profit" : "text-loss")}>
            <span className={cn("inline-block w-1.5 h-1.5 rounded-full", logAlive ? "bg-profit animate-pulse" : "bg-loss")} />
            {logAlive ? "日志流活跃" : "日志停滞"}
          </span>
          <Button variant="outline" size="sm" onClick={refetch} disabled={loading}>
            <RefreshCw className={cn("w-3 h-3", loading && "animate-spin")} />
          </Button>
        </div>
      </div>

      <Card className="p-0 overflow-hidden">
        <div ref={logRef} className="font-mono text-xs h-[560px] overflow-y-auto bg-black/30 p-3 leading-relaxed">
          {lines.length === 0 ? (
            <div className="text-muted-foreground text-center py-8">暂无匹配日志（调整级别/关键字过滤）</div>
          ) : (
            lines.map((line: any, i: number) => {
              const lvl = String(line.level || "INFO");
              const isErr = lvl === "ERROR" || lvl === "CRITICAL";
              const isWarn = lvl === "WARNING";
              return (
                <div key={i} className={cn(
                  "py-0.5 px-1 hover:bg-muted/10 flex gap-2",
                  isErr ? "text-loss" : isWarn ? "text-warning" : "text-muted-foreground"
                )}>
                  <span className="text-muted-foreground/60 shrink-0 tabular-nums">{line.ts ? String(line.ts).slice(11) : ""}</span>
                  <span className={cn("shrink-0 w-12", isErr ? "text-loss" : isWarn ? "text-warning" : "text-cyan-400/70")}>{lvl}</span>
                  <span className="text-muted-foreground/50 shrink-0 max-w-[10rem] truncate" title={line.module}>{line.module ? String(line.module).replace("backend.services.", "…") : ""}</span>
                  <span className="flex-1 break-all">{line.msg}</span>
                </div>
              );
            })
          )}
        </div>
      </Card>
      <div className="text-xs text-muted-foreground">
        数据源：logs/backend.log（应用级日志）· 3s 轮询 · 默认已过滤行情轮询访问日志
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// 通用组件
// ═══════════════════════════════════════════════════════════════════

/** Aurora 卡片头：标题 + 图标 + 可选徽章，右侧可选提示 */
function CardHead({ icon, title, hint, badge, className }: { icon?: any; title: any; hint?: any; badge?: any; className?: string }) {
  return (
    <div className={cn("flex items-center justify-between gap-2", className)}>
      <div className="flex items-center gap-1.5 min-w-0">
        {icon}
        <span className="text-xs font-semibold truncate">{title}</span>
        {badge}
      </div>
      {hint != null && (
        <span className="text-xs text-muted-foreground shrink-0 tabular-nums">{hint}</span>
      )}
    </div>
  );
}

function LoadingSpinner() {
  return (
    <div className="flex justify-center py-12">
      <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
    </div>
  );
}

function EmptyChart() {
  return <div className="h-[220px] flex items-center justify-center text-xs text-muted-foreground">暂无数据</div>;
}

/** 风控冻结状态横幅（统一冻结台账可见性） */
function PortfolioBudgetBanner({ state }: { state: any }) {
  if (!state || state.error) return null;
  const fmt = (v: any) => {
    const s = Math.max(0, Math.round(Number(v) || 0));
    if (s >= 3600) return `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m`;
    if (s >= 60) return `${Math.floor(s / 60)}m${s % 60}s`;
    return `${s}s`;
  };
  // 统一台账结构
  const active = Array.isArray(state.active_freeze) ? state.active_freeze : [];
  const budget = state.budget ?? {};
  const globalFrozen = !!budget.global_frozen;
  const accountFrozen = Object.keys(budget.account_frozen ?? {}).length > 0;
  const strategyFrozen = Object.keys(budget.strategy_frozen ?? {}).length > 0;
  const any = active.length > 0 || globalFrozen || accountFrozen || strategyFrozen;
  if (!any) return null;
  return (
    <Card className="p-3 border-warning/40 bg-warning/10">
      <div className="flex items-center gap-2 mb-1.5">
        <AlertTriangle className="w-4 h-4 text-warning shrink-0" />
        <span className="text-xs font-semibold text-warning">风控冻结（统一台账 · 交易对级）</span>
        <span className="text-xs text-muted-foreground ml-auto">15s 轮询</span>
      </div>
      <div className="text-xs text-muted-foreground space-y-0.5">
        {globalFrozen && (
          <div className="text-loss font-medium">全局冻结（历史遗留，设计上不应再自动触发）</div>
        )}
        {accountFrozen && (
          <div className="text-loss font-medium">账户级冻结（历史遗留）</div>
        )}
        {strategyFrozen && (
          <div className="text-loss font-medium">策略级冻结（历史遗留）</div>
        )}
        {active.map((f: any) => (
          <div key={`${f.strategy}-${f.symbol}`} className="text-loss">
            冻结 {f.symbol}（{f.strategy}）· 剩余 {fmt(f.remaining_s)} · {f.why}
          </div>
        ))}
        {active.length === 0 && (
          <div className="text-xs opacity-70">冻结台账详情见后端 /api/full-auto/debug/portfolio-budget</div>
        )}
      </div>
    </Card>
  );
}

function StatusPill({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    <Card className="p-2.5 flex items-center gap-2">
      {ok ? <CheckCircle2 className="w-4 h-4 text-profit shrink-0" /> : <XCircle className="w-4 h-4 text-loss shrink-0" />}
      <div className="min-w-0">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="text-xs font-medium truncate">{detail}</div>
      </div>
    </Card>
  );
}

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: "profit" | "loss" }) {
  return (
    <div className="text-center p-1.5 rounded bg-muted/10">
      <div className={cn("text-sm font-bold tabular-nums", tone === "profit" && "text-profit", tone === "loss" && "text-loss")}>{value}</div>
      <div className="text-xs text-muted-foreground">{label}{sub ? ` · ${sub}` : ""}</div>
    </div>
  );
}

function KPICard({ label, value, icon: Icon, color }: { label: string; value: any; icon: any; color?: string }) {
  return (
    <Card className="p-3 flex items-center gap-2">
      <Icon className={cn("w-4 h-4", color ?? "text-primary")} />
      <div className="min-w-0">
        <div className={cn("text-base font-bold tabular-nums truncate", color)}>{value}</div>
        <div className="text-xs text-muted-foreground">{label}</div>
      </div>
    </Card>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground mb-0.5">{label}</div>
      <div className="text-xs font-medium truncate">{value}</div>
    </div>
  );
}
