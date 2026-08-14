"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Radar, Activity, BarChart3, Radio, Clock, Terminal,
  RefreshCw, Loader2, Cpu, Zap, TrendingUp, Boxes,
  CheckCircle2, XCircle, AlertTriangle, Pause, Play,
  Server, Brain, Gauge, Layers,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getBackendUrl } from "@/lib/backend-config";
import { getAccessToken } from "@/lib/stores/auth";
import { OpenCodeDisabledCard } from "@/components/learning/OpenCodeDisabledCard";
import {
  LineChart as RLineChart, Line as RLine, BarChart as RBarChart, Bar as RBar,
  PieChart as RPieChart, Pie as RPie, Cell as RCell,
  XAxis, YAxis, Tooltip as RTooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";

// 后端基地址(开发=localhost:8000, 生产=配置的域名)
const BACKEND = getBackendUrl().replace(/\/$/, "");

type Tab = "overview" | "stats" | "decisions" | "scheduler" | "logs";

// ═══════════════════════════════════════════════════════════════════
// 主页面
// ═══════════════════════════════════════════════════════════════════

export default function AgentMonitorPage() {
  const [tab, setTab] = useState<Tab>("overview");

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
    { key: "overview", label: "运行总览", icon: Activity },
    { key: "stats", label: "执行统计", icon: BarChart3 },
    { key: "decisions", label: "决策流", icon: Radio },
    { key: "scheduler", label: "调度监控", icon: Clock },
    { key: "logs", label: "实时日志", icon: Terminal },
  ];

  return (
    <div className="p-4 space-y-4 max-w-7xl mx-auto">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h1 className="text-lg font-bold flex items-center gap-2">
          <Radar className="w-5 h-5 text-primary" />
          Agent 运行监控
        </h1>
        <AccountSessionSelector
          sessions={sessionsList}
          selectedAccountId={selectedAccountId}
          selectedSessionId={selectedSessionId}
          onAccountChange={handleAccountChange}
          onSessionChange={setSelectedSessionId}
        />
      </div>

      {/* Tab 导航 */}
      <div className="flex items-center gap-1 flex-wrap border-b border-border/50 pb-2">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md transition-colors",
              tab === t.key
                ? "bg-primary/15 text-primary font-medium"
                : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
            )}
          >
            <t.icon className="w-3.5 h-3.5" />
            {t.label}
          </button>
        ))}
      </div>

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
        className="h-8 px-2 rounded-md border border-border/60 bg-background text-xs max-w-44"
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
          className="h-8 px-2 rounded-md border border-border/60 bg-background text-xs max-w-52"
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
        <span className="text-[10px] text-muted-foreground">该账户暂无会话</span>
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
  const [tick, setTick] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  const refetch = useCallback(() => setTick(t => t + 1), []);

  useEffect(() => {
    if (!url) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    const load = async () => {
      // 超时中断:防慢API卡死页面(10s上限)
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      const timeout = setTimeout(() => ac.abort(), 10000);
      try {
        const token = getAccessToken();
        const headers: Record<string, string> = { Accept: "application/json" };
        if (token) headers.Authorization = `Bearer ${token}`;
        const res = await fetch(url, { signal: ac.signal, headers });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        if (!cancelled) { setData(json); setError(null); }
      } catch (e) {
        if (!cancelled) {
          const msg = e instanceof Error ? e.message : String(e);
          setError(msg.includes('abort') ? '请求超时' : msg);
        }
      } finally {
        clearTimeout(timeout);
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const id = setInterval(load, interval);
    return () => { cancelled = true; clearInterval(id); abortRef.current?.abort(); };
  }, [url, interval, tick]);

  return { data, loading, error, refetch };
}

// ═══════════════════════════════════════════════════════════════════
// Tab 1: 运行总览
// ═══════════════════════════════════════════════════════════════════

function OverviewTab({ sessionsData, selectedSessionId }: { sessionsData: any[]; selectedSessionId: string | null }) {
  const tickIntervals = usePoll<any>(`${BACKEND}/api/full-auto/tick-intervals`, 30000);

  const runningSession = sessionsData.find((s: any) => s.session_id === selectedSessionId)
    ?? sessionsData.find((s: any) => s.status === "running" || s.status === "defensive");
  const sessionId = runningSession?.session_id ?? selectedSessionId;
  const tierStatus = usePoll<any>(sessionId ? `${BACKEND}/api/full-auto/tier-status/${sessionId}` : null, 10000);
  const scheduler = usePoll<any>(`${BACKEND}/api/full-auto/debug/scheduler-state`, 15000);
  const mltoThesis = usePoll<any>(sessionId ? `${BACKEND}/api/mlto/sessions/${sessionId}/thesis/summary` : null, 15000);
  const autoCoinStatus = usePoll<any>(sessionId ? `${BACKEND}/api/auto-coin/${sessionId}/status` : null, 15000);
  const autoCoinHistory = usePoll<any>(sessionId ? `${BACKEND}/api/auto-coin/${sessionId}/history?limit=10` : null, 30000);
  // 中线因子池状态（因子化新形态的数据源：active/候选/拒绝 + 时间框架分布）
  const midlongFactors = usePoll<any>(`${BACKEND}/api/ops/midlong-factors`, 30000);

  const intervals = tickIntervals.data?.intervals ?? { coordinator: 30, short: 30, mid: 120, long: 240 };
  const tiers = tierStatus.data?.tiers ?? {};
  const jobs = scheduler.data?.jobs ?? [];
  const mltoLanes = mltoThesis.data?.lanes ?? {};
  const longFixedSyms: string[] = (mltoLanes.long_symbols || runningSession?.fixed_symbols_by_tier?.long || [])
    .map((s: string) => String(s).toUpperCase())
    .filter(Boolean);
  const longFixedLabel = longFixedSyms.length
    ? `TrendAgent · MLTO long · 仅 ${longFixedSyms.join("/")}`
    : "TrendAgent · MLTO long · 未配置固定币";
  const totalEquity = tierStatus.data?.total_equity ?? 0;

  // 统一循环跳过次数
  const skipCount = Object.values(scheduler.data?.unified_tick_count ?? {}).reduce((s: number, v: any) => s + (typeof v === "number" ? v : 0), 0);

  // 找到 unified/scalp/midlong 的 next_run
  const findJob = (pattern: string) => jobs.find((j: any) => j.id?.includes(pattern));

  // AI 选币：独立异步循环（非 APScheduler job），用 last_scan_at + 扫描间隔推算下次扫描
  const autoScanInterval = autoCoinStatus.data?.scan_interval ?? 1800;
  const autoLastScan = autoCoinStatus.data?.last_scan_at ?? null;
  const autoNextScanJob = autoLastScan
    ? { next_run: new Date(autoLastScan).getTime() + autoScanInterval * 1000 }
    : null;
  const autoPool = autoCoinStatus.data?.candidate_pool ?? {};
  const autoSymbols = autoCoinStatus.data?.auto_symbols ?? [];
  const mltoTheses = mltoThesis.data?.theses ?? [];
  const mltoLastUpdate = mltoTheses.length
    ? new Date(Math.max(...mltoTheses.map((t: any) => new Date(t.updated_at ?? 0).getTime()))).toLocaleTimeString("zh-CN", { hour12: false })
    : "--";

  const tierCards: {
    key: string; name: string; icon: any; color: string; interval: number;
    label: string; data: any; job: any; badge?: string; countdownLabel?: string;
    stats?: { label: string; value: number }[]; kpiExclude?: boolean;
    showBudget?: boolean; footer?: any;
  }[] = [
    {
      key: "short", name: "短线 Scalp", icon: Zap, color: "primary",
      interval: intervals.short, label: "因子引擎 · 5m",
      data: tiers.short,
      job: findJob("scalp"),
    },
    {
      key: "mid", name: "中线 · 因子化", icon: Boxes, color: "profit",
      interval: intervals.mid ?? intervals.long,
      label: "因子池 mid · 4h/1d · regime/ADX 过滤 → IC 加权",
      data: {
        ...(tiers.mid || {}),
        symbols: tiers.mid?.symbols?.length
          ? tiers.mid.symbols
          : (mltoTheses.filter((t: any) => t.tier === "mid").map((t: any) => t.symbol)),
      },
      job: findJob("midlong"),
      footer: (
        <div className="text-[9px] text-muted-foreground space-y-0.5 pt-1">
          <div>
            因子池：
            active={midlongFactors.data?.health?.active ?? "…"}
            {" · "}候选={midlongFactors.data?.health?.candidate ?? "…"}
            {" · "}拒绝={midlongFactors.data?.health?.rejected ?? "…"}
            {midlongFactors.data?.health?.by_timeframe
              ? ` · 4h=${midlongFactors.data.health.by_timeframe["4h"] ?? 0} / 1d=${midlongFactors.data.health.by_timeframe["1d"] ?? 0}`
              : ""}
            {midlongFactors.data?.health?.avg_active_ic != null
              ? ` · 均|IC|=${midlongFactors.data.health.avg_active_ic}`
              : ""}
          </div>
          <div>宇宙 = 固定 ∪ AI≤3；得分高/低直接执行或否决，仅边缘带问 LLM（fail-closed）</div>
          <div>过渡期：因子研究进行中，MLTO 平行对照；shadow 达标后切因子路由</div>
        </div>
      ),
    },
    {
      key: "long", name: "固定长线", icon: Boxes, color: "warning",
      interval: intervals.long, label: longFixedLabel,
      data: tiers.long,
      job: findJob("midlong"),
      footer: (
        <div className="text-[9px] text-muted-foreground space-y-0.5 pt-1">
          <div>MLTO 长线 · 更新 {mltoLastUpdate}</div>
          <div className="flex gap-1 flex-wrap">
            {mltoTheses.filter((t: any) => t.tier === "long").map((t: any) => (
              <span key={`long-${t.symbol}`} className={cn(
                "px-1 py-0.5 rounded",
                t.direction === "long" ? "bg-profit/10 text-profit" :
                t.direction === "short" ? "bg-loss/10 text-loss" : "bg-muted/30 text-muted-foreground"
              )}>
                {t.symbol} {t.direction === "long" ? "多" : t.direction === "short" ? "空" : "中性"}
              </span>
            ))}
          </div>
        </div>
      ),
    },
    {
      key: "auto", name: "AI 选币", icon: Radar, color: "profit",
      interval: autoScanInterval, badge: `${autoScanInterval}s/跟投`,
      label: `${autoCoinStatus.data?.source_label || "平台看板跟投"} · ${autoCoinStatus.data?.exchange ?? "asterdex"}`,
      countdownLabel: "下次同步",
      data: {
        active_count: autoSymbols.length,
        position_count: 0,
        symbols: autoSymbols,
      },
      stats: [
        { label: "选币", value: autoSymbols.length },
        { label: "冷却", value: autoPool.cooling_count ?? 0 },
        { label: "黑名单", value: autoPool.blacklist_count ?? 0 },
      ],
      job: autoNextScanJob,
      kpiExclude: true,
      showBudget: false,
      footer: (
        <div className="text-[9px] text-muted-foreground space-y-1 pt-1">
          <div className="flex items-center justify-between gap-2">
            <span>上次同步 {autoLastScan ? new Date(autoLastScan).toLocaleTimeString("zh-CN", { hour12: false }) : "--"}</span>
            <span>池 {Object.keys(autoPool.active ?? {}).length} · 历史 {autoCoinHistory.data?.total ?? 0}</span>
          </div>
          {autoCoinStatus.data?.inject_blocked_reason && (
            <div className="text-warning/90 truncate" title={autoCoinStatus.data.inject_blocked_reason}>
              注入受限：{autoCoinStatus.data.inject_blocked_reason}
            </div>
          )}
          <div>
            <div className="mb-0.5">当前选币 {autoSymbols.length} 个（来自 VIP 短线看板）</div>
            {autoSymbols.length > 0 ? (
              <div className="flex gap-1 flex-wrap">
                {autoSymbols.map((sym: string) => (
                  <span
                    key={sym}
                    className="px-1 py-0.5 rounded bg-profit/10 text-profit font-medium"
                    title={sym}
                  >
                    {sym}
                  </span>
                ))}
              </div>
            ) : (
              <div className="text-muted-foreground/80">暂无选出交易对（等待看板同步）</div>
            )}
          </div>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={() => { tickIntervals.refetch(); tierStatus.refetch(); scheduler.refetch(); autoCoinStatus.refetch(); autoCoinHistory.refetch(); }}>
          <RefreshCw className="w-3.5 h-3.5" /> 刷新
        </Button>
      </div>

      {/* 顶部状态条：OpenCode 路由已注释，禁止 404→假绿灯 */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <StatusPill label="后端" ok={true} detail=":8000 运行中" />
        <StatusPill label="会话" ok={!!runningSession} detail={runningSession ? `${runningSession.account_name} · ${runningSession.status}${runningSession.active_exchange ? ` · ${runningSession.active_exchange}` : ""}` : "无活跃会话"} />
        <StatusPill label="调度任务" ok={jobs.length > 0} detail={`${jobs.length} 个 job`} />
      </div>
      <OpenCodeDisabledCard />

      {/* 三周期卡片 */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {tierCards.map((t) => {
          const d = t.data ?? {};
          const budgetPct = d.budget_max ? (d.margin_used / d.budget_max) * 100 : 0;
          return (
            <Card key={t.key} className="p-4 space-y-2 relative overflow-hidden">
              {/* 装饰光效 */}
              <div className={cn("absolute top-0 right-0 w-20 h-20 rounded-full blur-3xl opacity-10",
                t.color === "primary" ? "bg-primary" : t.color === "profit" ? "bg-profit" : "bg-warning")} />
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className={cn("w-8 h-8 rounded-lg flex items-center justify-center",
                    t.color === "primary" ? "bg-primary/10" : t.color === "profit" ? "bg-profit/10" : "bg-warning/10")}>
                    <t.icon className={cn("w-4 h-4", t.color === "primary" ? "text-primary" : t.color === "profit" ? "text-profit" : "text-warning")} />
                  </div>
                  <div>
                    <div className="text-sm font-medium">{t.name}</div>
                    <div className="text-[10px] text-muted-foreground">{t.label}</div>
                  </div>
                </div>
                <Badge variant="secondary" className="text-[9px] tabular-nums">{t.badge ?? `${t.interval}s/tick`}</Badge>
              </div>

              {/* 下次 tick 倒计时 */}
              <NextTickCountdown job={t.job} interval={t.interval} label={t.countdownLabel} />

              {/* 统计 */}
              <div className="grid grid-cols-3 gap-2 pt-1">
                {t.stats ? t.stats.map((s) => <Stat key={s.label} label={s.label} value={s.value} />) : (
                  <>
                    <Stat label="策略" value={d.active_count ?? d.strategy_count ?? 0} />
                    <Stat label="持仓" value={d.position_count ?? 0} />
                    <Stat label="符号" value={d.symbols?.length ?? 0} />
                  </>
                )}
              </div>

              {/* 预算进度 */}
              {t.showBudget !== false && (
                <div className="space-y-1">
                  <div className="flex justify-between text-[10px] text-muted-foreground">
                    <span>预算占用</span>
                    <span className="tabular-nums">{budgetPct.toFixed(1)}%</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-muted/30 overflow-hidden">
                    <div className={cn("h-full transition-all rounded-full",
                      budgetPct > 80 ? "bg-loss" : budgetPct > 50 ? "bg-warning" : "bg-profit")}
                      style={{ width: `${Math.min(budgetPct, 100)}%` }} />
                  </div>
                </div>
              )}
              {t.footer}
            </Card>
          );
        })}
      </div>

      {/* 全局 KPI */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <KPICard label="总权益" value={`$${totalEquity.toFixed(2)}`} icon={Gauge} />
        <KPICard label="活跃策略" value={tierCards.filter((t: any) => !t.kpiExclude).reduce((s, t) => s + ((t.data?.active_count ?? 0) as number), 0)} icon={Layers} />
        <KPICard label="总持仓" value={tierCards.filter((t: any) => !t.kpiExclude).reduce((s, t) => s + ((t.data?.position_count ?? 0) as number), 0)} icon={Activity} />
        <KPICard label="调度任务" value={jobs.length} icon={Clock} />
        <KPICard label="报错中心" value="→ 运维台" icon={AlertTriangle} color="text-muted-foreground" />
      </div>

      {/* 分周期策略活动摘要（开仓/平仓/减仓记录） */}
      <TierActivityPanels sessionId={sessionId} />

      {/* 协调器状态 */}
      <Card className="p-4">
        <div className="flex items-center justify-between mb-3">
          <span className="text-sm font-medium flex items-center gap-1.5">
            <Cpu className="w-4 h-4 text-primary" /> 协调器 / 统一循环
          </span>
          <Badge variant="secondary" className="text-[10px]">tick 跳过 {skipCount}</Badge>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-2 gap-3 text-xs">
          <Detail label="协调器间隔" value={`${intervals.coordinator}s`} />
          <Detail label="统一循环" value={scheduler.data?.unified_loop_running ? "运行中" : "—"} />
        </div>
      </Card>

      {/* 中长线：长线=纯AI（TrendAgent 唯一通道）；中线=因子化（固定∪AI≤3，过渡期 MLTO 对照） */}
      <Card className="p-4">
        <div className="flex items-center justify-between mb-3">
          <span className="text-sm font-medium flex items-center gap-1.5">
            <Brain className="w-4 h-4 text-warning" /> 中长线 · 中线因子化（过渡期 MLTO 对照）
          </span>
          <Badge variant="secondary" className="text-[10px]">
            {mltoThesis.data?.theses?.length ?? 0} 个 thesis
          </Badge>
        </div>
        {(() => {
          const all = mltoThesis.data?.theses ?? [];
          const lanes = mltoThesis.data?.lanes ?? {};
          const longRows = all.filter((t: any) => t.tier === "long");
          const midRows = all.filter((t: any) => t.tier === "mid");
          const fixedSet = new Set<string>(
            (lanes.fixed_in_mid || []).map((s: string) => String(s).toUpperCase()),
          );
          const aiMidSet = new Set<string>(
            (lanes.ai_mid_symbols || []).map((s: string) => String(s).toUpperCase()),
          );
          const midFixedRows = midRows.filter((t: any) => fixedSet.has(String(t.symbol || "").toUpperCase()));
          const midAiRows = midRows.filter((t: any) => {
            const s = String(t.symbol || "").toUpperCase();
            return aiMidSet.has(s) && !fixedSet.has(s);
          });
          const midOtherRows = midRows.filter((t: any) => {
            const s = String(t.symbol || "").toUpperCase();
            return !fixedSet.has(s) && !aiMidSet.has(s);
          });
          const renderRow = (t: any, i: number, tag?: string) => (
            <div key={`${t.tier}-${t.symbol}-${tag || ""}-${i}`} className="grid grid-cols-12 gap-2 items-center text-xs py-1.5 border-b border-border/10 last:border-0">
              <div className="col-span-2 font-medium">
                {t.symbol}
                {tag ? <span className="ml-1 text-[9px] text-muted-foreground">{tag}</span> : null}
              </div>
              <div className="col-span-1">
                <span className={cn("text-[10px] px-1.5 py-0.5 rounded",
                  t.direction === "long" ? "bg-profit/10 text-profit" :
                  t.direction === "short" ? "bg-loss/10 text-loss" :
                  "bg-muted/30 text-muted-foreground")}>
                  {t.direction === "long" ? "多" : t.direction === "short" ? "空" : "中性"}
                </span>
              </div>
              <div className="col-span-1 text-muted-foreground">{t.tier === "long" ? "长线" : "中线"}</div>
              <div className="col-span-2 text-muted-foreground tabular-nums">
                conv={t.llm_conviction ?? 0}
              </div>
              <div className="col-span-2 text-muted-foreground tabular-nums">
                hub={t.hub_adjusted ? (t.hub_adjusted * 100).toFixed(0) : 0}%
              </div>
              <div className="col-span-2 text-muted-foreground tabular-nums">
                ready={t.open_readiness ?? 0}
              </div>
              <div className="col-span-2 min-w-0">
                <span
                  className={cn("text-[10px] block truncate",
                    t.gate_status?.can_open ? "text-profit" : "text-muted-foreground")}
                  title={t.gate_status?.summary ?? ""}
                >
                  {(() => {
                    const gs = t.gate_status;
                    if (!gs) return t.pending ? "等待调度" : "—";
                    if (gs.can_open) return "可开仓";
                    const pending = (gs.checks || [])
                      .filter((c: any) => !c.ok)
                      .map((c: any) => c.label)
                      .filter(Boolean);
                    if (pending.length) return `还需: ${pending.slice(0, 2).join(" · ")}`;
                    return gs.summary || "—";
                  })()}
                </span>
              </div>
            </div>
          );
          if (!all.length) {
            return (
              <div className="text-xs text-muted-foreground py-4 text-center">
                {mltoThesis.loading ? "加载中..." : "暂无 MLTO thesis（等待调度）"}
              </div>
            );
          }
          return (
            <div className="space-y-3">
              <div>
                <div className="text-[10px] text-muted-foreground mb-1">
                  固定长线 · {longRows.length} · {(lanes.long_symbols || []).join("/") || "未配置"}
                </div>
                {longRows.length ? longRows.map((t: any, i: number) => renderRow(t, i)) : (
                  <div className="text-[10px] text-muted-foreground py-1">暂无长线 thesis</div>
                )}
              </div>
              <div>
                <div className="text-[10px] text-muted-foreground mb-1">
                  中线（过渡期 MLTO 对照） · {midRows.length}
                  {" · 固定 "}{(lanes.fixed_in_mid || []).join("/") || "—"}
                  {" + AI≤3 "}{(lanes.ai_mid_symbols || []).join("/") || "—"}
                </div>
                {midFixedRows.length ? midFixedRows.map((t: any, i: number) => renderRow(t, i, "固定")) : null}
                {midAiRows.length ? midAiRows.map((t: any, i: number) => renderRow(t, i, "AI")) : null}
                {midOtherRows.length ? midOtherRows.map((t: any, i: number) => renderRow(t, i, "续管")) : null}
                {!midRows.length ? (
                  <div className="text-[10px] text-muted-foreground py-1">暂无中线 thesis</div>
                ) : null}
              </div>
            </div>
          );
        })()}
      </Card>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// 分周期策略活动摘要（开仓/平仓/减仓记录）
// ═══════════════════════════════════════════════════════════════════

function TierActivityPanels({ sessionId }: { sessionId?: string }) {
  const { data } = usePoll<any>(sessionId ? `${BACKEND}/api/full-auto/tier-activity/${sessionId}` : null, 10000);
  const acts = data ?? { short: [], mid: [], long: [] };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
      <TierActivityColumn title="短线" items={acts.short ?? []} color="primary" icon={Zap} />
      <TierActivityColumn title="中线(因子化)" items={acts.mid ?? []} color="profit" icon={Boxes} />
      <TierActivityColumn title="固定长线" items={acts.long ?? []} color="warning" icon={Boxes} />
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
          <Badge variant="secondary" className="text-[9px]">{items.length}</Badge>
          <button onClick={() => setPaused(!paused)} className="text-muted-foreground hover:text-foreground">
            {paused ? <Play className="w-3 h-3" /> : <Pause className="w-3 h-3" />}
          </button>
        </div>
      </div>
      <div ref={scrollRef} className="text-[10px] h-[300px] overflow-y-auto bg-black/20 rounded p-1.5 space-y-0.5">
        {items.length === 0 ? (
          <div className="text-muted-foreground text-center py-4 text-[11px]">暂无{title}策略记录</div>
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
                  <span className={cn("shrink-0 text-[9px]",
                    item.direction === "long" ? "text-profit" : "text-loss")}>
                    {item.direction === "long" ? "多" : "空"}
                  </span>
                )}
                {item.lane_note && (
                  <span className="text-warning shrink-0 text-[9px]">{item.lane_note}</span>
                )}
                {item.confidence > 0 && (
                  <span className="text-muted-foreground shrink-0 tabular-nums">{item.confidence}%</span>
                )}
                {isBlocked && item.block_reason && (
                  <span className="text-loss text-[9px] truncate" title={item.block_reason}>
                    {item.block_reason}
                  </span>
                )}
                {isExecuted && (
                  <span className="text-profit text-[9px] shrink-0">已执行</span>
                )}
                {item.reasoning && (
                  <span className="text-muted-foreground text-[9px] truncate ml-auto" title={item.reasoning}>
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
    const calc = () => {
      if (job?.next_run) {
        const next = new Date(job.next_run).getTime();
        const diff = Math.max(0, Math.floor((next - Date.now()) / 1000));
        setRemaining(diff);
      } else {
        setRemaining(interval);
      }
    };
    calc();
    const id = setInterval(calc, 1000);
    return () => clearInterval(id);
  }, [job?.next_run, interval]);

  const pct = interval > 0 ? ((interval - remaining) / interval) * 100 : 0;
  return (
    <div className="flex items-center gap-2 text-[10px]">
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
          <div className="text-sm font-medium mb-3">Tick 耗时分布 (ms)</div>
          {barData.length === 0 ? (
            <EmptyChart />
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <RBarChart data={barData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1E2530" />
                <XAxis dataKey="name" tick={{ fill: "#6B7785", fontSize: 10 }} />
                <YAxis tick={{ fill: "#6B7785", fontSize: 10 }} />
                <RTooltip contentStyle={{ background: "#11161D", border: "1px solid #1E2530", borderRadius: 6, fontSize: 12 }} />
                <RBar dataKey="p50" fill="#5B8DEF" radius={[3, 3, 0, 0]} name="P50" />
                <RBar dataKey="p95" fill="#FFB938" radius={[3, 3, 0, 0]} name="P95" />
              </RBarChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card className="p-4">
          <div className="text-sm font-medium mb-3">成功率 (%)</div>
          {barData.length === 0 ? (
            <EmptyChart />
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <RLineChart data={barData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1E2530" />
                <XAxis dataKey="name" tick={{ fill: "#6B7785", fontSize: 10 }} />
                <YAxis domain={[0, 100]} tick={{ fill: "#6B7785", fontSize: 10 }} />
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
          <div className="text-sm font-medium mb-3">决策分布 ({allDecisions.length})
            {selectedAccountId != null && <Badge variant="secondary" className="ml-1.5 text-[9px]">账户 {selectedAccountId}</Badge>}
          </div>
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
              <div key={p.name} className="flex items-center gap-1 text-[10px]">
                <div className="w-2 h-2 rounded-full" style={{ background: p.color }} />
                <span className="text-muted-foreground">{p.name}</span>
                <span className="tabular-nums font-medium">{p.value}</span>
              </div>
            ))}
          </div>
        </Card>

        <Card className="p-4">
          <div className="text-sm font-medium mb-3">Agent 绩效 (7天)
            <Badge variant="secondary" className="ml-1.5 text-[9px]">全局</Badge>
          </div>
          <div className="space-y-2">
            {Object.entries(agents).map(([name, a]: [string, any]) => (
              <div key={name} className="p-2 rounded bg-muted/10 text-xs space-y-1">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{name}</span>
                  <span className={cn("tabular-nums font-medium", (a.net_pnl ?? 0) >= 0 ? "text-profit" : "text-loss")}>
                    {(a.net_pnl ?? 0) >= 0 ? "+" : ""}${(a.net_pnl ?? 0).toFixed(2)}
                  </span>
                </div>
                <div className="flex gap-3 text-[10px] text-muted-foreground">
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
        <div className="px-4 py-2 border-b border-border/50 text-sm font-medium">Tick 执行明细</div>
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
    if (filter === "long") return r.includes("MLTO") || r.includes("TrendAgent") || r.includes("长线") || r.includes("scalp");
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
              className={cn("px-2 py-1 text-[10px] rounded",
                filter === f ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-muted/30")}>
              {f === "all" ? "全部" : f === "long" ? "长线" : f === "buy" ? "买入" : "卖出"}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-profit">买 {stats.buy}</span>
          <span className="text-[10px] text-loss">卖 {stats.sell}</span>
          <span className="text-[10px] text-muted-foreground">观望 {stats.hold}</span>
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
              <div key={d.id || i} className="px-4 py-2.5 hover:bg-muted/10">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-[10px] text-muted-foreground font-mono tabular-nums shrink-0">
                    {d.created_at ? new Date(d.created_at).toLocaleTimeString("zh-CN", { hour12: false }) : "--"}
                  </span>
                  <span className="text-xs font-bold shrink-0">{d.symbol}</span>
                  <Badge className={cn("text-[9px] shrink-0",
                    isBuy ? "bg-profit/20 text-profit" : isSell ? "bg-loss/20 text-loss" : "bg-muted text-muted-foreground")}>
                    {isBuy ? "买入" : isSell ? "卖出" : "观望"}
                  </Badge>
                  {d.target_portion > 0 && (
                    <span className="text-[10px] text-muted-foreground tabular-nums">目标 {(d.target_portion * 100).toFixed(0)}%</span>
                  )}
                  {d.executed ? (
                    <Badge variant="outline" className="text-[9px] text-profit border-profit/30">已执行</Badge>
                  ) : null}
                </div>
                {d.reasoning && <p className="text-[11px] text-muted-foreground line-clamp-2 pl-1">{d.reasoning}</p>}
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
  const intervals = tickIntervals.data?.intervals ?? { short: 30, mid: 120, long: 240 };
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
          <Badge variant={running ? "default" : "secondary"} className={cn("text-[10px]", running && "bg-profit/20 text-profit")}>
            {running ? "运行中" : "已停止"}
          </Badge>
        </div>
        <Button variant="outline" size="sm" onClick={refetch}>
          <RefreshCw className="w-3.5 h-3.5" /> 刷新
        </Button>
      </div>

      {/* 运行中的会话（多账户，高亮当前所选） */}
      <Card className="p-4">
        <div className="text-sm font-medium mb-2">运行中的会话 ({runningSessions.length})</div>
        <div className="flex gap-1.5 flex-wrap">
          {runningSessions.length === 0 && <div className="text-xs text-muted-foreground">无</div>}
          {runningSessions.map((sid: string) => (
            <span key={sid} className={cn("px-2 py-1 rounded text-[10px] font-mono",
              sid === selectedSessionId ? "bg-primary/15 text-primary font-medium" : "bg-muted/20 text-muted-foreground")}>
              {sid}
            </span>
          ))}
        </div>
      </Card>

      {/* 两周期 tick 配置 vs 实际 */}
      <Card className="p-4">
        <div className="text-sm font-medium mb-3">Tick 间隔</div>
        <div className="grid grid-cols-2 gap-3">
          {[
            { label: "短线", val: intervals.short, color: "text-primary" },
            { label: "AI中线", val: intervals.mid ?? intervals.long, color: "text-profit" },
            { label: "固定长线", val: intervals.long, color: "text-warning" },
          ].map(t => (
            <div key={t.label} className="text-center p-3 rounded-lg bg-muted/10">
              <div className="text-[10px] text-muted-foreground mb-1">{t.label}</div>
              <div className={cn("text-2xl font-bold tabular-nums", t.color)}>{t.val}</div>
              <div className="text-[10px] text-muted-foreground">秒/tick</div>
            </div>
          ))}
        </div>
      </Card>

      {/* FullAuto 任务 */}
      <Card className="p-0 overflow-hidden">
        <div className="px-4 py-2 border-b border-border/50 text-sm font-medium">FullAuto 调度任务</div>
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
                {isOverdue && <Badge className="bg-warning/20 text-warning text-[9px]">超时</Badge>}
              </div>
            );
          })}
          {fullAutoJobs.length === 0 && <div className="py-4 text-center text-xs text-muted-foreground">暂无 FullAuto 任务</div>}
        </div>
      </Card>

      {/* 其他系统任务 */}
      <Card className="p-0 overflow-hidden">
        <div className="px-4 py-2 border-b border-border/50 text-sm font-medium">系统调度任务 ({otherJobs.length})</div>
        <div className="divide-y divide-border/20 max-h-60 overflow-y-auto">
          {otherJobs.map((j: any) => {
            const nextRun = j.next_run ? new Date(j.next_run) : null;
            return (
              <div key={j.id} className="flex items-center gap-3 px-4 py-1.5 text-xs">
                <Clock className="w-3 h-3 text-muted-foreground shrink-0" />
                <span className="font-mono flex-1 truncate text-muted-foreground">{j.id}</span>
                <span className="text-[10px] text-muted-foreground tabular-nums shrink-0">
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
          <div className="text-sm font-medium mb-2">统一循环状态</div>
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
// Tab 5: 实时日志
// ═══════════════════════════════════════════════════════════════════

function LogsTab() {
  const levelParam = "WARNING";
  const { data, loading, refetch } = usePoll<any>(
    `${BACKEND}/api/system-logs/?limit=200&level=${levelParam}`,
    15000,
  );
  const [paused, setPaused] = useState(false);
  const [levelFilter, setLevelFilter] = useState<string>("all");
  const logEndRef = useRef<HTMLDivElement>(null);

  const rawLogs = Array.isArray(data?.logs) ? data.logs : [];
  const lines = rawLogs
    .map((l: any) => {
      const lvl = String(l.level || l.severity || "").toUpperCase();
      const msg = String(l.message || l.msg || l.detail || JSON.stringify(l));
      const ts = l.timestamp || l.created_at || "";
      return { lvl, text: `${ts} [${lvl}] ${msg}` };
    })
    .filter((l: { lvl: string }) => {
      if (levelFilter === "all") return true;
      if (levelFilter === "error") return l.lvl.includes("ERROR") || l.lvl.includes("CRITICAL");
      if (levelFilter === "warn") return l.lvl.includes("WARN");
      if (levelFilter === "info") return l.lvl.includes("INFO");
      return true;
    });

  useEffect(() => {
    if (!paused && logEndRef.current) {
      logEndRef.current.scrollTop = 0;
    }
  }, [lines, paused]);

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-border/60 bg-muted/10 px-3 py-2 text-xs text-muted-foreground">
        OpenCode 日志接口已停用。此处读取 <code className="text-[10px]">/api/system-logs</code>（系统级日志，跨账户）；完整分级报错见{" "}
        <a href="/ops#ops-errors" className="text-primary underline underline-offset-2">运维台 · 报错中心</a>。
      </div>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Button size="sm" variant={paused ? "default" : "outline"} onClick={() => setPaused(!paused)}>
            {paused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
            {paused ? "继续滚屏" : "暂停"}
          </Button>
          <div className="flex items-center gap-1">
            {["all", "info", "warn", "error"].map(l => (
              <button key={l} onClick={() => setLevelFilter(l)}
                className={cn("px-2 py-1 text-[10px] rounded",
                  levelFilter === l ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-muted/30")}>
                {l === "all" ? "全部" : l === "info" ? "INFO" : l === "warn" ? "WARN" : "ERROR"}
              </button>
            ))}
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={refetch} disabled={loading}>
          <RefreshCw className={cn("w-3 h-3", loading && "animate-spin")} />
        </Button>
      </div>

      <Card className="p-0 overflow-hidden">
        <div ref={logEndRef} className="font-mono text-[10px] h-[500px] overflow-y-auto bg-black/30 p-3 leading-relaxed">
          {lines.length === 0 ? (
            <div className="text-muted-foreground text-center py-8">暂无系统日志（或级别过滤过严）</div>
          ) : (
            lines.map((line: { text: string; lvl: string }, i: number) => {
              const isErr = line.lvl.includes("ERROR") || line.lvl.includes("CRITICAL");
              const isWarn = line.lvl.includes("WARN");
              return (
                <div key={i} className={cn(
                  "py-0.5 px-1 hover:bg-muted/10",
                  isErr ? "text-loss" : isWarn ? "text-warning" : "text-muted-foreground"
                )}>
                  {line.text}
                </div>
              );
            })
          )}
        </div>
      </Card>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// 通用组件
// ═══════════════════════════════════════════════════════════════════

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

function StatusPill({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    <Card className="p-2.5 flex items-center gap-2">
      {ok ? <CheckCircle2 className="w-4 h-4 text-profit shrink-0" /> : <XCircle className="w-4 h-4 text-loss shrink-0" />}
      <div className="min-w-0">
        <div className="text-[10px] text-muted-foreground">{label}</div>
        <div className="text-xs font-medium truncate">{detail}</div>
      </div>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="text-center p-1.5 rounded bg-muted/10">
      <div className="text-sm font-bold tabular-nums">{value}</div>
      <div className="text-[9px] text-muted-foreground">{label}</div>
    </div>
  );
}

function KPICard({ label, value, icon: Icon, color }: { label: string; value: any; icon: any; color?: string }) {
  return (
    <Card className="p-3 flex items-center gap-2">
      <Icon className={cn("w-4 h-4", color ?? "text-primary")} />
      <div className="min-w-0">
        <div className={cn("text-base font-bold tabular-nums truncate", color)}>{value}</div>
        <div className="text-[10px] text-muted-foreground">{label}</div>
      </div>
    </Card>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] text-muted-foreground mb-0.5">{label}</div>
      <div className="text-xs font-medium truncate">{value}</div>
    </div>
  );
}
