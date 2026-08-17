"use client";

/**
 * Agent 运行时监控 Tab
 * 对接后端 /api/monitor/agents/* 端点
 * 展示9个Agent的运行状态、健康度、LLM token、执行频次、实时日志
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Bot, Activity, RefreshCw, Loader2, Pause, Play,
  CheckCircle2, XCircle, AlertTriangle, Cpu, Zap,
  TrendingUp, Gauge, Terminal, RotateCcw,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  BarChart as RBarChart, Bar as RBar,
  XAxis, YAxis, Tooltip as RTooltip, ResponsiveContainer, CartesianGrid,
} from "recharts";
import { getAccessToken } from "@/lib/stores/auth";

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const token = getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}
// ═══════════════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════════════

interface AgentOverview {
  agent_id: string;
  display_name: string;
  llm_level: string;
  status: string;
  health_score: number;
  call_count: number;
  success_count: number;
  failure_count: number;
  success_rate: number;
  timeout_count: number;
  circuit_breaker_state: string;
  circuit_breaker_failures: number;
  last_exec_ts: number;
  last_exec_ago_sec: number | null;
  last_exec_duration_ms: number;
  log_count: number;
  llm_total_tokens: number;
  llm_prompt_tokens: number;
  llm_completion_tokens: number;
}

interface OverviewResponse {
  status: string;
  agents: AgentOverview[];
  total_agents: number;
  healthy: number;
  warning: number;
  critical: number;
  uptime_seconds: number;
}

interface FrequencyResponse {
  agents: string[];
  hours: string[];
  matrix: Record<string, number[]>;
  total_calls: Record<string, number>;
}

interface LogEntry {
  ts: string;
  agent_id: string;
  level: string;
  message: string;
}

interface LogsResponse {
  logs: LogEntry[];
  total: number;
}

// ═══════════════════════════════════════════════════════════════════
// 通用轮询 Hook
// ═══════════════════════════════════════════════════════════════════

function usePoll<T>(
  url: string | null,
  interval: number
): { data: T | null; loading: boolean; error: string | null; refetch: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const refetch = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!url) return;
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch(url, { headers: authHeaders() });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json = await res.json();
        if (!cancelled) {
          setData(json);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const id = setInterval(load, interval);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [url, interval, tick]);

  return { data, loading, error, refetch };
}

// ═══════════════════════════════════════════════════════════════════
// 主组件
// ═══════════════════════════════════════════════════════════════════

const AGENT_COLORS: Record<string, string> = {
  market_data: "#5B8DEF",
  factor_engine: "#00C896",
  intel_signal: "#FFB938",
  risk_control: "#FF4D6D",
  mt_orchestrator: "#A78BFA",
  master_controller: "#F472B6",
  trade_execution: "#22D3EE",
  signal_bus: "#FB923C",
  genetic_optimizer: "#34D399",
};

export function AgentRuntimeTab() {
  const overview = usePoll<OverviewResponse>("/api/monitor/agents/overview", 10000);
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);

  const handleReset = async (agentId: string) => {
    try {
      await fetch(`/api/monitor/agents/${agentId}/reset`, {
        method: "POST",
        headers: authHeaders(),
      });
      overview.refetch();
    } catch (e) {
      console.error("Reset failed:", e);
    }
  };

  if (overview.loading && !overview.data) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (overview.error && !overview.data) {
    return (
      <Card className="p-8 text-center">
        <AlertTriangle className="w-8 h-8 text-warning mx-auto mb-2" />
        <div className="text-sm text-muted-foreground">
          后端监控 API 未就绪：{overview.error}
        </div>
        <div className="text-xs text-muted-foreground mt-1">
          请确认后端已启动，且 /api/monitor/agents/overview 可访问
        </div>
      </Card>
    );
  }

  const agents = overview.data?.agents ?? [];
  const activeAgents = agents.filter((a) => a.status !== "idle");
  const totalLogs = agents.reduce((s, a) => s + (a.log_count || 0), 0);
  const totalErrors = agents.reduce((s, a) => s + a.failure_count, 0);
  const totalTokens = agents.reduce((s, a) => s + (a.llm_total_tokens || 0), 0);
  const uptime = overview.data?.uptime_seconds ?? 0;
  const uptimeStr =
    uptime > 3600 ? `${Math.floor(uptime / 3600)}h ${Math.floor((uptime % 3600) / 60)}m` : `${Math.floor(uptime / 60)}m`;

  return (
    <div className="space-y-3">
      {/* 顶部工具栏 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Badge variant="secondary" className="text-[10px]">
            <Bot className="w-3 h-3 mr-1" /> {agents.length} Agents
          </Badge>
          <span className="text-[10px] text-muted-foreground">运行 {uptimeStr}</span>
        </div>
        <Button variant="outline" size="sm" onClick={overview.refetch}>
          <RefreshCw className={cn("w-3.5 h-3.5", overview.loading && "animate-spin")} /> 刷新
        </Button>
      </div>

      {/* 全局 KPI */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KpiCard label="活跃/总" value={`${activeAgents.length}/${agents.length}`} icon={Activity} color="text-primary" />
        <KpiCard label="总日志" value={totalLogs} icon={TrendingUp} color="text-primary" />
        <KpiCard label="LLM Tokens" value={totalTokens.toLocaleString()} icon={Cpu} color="text-primary" />
        <KpiCard label="总错误" value={totalErrors} icon={XCircle} color={totalErrors > 0 ? "text-loss" : "text-muted-foreground"} />
      </div>

      {/* 9 个 Agent 状态卡片 */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {agents.map((agent) => (
          <AgentCard
            key={agent.agent_id}
            agent={agent}
            onReset={() => handleReset(agent.agent_id)}
          />
        ))}
        {agents.length === 0 && (
          <Card className="p-8 text-center col-span-full">
            <Bot className="w-8 h-8 text-muted-foreground mx-auto mb-2" />
            <div className="text-sm text-muted-foreground">
              暂无 Agent 运行时数据（等待后端 Agent 首次调用后自动填充）
            </div>
          </Card>
        )}
      </div>

      {/* 执行频次图表 */}
      <FrequencyChart selectedAgent={selectedAgent} onSelect={setSelectedAgent} />

      {/* Agent 运行日志 */}
      <AgentLogsPanel agentFilter={selectedAgent} onFilterChange={setSelectedAgent} />
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Agent 状态卡片
// ═══════════════════════════════════════════════════════════════════

function AgentCard({ agent, onReset }: { agent: AgentOverview; onReset: () => void }) {
  const color = AGENT_COLORS[agent.agent_id] ?? "#94A1BC";
  const statusColor =
    agent.status === "running" ? "text-primary" :
    agent.status === "error" ? "text-loss" :
    agent.status === "stopped" ? "text-muted-foreground" :
    "text-profit";

  const healthColor =
    agent.health_score >= 80 ? "#00C896" :
    agent.health_score >= 60 ? "#FFB938" :
    "#FF4D6D";

  const cbColor =
    agent.circuit_breaker_state === "closed" ? "text-profit" :
    agent.circuit_breaker_state === "half_open" ? "text-warning" :
    "text-loss";

  // 健康度环形（SVG circle）
  const ringRadius = 16;
  const ringCircumference = 2 * Math.PI * ringRadius;
  const ringOffset = ringCircumference * (1 - agent.health_score / 100);

  return (
    <Card className="p-3 space-y-2 relative overflow-hidden">
      <div
        className="absolute top-0 right-0 w-16 h-16 rounded-full blur-2xl opacity-10"
        style={{ background: color }}
      />
      {/* 头部 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
            style={{ background: `${color}1A` }}
          >
            <Bot className="w-4 h-4" style={{ color }} />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-medium truncate">{agent.display_name}</div>
            <div className="text-[9px] text-muted-foreground font-mono">{agent.agent_id}</div>
          </div>
        </div>
        {/* 健康度环 */}
        <div className="relative w-10 h-10 flex-shrink-0">
          <svg className="w-10 h-10 -rotate-90" viewBox="0 0 40 40">
            <circle cx="20" cy="20" r={ringRadius} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="3" />
            <circle
              cx="20" cy="20" r={ringRadius} fill="none" stroke={healthColor}
              strokeWidth="3" strokeLinecap="round"
              strokeDasharray={ringCircumference}
              strokeDashoffset={ringOffset}
              className="transition-all duration-500"
            />
          </svg>
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-[10px] font-bold tabular-nums">{Math.round(agent.health_score)}</span>
          </div>
        </div>
      </div>

      {/* 状态行 */}
      <div className="flex items-center gap-2 text-[10px]">
        <span className={cn("flex items-center gap-1 font-medium", statusColor)}>
          <span className={cn("w-1.5 h-1.5 rounded-full", statusColor.replace("text-", "bg-"))} />
          {agent.status === "idle" ? "空闲" : agent.status === "running" ? "运行中" : agent.status === "error" ? "错误" : "已停止"}
        </span>
        <span className="text-muted-foreground">·</span>
        <Badge variant="outline" className={cn("text-[8px]", cbColor)}>
          CB: {agent.circuit_breaker_state === "closed" ? "关闭" : agent.circuit_breaker_state === "half_open" ? "半开" : "开启"}
        </Badge>
        {agent.llm_level !== "NONE" && (
          <Badge variant="secondary" className="text-[8px]">{agent.llm_level}</Badge>
        )}
        <span className="text-muted-foreground ml-auto">
          {agent.last_exec_ago_sec != null ? `${Math.round(agent.last_exec_ago_sec)}s前` : "—"}
        </span>
      </div>

      {/* 统计 */}
      <div className="grid grid-cols-4 gap-1.5">
        <MiniStat label="活动" value={agent.log_count} />
        <MiniStat
          label="成功率"
          value={agent.success_rate < 0 ? "—" : `${Math.round(agent.success_rate * 100)}%`}
          color={agent.success_rate < 0 ? "text-muted-foreground" : agent.success_rate >= 0.95 ? "text-profit" : agent.success_rate >= 0.8 ? "text-warning" : "text-loss"}
        />
        <MiniStat label="失败" value={agent.failure_count} color={agent.failure_count > 0 ? "text-loss" : ""} />
        <MiniStat label="超时" value={agent.timeout_count} color={agent.timeout_count > 0 ? "text-warning" : ""} />
      </div>

      {/* LLM token */}
      {agent.llm_total_tokens > 0 && (
        <div className="flex items-center gap-2 text-[9px] text-muted-foreground pt-1 border-t border-border/20">
          <Cpu className="w-3 h-3" />
          <span className="tabular-nums">{agent.llm_total_tokens.toLocaleString()} tokens</span>
          <span>·</span>
          <span className="tabular-nums">P:{agent.llm_prompt_tokens.toLocaleString()} C:{agent.llm_completion_tokens.toLocaleString()}</span>
        </div>
      )}

      {/* 重置按钮 */}
      {(agent.failure_count > 0 || agent.circuit_breaker_state !== "closed") && (
        <Button
          variant="ghost"
          size="sm"
          className="w-full h-6 text-[10px]"
          onClick={onReset}
        >
          <RotateCcw className="w-3 h-3" /> 重置错误状态
        </Button>
      )}
    </Card>
  );
}

// ═══════════════════════════════════════════════════════════════════
// 执行频次图表
// ═══════════════════════════════════════════════════════════════════

function FrequencyChart({
  selectedAgent,
  onSelect,
}: {
  selectedAgent: string | null;
  onSelect: (agent: string | null) => void;
}) {
  const { data, loading } = usePoll<FrequencyResponse>("/api/monitor/agents/frequency?hours=24", 60000);

  const apiHours = data?.hours ?? [];
  const apiMatrix = data?.matrix ?? {};
  const agentIds = data?.agents ?? [];

  // 将矩阵格式转换为 recharts 需要的行式数据
  const chartData = apiHours.map((h, i) => {
    const row: Record<string, number | string> = {
      hour: h.slice(11, 16), // HH:mm
    };
    let total = 0;
    for (const aid of agentIds) {
      const counts = apiMatrix[aid];
      const val = Array.isArray(counts) ? counts[i] ?? 0 : 0;
      row[aid] = val;
      total += val;
    }
    row.total = total;
    return row;
  });

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-medium flex items-center gap-1.5">
          <TrendingUp className="w-4 h-4 text-primary" /> 24h 执行频次
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={() => onSelect(null)}
            className={cn(
              "px-2 py-0.5 text-[10px] rounded transition-colors",
              !selectedAgent ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-muted/30"
            )}
          >
            全部
          </button>
          {agentIds.slice(0, 5).map((id) => (
            <button
              key={id}
              onClick={() => onSelect(selectedAgent === id ? null : id)}
              className={cn(
                "px-2 py-0.5 text-[10px] rounded transition-colors",
                selectedAgent === id ? "bg-primary/15 text-primary" : "text-muted-foreground hover:bg-muted/30"
              )}
            >
              {id.slice(0, 6)}
            </button>
          ))}
        </div>
      </div>

      {loading && chartData.length === 0 ? (
        <div className="h-[200px] flex items-center justify-center">
          <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
        </div>
      ) : chartData.length === 0 ? (
        <div className="h-[200px] flex items-center justify-center text-xs text-muted-foreground">
          暂无执行频次数据
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <RBarChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
            <XAxis dataKey="hour" tick={{ fill: "#94A1BC", fontSize: 9 }} interval="preserveStartEnd" />
            <YAxis tick={{ fill: "#94A1BC", fontSize: 10 }} allowDecimals={false} />
            <RTooltip
              contentStyle={{ background: "#0C1120", border: "1px solid rgba(255,255,255,0.08)", borderRadius: 6, fontSize: 11 }}
              cursor={{ fill: "rgba(255,255,255,0.08)40" }}
            />
            {selectedAgent ? (
              <RBar dataKey={selectedAgent} fill={AGENT_COLORS[selectedAgent] ?? "#5B8DEF"} radius={[2, 2, 0, 0]} />
            ) : (
              agentIds.map((id) => (
                <RBar
                  key={id}
                  dataKey={id}
                  stackId="agents"
                  fill={AGENT_COLORS[id] ?? "#94A1BC"}
                  radius={id === agentIds[agentIds.length - 1] ? [2, 2, 0, 0] : [0, 0, 0, 0]}
                />
              ))
            )}
          </RBarChart>
        </ResponsiveContainer>
      )}
    </Card>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Agent 运行日志面板
// ═══════════════════════════════════════════════════════════════════

function AgentLogsPanel({
  agentFilter,
  onFilterChange,
}: {
  agentFilter: string | null;
  onFilterChange: (agent: string | null) => void;
}) {
  const [paused, setPaused] = useState(false);
  const [levelFilter, setLevelFilter] = useState("all");
  const scrollRef = useRef<HTMLDivElement>(null);

  const logUrl = `/api/monitor/agents/logs?limit=200${agentFilter ? `&agent_id=${agentFilter}` : ""}`;
  const { data, loading, refetch } = usePoll<LogsResponse>(logUrl, 3000);

  const rawLogs = data?.logs ?? [];
  const logs =
    levelFilter === "all"
      ? rawLogs
      : rawLogs.filter((l) => l.level === levelFilter.toUpperCase());

  useEffect(() => {
    if (!paused && scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
  }, [logs, paused]);

  // 收集有日志的 agent 列表
  const agentIds = [...new Set(rawLogs.map((l) => l.agent_id))].sort();

  return (
    <Card className="p-0 overflow-hidden">
      {/* 工具栏 */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border/50">
        <div className="flex items-center gap-2">
          <Terminal className="w-3.5 h-3.5 text-primary" />
          <span className="text-xs font-medium">Agent 运行日志</span>
          <Badge variant="secondary" className="text-[9px]">{logs.length}</Badge>
        </div>
        <div className="flex items-center gap-1">
          {/* Agent 筛选 */}
          <select
            value={agentFilter ?? ""}
            onChange={(e) => onFilterChange(e.target.value || null)}
            className="bg-muted/30 border border-border/50 rounded text-[10px] px-1.5 py-0.5 text-foreground outline-none"
          >
            <option value="">全部 Agent</option>
            {agentIds.map((id) => (
              <option key={id} value={id}>{id}</option>
            ))}
          </select>
          {/* 级别筛选 */}
          <div className="flex items-center gap-0.5">
            {["all", "INFO", "WARN", "ERROR"].map((l) => (
              <button
                key={l}
                onClick={() => setLevelFilter(l)}
                className={cn(
                  "px-1.5 py-0.5 text-[9px] rounded transition-colors",
                  levelFilter === l
                    ? "bg-primary/15 text-primary"
                    : "text-muted-foreground hover:bg-muted/30"
                )}
              >
                {l === "all" ? "全部" : l}
              </button>
            ))}
          </div>
          <Button
            size="sm"
            variant={paused ? "default" : "outline"}
            className="h-6 px-2"
            onClick={() => setPaused(!paused)}
          >
            {paused ? <Play className="w-3 h-3" /> : <Pause className="w-3 h-3" />}
          </Button>
          <Button size="sm" variant="outline" className="h-6 px-2" onClick={refetch}>
            <RefreshCw className={cn("w-3 h-3", loading && "animate-spin")} />
          </Button>
        </div>
      </div>

      {/* 日志列表 */}
      <div
        ref={scrollRef}
        className="font-mono text-[10px] h-[400px] overflow-y-auto bg-black/30 p-2 leading-relaxed"
      >
        {logs.length === 0 ? (
          <div className="text-muted-foreground text-center py-8 text-xs">
            {loading ? "加载中..." : "暂无日志"}
          </div>
        ) : (
          logs.map((log, i) => {
            const isErr = log.level === "ERROR";
            const isWarn = log.level === "WARN";
            const agentColor = AGENT_COLORS[log.agent_id] ?? "#94A1BC";

            return (
              <div
                key={i}
                className={cn(
                  "flex items-start gap-2 py-0.5 px-1 hover:bg-muted/10 rounded",
                  isErr && "bg-loss/5"
                )}
              >
                <span className="text-muted-foreground shrink-0 tabular-nums">
                  {log.ts ? new Date(log.ts).toLocaleTimeString("zh-CN", { hour12: false }) : "--:--:--"}
                </span>
                <span
                  className="shrink-0 font-medium"
                  style={{ color: agentColor }}
                >
                  [{log.agent_id}]
                </span>
                <span
                  className={cn(
                    "shrink-0 font-bold",
                    isErr ? "text-loss" : isWarn ? "text-warning" : "text-primary/70"
                  )}
                >
                  {log.level}
                </span>
                <span
                  className={cn(
                    "flex-1 break-all",
                    isErr ? "text-loss/90" : isWarn ? "text-warning/90" : "text-foreground/70"
                  )}
                >
                  {log.message}
                </span>
              </div>
            );
          })
        )}
      </div>
    </Card>
  );
}

// ═══════════════════════════════════════════════════════════════════
// 通用小组件
// ═══════════════════════════════════════════════════════════════════

function KpiCard({
  label,
  value,
  icon: Icon,
  color,
}: {
  label: string;
  value: any;
  icon: any;
  color?: string;
}) {
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

function MiniStat({
  label,
  value,
  color,
}: {
  label: string;
  value: any;
  color?: string;
}) {
  return (
    <div className="text-center p-1 rounded bg-muted/10">
      <div className={cn("text-xs font-bold tabular-nums", color)}>{value}</div>
      <div className="text-[8px] text-muted-foreground">{label}</div>
    </div>
  );
}
