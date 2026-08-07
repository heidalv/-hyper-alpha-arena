"use client";

import { useEffect, useMemo, useState } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Play, Square, Pause, RefreshCw, Loader2, Plus, X,
  Zap, Activity, Search, Settings2, Bot, Trash2, ChevronDown, ChevronRight,
} from "lucide-react";
import {
  useSessions, useAccounts, useStartSession, useStopSession,
  usePauseSession, useResumeSession, useDeleteSession,
} from "@/hooks/useTradingData";
import { sessionApi, autoCoinApi, type SessionStatus } from "@/lib/api";
import { useQueryClient, useQuery } from "@tanstack/react-query";
import { useAuthStore } from "@/lib/stores/auth";
import { cn } from "@/lib/utils";

/** 会话内 AI 选币：仅 VIP / 管理员 */
function useCanSessionAutoCoin(): boolean {
  const user = useAuthStore((s) => s.user);
  const tier = (user?.tier || "").toLowerCase();
  const role = (user?.role || "").toLowerCase();
  return role === "admin" || tier === "vip";
}

type SessionConfigForm = {
  risk_level: string;
  risk_mode: string;
  max_concurrent_strategies: number;
  max_total_drawdown_pct: number;
  daily_loss_limit_pct: number;
  active_exchange: string;
  auto_coin_max_slots: number;
};

function configFromSession(session: SessionStatus): SessionConfigForm {
  return {
    risk_level: session.risk_level ?? "moderate",
    risk_mode: session.risk_mode ?? "ai_dynamic",
    max_concurrent_strategies: session.max_concurrent_strategies ?? 25,
    max_total_drawdown_pct: session.max_total_drawdown_pct ?? 0.30,
    daily_loss_limit_pct: session.daily_loss_limit_pct ?? 0.05,
    active_exchange: session.active_exchange ?? "",
    auto_coin_max_slots: session.auto_coin_max_slots ?? 5,
  };
}

export function SessionManager() {
  const { data: sessions, isLoading } = useSessions();
  const { data: accounts } = useAccounts();
  const startMut = useStartSession();
  const deleteMut = useDeleteSession();
  const qc = useQueryClient();

  const [showCreate, setShowCreate] = useState(false);
  const [selectedAccount, setSelectedAccount] = useState<number | null>(null);
  const [symbols, setSymbols] = useState("BTC,ETH,SOL");
  const [mode, setMode] = useState("paper");
  const [showStopped, setShowStopped] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const paperAccounts = accounts?.filter((a) => a.trading_mode === "paper") ?? [];

  // 分组：活跃 vs 已停用
  const activeStatuses = ["running", "defensive", "paused"];
  const activeSessions = sessions?.filter((s) => activeStatuses.includes(s.status)) ?? [];
  const stoppedSessions = sessions?.filter((s) => !activeStatuses.includes(s.status)) ?? [];

  const handleCreate = async () => {
    if (!selectedAccount) return;
    const symList = symbols.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
    await startMut.mutateAsync({ account_id: selectedAccount, symbols: symList, mode });
    setShowCreate(false);
  };

  const handleDelete = async (sessionId: string) => {
    await deleteMut.mutateAsync(sessionId);
    setConfirmDelete(null);
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium">AI 交易会话</h2>
        <div className="flex items-center gap-2">
          {stoppedSessions.length > 0 && (
            <Badge variant="secondary" className="text-[10px]">{activeSessions.length} 活跃 · {stoppedSessions.length} 已停用</Badge>
          )}
          <Button size="sm" variant="outline" onClick={() => setShowCreate(!showCreate)}>
            {showCreate ? <X className="w-3.5 h-3.5 mr-1" /> : <Plus className="w-3.5 h-3.5 mr-1" />}
            {showCreate ? "取消" : "创建会话"}
          </Button>
        </div>
      </div>

      {/* 创建会话表单 */}
      {showCreate && (
        <Card className="p-4 border-primary/30 space-y-3">
          <div>
            <label className="text-xs text-muted-foreground block mb-1">交易账户</label>
            <select value={selectedAccount ?? ""} onChange={(e) => setSelectedAccount(Number(e.target.value))}
              className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
              <option value="">选择账户...</option>
              {paperAccounts.map((a) => (<option key={a.id} value={a.id}>{a.name} (${a.current_cash})</option>))}
            </select>
          </div>
          <div>
            <label className="text-xs text-muted-foreground block mb-1">交易对 (逗号分隔)</label>
            <Input type="text" value={symbols} onChange={(e) => setSymbols(e.target.value)} placeholder="BTC,ETH,SOL" className="text-sm" />
          </div>
          <div>
            <label className="text-xs text-muted-foreground block mb-1">交易模式</label>
            <select value={mode} onChange={(e) => setMode(e.target.value)} className="w-full bg-card border border-border text-sm rounded px-2 py-1.5">
              <option value="paper">模拟 (Paper)</option><option value="live">实盘 (Live)</option>
            </select>
          </div>
          <Button size="sm" className="w-full" onClick={handleCreate} disabled={!selectedAccount || startMut.isPending}>
            {startMut.isPending ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Play className="w-3.5 h-3.5 mr-1" />}
            启动会话
          </Button>
          {startMut.isError && <div className="text-xs text-loss">启动失败: {startMut.error?.message}</div>}
        </Card>
      )}

      {/* 会话列表 */}
      {isLoading ? (
        <div className="flex justify-center py-6"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>
      ) : (!sessions || sessions.length === 0) ? (
        <div className="text-center py-6 text-muted-foreground text-sm">暂无会话，点击"创建会话"启动</div>
      ) : (
        <div className="space-y-3">
          {/* 活跃会话 */}
          <div className="space-y-2">
            {activeSessions.length > 0 && (
              <div className="text-[10px] text-muted-foreground font-medium uppercase tracking-wider flex items-center gap-1">
                <Activity className="w-3 h-3 text-profit" />
                运行中 ({activeSessions.length})
              </div>
            )}
            {activeSessions.map((s) => (
              <SessionRow
                key={s.session_id}
                session={s}
                confirmDelete={confirmDelete}
                setConfirmDelete={setConfirmDelete}
                onDelete={handleDelete}
                deleting={deleteMut.isPending}
              />
            ))}
          </div>

          {/* 已停用会话（可折叠） */}
          {stoppedSessions.length > 0 && (
            <div className="space-y-2">
              <button
                onClick={() => setShowStopped(!showStopped)}
                className="text-[10px] text-muted-foreground font-medium uppercase tracking-wider flex items-center gap-1 hover:text-foreground"
              >
                {showStopped ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                已停用 ({stoppedSessions.length})
              </button>
              {showStopped && (
                <>
                  <div className="text-[10px] text-muted-foreground bg-muted/10 rounded p-2 border border-border/30">
                    已停用的会话会保留在数据库中。点击删除按钮可彻底清除（含策略和持仓记录）。
                  </div>
                  {stoppedSessions.map((s) => (
                    <SessionRow
                      key={s.session_id}
                      session={s}
                      confirmDelete={confirmDelete}
                      setConfirmDelete={setConfirmDelete}
                      onDelete={handleDelete}
                      deleting={deleteMut.isPending}
                    />
                  ))}
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SessionRow({
  session,
  confirmDelete,
  setConfirmDelete,
  onDelete,
  deleting,
}: {
  session: any;
  confirmDelete: string | null;
  setConfirmDelete: (id: string | null) => void;
  onDelete: (id: string) => void;
  deleting: boolean;
}) {
  const qc = useQueryClient();
  const status = session.status;
  const isRunning = status === "running";
  const isPaused = status === "paused";
  const isDefensive = status === "defensive";
  const isStopped = status === "stopped";
  // running/paused/defensive 都允许热改配置与交易对
  const canEdit = !isStopped;
  const [expanded, setExpanded] = useState(isRunning || isPaused || isDefensive);
  const [addSym, setAddSym] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [autoCoinStatus, setAutoCoinStatus] = useState<any>(null);
  const canAutoCoin = useCanSessionAutoCoin();
  const isConfirming = confirmDelete === session.session_id;

  const loadAutoCoin = async () => {
    try { setAutoCoinStatus(await autoCoinApi.status(session.session_id)); } catch {}
  };

  useEffect(() => {
    if (expanded) loadAutoCoin();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded, session.session_id]);

  const action = async (type: string, fn: () => Promise<any>) => {
    setBusy(type);
    try {
      await fn();
      // 2026-07-20：invalidate 后立即 refetch，确保列表立刻反映变更，
      // 不依赖 staleTime 过期。避免"删除后又刷新回来"的视觉假象。
      await qc.invalidateQueries({ queryKey: ["sessions"] });
      await qc.refetchQueries({ queryKey: ["sessions"] });
    } catch (e: any) {
      const msg = e?.message || e?.detail || String(e);
      alert(`操作失败: ${msg}`);
    } finally { setBusy(null); }
  };

  const toggleAutoCoin = async () => {
    const enabled = autoCoinStatus?.auto_coin_enabled;
    if (!enabled && !canAutoCoin) {
      alert("会话内 AI 选币仅 VIP 可用，请升级 VIP 后再开启");
      return;
    }
    await action("autoCoin", async () => {
      if (enabled) { await autoCoinApi.stop(session.session_id); }
      else { await autoCoinApi.start(session.session_id); }
      await loadAutoCoin();
    });
  };

  const scanNow = async () => {
    if (!canAutoCoin) {
      alert("会话内 AI 选币仅 VIP 可用");
      return;
    }
    await action("scan", () => autoCoinApi.scanNow(session.session_id));
  };

  const handleAddSym = async () => {
    if (!addSym.trim()) return;
    const syms = addSym.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
    await action("add", () => sessionApi.addSymbols(session.session_id, syms));
    setAddSym("");
  };

  const handleRemoveSym = async (sym: string) => {
    await action("remove", () => sessionApi.removeSymbols(session.session_id, [sym]));
  };

  const healthCheck = async () => {
    await action("health", () => sessionApi.healthCheck(session.session_id));
  };

  return (
    <Card className="p-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <Badge variant="secondary" className={cn("text-[10px]",
            isRunning ? "bg-profit/20 text-profit" : isPaused ? "bg-warning/20 text-warning" : "bg-muted text-muted-foreground")}>
            {status}
          </Badge>
          <Badge variant="outline" className={cn("text-[9px]",
            session.trading_mode === "live" ? "bg-loss/15 text-loss border-loss/30" : "bg-primary/15 text-primary border-primary/30")}>
            {session.trading_mode === "live" ? "实盘" : "模拟"}
          </Badge>
          {session.active_exchange && (
            <Badge variant="outline" className="text-[9px] bg-muted/40">{session.active_exchange}</Badge>
          )}
          <span className="text-xs font-mono text-muted-foreground truncate">{session.session_id?.slice(0, 16)}...</span>
          <button onClick={() => { setExpanded(!expanded); if (!expanded && !autoCoinStatus) loadAutoCoin(); }}
            className="text-xs text-primary hover:underline ml-1">
            {expanded ? "收起" : "展开配置"}
          </button>
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          {isRunning && (
            <Button size="sm" variant="ghost" className="h-7 px-2 text-xs text-warning"
              onClick={() => action("pause", () => sessionApi.pause(session.session_id))} disabled={!!busy}>
              {busy === "pause" ? <Loader2 className="w-3 h-3 animate-spin" /> : <Pause className="w-3 h-3" />}
            </Button>
          )}
          {isPaused && (
            <Button size="sm" variant="ghost" className="h-7 px-2 text-xs text-profit"
              onClick={() => action("resume", () => sessionApi.resume(session.session_id))} disabled={!!busy}>
              {busy === "resume" ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
            </Button>
          )}
          {status !== "stopped" && (
            <Button size="sm" variant="ghost" className="h-7 px-2 text-xs text-loss"
              onClick={() => action("stop", () => sessionApi.stop(session.session_id))} disabled={!!busy}>
              {busy === "stop" ? <Loader2 className="w-3 h-3 animate-spin" /> : <Square className="w-3 h-3" />}
            </Button>
          )}
          {/* 删除按钮：彻底从数据库清除 */}
          {isStopped ? (
            isConfirming ? (
              <div className="flex items-center gap-1">
                <Button size="sm" variant="destructive" className="h-7 px-2 text-[10px]"
                  onClick={() => onDelete(session.session_id)} disabled={deleting}>
                  {deleting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                  确认删除
                </Button>
                <Button size="sm" variant="ghost" className="h-7 px-2 text-[10px]"
                  onClick={() => setConfirmDelete(null)}>
                  取消
                </Button>
              </div>
            ) : (
              <Button size="sm" variant="ghost" className="h-7 px-2 text-xs text-muted-foreground hover:text-loss"
                onClick={() => setConfirmDelete(session.session_id)} title="彻底删除（含策略和持仓记录）">
                <Trash2 className="w-3 h-3" />
              </Button>
            )
          ) : null}
        </div>
      </div>

      {/* 删除确认警告 */}
      {isConfirming && (
        <div className="mt-2 p-2 rounded bg-loss/10 border border-loss/30 text-[10px] text-loss">
          ⚠️ 彻底删除将从数据库清除该会话及其所有策略、持仓记录，不可恢复。
        </div>
      )}

      {/* 关键信息行 */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-2 text-[10px] text-muted-foreground">
        <span><Bot className="w-3 h-3 inline mr-1" />交易员: <span className="text-foreground">{session.account_name ?? `#${session.account_id}`}</span></span>
        {session.paper_account_name && (
          <span>模拟账户: <span className="text-foreground">{session.paper_account_name}</span></span>
        )}
        <span>风险: <span className="text-foreground">{session.risk_level ?? "moderate"} / {session.risk_mode ?? "ai_dynamic"}</span></span>
        <span>{session.active_count}/{session.max_concurrent_strategies ?? 25} 策略</span>
        <span>AI槽位: <span className="text-foreground">{(session.auto_coin_symbols || []).length}/{session.auto_coin_max_slots ?? 5}</span></span>
        {session.total_trades !== undefined && session.total_trades > 0 && (
          <span>胜率: <span className={cn("text-foreground", (session.win_rate ?? 0) >= 50 ? "text-profit" : "text-loss")}>{(session.win_rate ?? 0).toFixed(1)}%</span> ({session.total_trades}笔)</span>
        )}
        {session.total_pnl !== undefined && (
          <span>PnL: <span className={cn(session.total_pnl >= 0 ? "text-profit" : "text-loss")}>{session.total_pnl >= 0 ? "+" : ""}{session.total_pnl.toFixed(2)}</span></span>
        )}
      </div>

      {/* 固定交易对（不占 AI 选币槽位） */}
      <div className="mt-2 space-y-1">
        <div className="text-[10px] text-muted-foreground">固定交易对 · 长线</div>
        <div className="flex flex-wrap gap-1">
          {(session.symbols || []).length === 0 ? (
            <span className="text-[10px] text-muted-foreground/70">无</span>
          ) : (
            (session.symbols || []).map((s: string) => (
              <div key={s} className="flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-muted/50 text-[10px]">
                {s}
                {canEdit && (
                  <button onClick={() => handleRemoveSym(s)} className="text-muted-foreground hover:text-loss ml-0.5">
                    <X className="w-2.5 h-2.5" />
                  </button>
                )}
              </div>
            ))
          )}
        </div>
        {(session.auto_coin_symbols || []).length > 0 && (
          <>
            <div className="text-[10px] text-muted-foreground pt-1">
              AI选币 · 短线 · 槽位 {(session.auto_coin_symbols || []).length}/{session.auto_coin_max_slots ?? 5}
            </div>
            <div className="flex flex-wrap gap-1">
              {(session.auto_coin_symbols || []).map((s: string) => (
                <div key={`auto-${s}`} className="flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-primary/10 text-primary text-[10px]">
                  {s}
                  {canEdit && (
                    <button onClick={() => handleRemoveSym(s)} className="text-primary/60 hover:text-loss ml-0.5">
                      <X className="w-2.5 h-2.5" />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* 展开区 */}
      {expanded && (
        <div className="mt-3 pt-3 border-t border-border/30 space-y-3">
          {/* 动态加币 */}
          {canEdit && (
            <div className="flex gap-2">
              <Input value={addSym} onChange={(e) => setAddSym(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleAddSym()}
                placeholder="加币 (如 ARB,OP)" className="text-xs h-7" disabled={!!busy} />
              <Button size="sm" variant="outline" className="h-7 text-xs" onClick={handleAddSym} disabled={busy === "add"}>
                {busy === "add" ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
              </Button>
            </div>
          )}

          {/* 自动选币（仅 VIP / 管理员可开启） */}
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Zap className="w-3.5 h-3.5 text-primary" />
                <span className="text-xs">自动选币</span>
                <Badge variant="outline" className="text-[9px]">VIP</Badge>
                {autoCoinStatus && (
                  <Badge variant="secondary" className={cn("text-[9px]",
                    autoCoinStatus.auto_coin_enabled ? "bg-profit/20 text-profit" : "bg-muted text-muted-foreground")}>
                    {autoCoinStatus.auto_coin_enabled ? "ON" : "OFF"}
                  </Badge>
                )}
                {autoCoinStatus?.degraded && (
                  <Badge variant="outline" className="text-[9px] text-amber-400 border-amber-500/40">
                    {autoCoinStatus.degraded === "score_only" ? "规则分·非AI" : autoCoinStatus.degraded}
                  </Badge>
                )}
                {autoCoinStatus?.rank_source && (
                  <Badge variant="outline" className="text-[9px]">
                    {autoCoinStatus.rank_source}
                  </Badge>
                )}
                <Badge variant="outline" className="text-[9px]">
                  槽位 {(session.auto_coin_symbols || []).length}/{session.auto_coin_max_slots ?? 5}
                </Badge>
              </div>
              <div className="flex gap-1">
                {canAutoCoin && autoCoinStatus?.auto_coin_enabled && (
                  <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={scanNow} disabled={busy === "scan"}>
                    {busy === "scan" ? <Loader2 className="w-3 h-3 animate-spin" /> : <Search className="w-3 h-3 mr-1" />}
                    补仓扫描
                  </Button>
                )}
                {canAutoCoin ? (
                  <Button size="sm" variant="outline" className="h-7 text-xs" onClick={toggleAutoCoin} disabled={busy === "autoCoin"}>
                    {busy === "autoCoin" ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
                    {autoCoinStatus?.auto_coin_enabled ? "关闭" : "开启"}
                  </Button>
                ) : autoCoinStatus?.auto_coin_enabled ? (
                  <Button size="sm" variant="outline" className="h-7 text-xs" onClick={toggleAutoCoin} disabled={busy === "autoCoin"}>
                    {busy === "autoCoin" ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
                    关闭
                  </Button>
                ) : (
                  <Button size="sm" variant="outline" className="h-7 text-xs opacity-60" disabled title="需 VIP">
                    需 VIP
                  </Button>
                )}
              </div>
            </div>
            {!canAutoCoin && (
              <p className="text-[10px] text-muted-foreground pl-5">会话内 AI 选币仅 VIP 可用，请升级后再开启</p>
            )}
            {canAutoCoin && autoCoinStatus?.auto_coin_enabled && (
              <p className="text-[10px] text-muted-foreground pl-5">
                槽位是上限；实际跟投数量取决于 VIP 短线看板（不含你的固定币 BTC/ETH/SOL）。
                改大槽位后点「补仓扫描」或保存配置会自动补。看板币不够时无法凑满。
              </p>
            )}
          </div>

          {/* 自动选币状态 */}
          {autoCoinStatus?.auto_coin_enabled && autoCoinStatus.auto_symbols && (
            <div className="text-xs">
              <span className="text-muted-foreground">选中的币: </span>
              <span className="text-primary">{autoCoinStatus.auto_symbols.join(", ")}</span>
            </div>
          )}

          {/* 会话配置（运行中可直接改，含 AI 槽位） */}
          <ConfigEditor
            session={session}
            canEdit={canEdit}
            onUpdated={() => {
              qc.invalidateQueries({ queryKey: ["sessions"] });
              qc.refetchQueries({ queryKey: ["sessions"] });
            }}
          />

          {/* 健康状态 */}
          <HealthStatus sessionId={session.session_id} status={status} onCheck={healthCheck} busy={busy} />

          {/* Tier 状态 */}
          <TierStatus sessionId={session.session_id} />
        </div>
      )}
    </Card>
  );
}

function TierStatus({ sessionId }: { sessionId: string }) {
  // 2026-07-20：原实现初始 tiers=null 且不自动加载，用户展开后看到空白。
  // 改为 useQuery 自动加载 + 10s 轮询，与 dashboard/agent-monitor 一致。
  const { data: tiers, isLoading, refetch } = useQuery({
    queryKey: ["tier-status", sessionId],
    queryFn: () => sessionApi.tierStatus(sessionId),
    refetchInterval: 10000,
  });

  const tierData = tiers?.tiers ?? tiers ?? {};

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <Settings2 className="w-3.5 h-3.5 text-muted-foreground" />
          <span className="text-xs">三周期状态</span>
        </div>
        <button onClick={() => refetch()} className="text-[10px] text-primary hover:underline">
          {isLoading ? "加载中..." : "刷新"}
        </button>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {["short", "mid", "long"].map((tier) => {
          const t = tierData[tier] || {};
          return (
            <div key={tier} className="p-2 rounded bg-muted/30 text-center">
              <div className="text-[10px] text-muted-foreground">
                {tier === "short" ? "短线" : tier === "mid" ? "中线" : "长线"}
              </div>
              <div className="text-sm font-bold tabular-nums">{t.active_count ?? 0}</div>
              <div className="text-[9px] text-muted-foreground">{t.position_count ?? 0} 持仓</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function HealthStatus({
  sessionId, status, onCheck, busy,
}: {
  sessionId: string;
  status: string;
  onCheck: () => void;
  busy: string | null;
}) {
  // 2026-07-20：原"健康检查"只有一个按钮，根本不展示健康状态。
  // 现在用 useQuery 轮询 /full-auto/status/{sessionId} 拿 system_health 并展示。
  // 只有非 stopped 状态才轮询（避免对已停止会话空跑）。
  const enabled = status !== "stopped";
  const { data, isLoading } = useQuery({
    queryKey: ["session-status", sessionId],
    queryFn: () => sessionApi.getStatus(sessionId),
    refetchInterval: enabled ? 15000 : false,
    enabled,
  });

  const h = data?.system_health;
  const dataFlowOk = h?.data_flow_ok;
  const aiOk = h?.ai_connection_ok;
  const consecFails = h?.consecutive_ai_failures ?? 0;
  const lastAiSuccess = h?.last_ai_success;
  const lastCheck = data?.last_health_check;

  const okBadge = (ok?: boolean) => (
    <Badge variant="secondary" className={cn("text-[9px]",
      ok ? "bg-profit/20 text-profit" : "bg-loss/20 text-loss")}>
      {ok ? "正常" : "异常"}
    </Badge>
  );

  return (
    <div>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity className="w-3.5 h-3.5 text-muted-foreground" />
          <span className="text-xs">健康状态</span>
          {isLoading && <Loader2 className="w-3 h-3 animate-spin text-muted-foreground" />}
        </div>
        {enabled && (
          <Button size="sm" variant="outline" className="h-7 text-xs" onClick={onCheck} disabled={busy === "health"}>
            {busy === "health" ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3 mr-1" />}
            检查
          </Button>
        )}
      </div>
      {enabled && (
        <div className="mt-1 grid grid-cols-2 gap-1 text-[10px]">
          <div className="flex items-center justify-between px-1.5 py-1 rounded bg-muted/30">
            <span className="text-muted-foreground">数据流</span>
            {okBadge(dataFlowOk)}
          </div>
          <div className="flex items-center justify-between px-1.5 py-1 rounded bg-muted/30">
            <span className="text-muted-foreground">AI 连接</span>
            {okBadge(aiOk)}
          </div>
          <div className="flex items-center justify-between px-1.5 py-1 rounded bg-muted/30">
            <span className="text-muted-foreground">连续失败</span>
            <span className={cn("tabular-nums", consecFails > 0 ? "text-loss" : "text-profit")}>{consecFails}</span>
          </div>
          <div className="flex items-center justify-between px-1.5 py-1 rounded bg-muted/30">
            <span className="text-muted-foreground">上次检查</span>
            <span className="tabular-nums text-muted-foreground">
              {lastCheck ? new Date(lastCheck).toLocaleTimeString("zh-CN", { hour12: false }) : "—"}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

function ConfigEditor({
  session, canEdit, onUpdated,
}: {
  session: SessionStatus;
  canEdit: boolean;
  onUpdated: () => void;
}) {
  // 运行中/暂停/防守：直接改配置，无需先点「编辑」
  const canAutoCoin = useCanSessionAutoCoin();
  const [form, setForm] = useState<SessionConfigForm>(() => configFromSession(session));
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // 列表刷新后同步表单（未改动时）
  const baseline = useMemo(() => configFromSession(session), [
    session.risk_level,
    session.risk_mode,
    session.max_concurrent_strategies,
    session.max_total_drawdown_pct,
    session.daily_loss_limit_pct,
    session.active_exchange,
    session.auto_coin_max_slots,
  ]);

  const dirty = useMemo(() => {
    return (
      form.risk_level !== baseline.risk_level ||
      form.risk_mode !== baseline.risk_mode ||
      Number(form.max_concurrent_strategies) !== Number(baseline.max_concurrent_strategies) ||
      Number(form.max_total_drawdown_pct) !== Number(baseline.max_total_drawdown_pct) ||
      Number(form.daily_loss_limit_pct) !== Number(baseline.daily_loss_limit_pct) ||
      (form.active_exchange || "") !== (baseline.active_exchange || "") ||
      Number(form.auto_coin_max_slots) !== Number(baseline.auto_coin_max_slots)
    );
  }, [form, baseline]);

  useEffect(() => {
    // 有未保存修改时不要被 5s 轮询冲掉
    if (dirty) return;
    setForm(baseline);
  }, [baseline, dirty]);

  const handleSave = async () => {
    if (!canEdit) return;
    setSaving(true);
    setMsg(null);
    setErr(null);
    try {
      const payload: Record<string, unknown> = {
        risk_level: form.risk_level,
        risk_mode: form.risk_mode,
        max_concurrent_strategies: Number(form.max_concurrent_strategies),
        max_total_drawdown_pct: Number(form.max_total_drawdown_pct),
        daily_loss_limit_pct: Number(form.daily_loss_limit_pct),
        active_exchange: (form.active_exchange || "").trim(),
      };
      if (canAutoCoin) {
        payload.auto_coin_max_slots = Number(form.auto_coin_max_slots);
      }
      const res = await sessionApi.updateConfig(session.session_id, payload as any);
      setMsg(res.message || "已保存，运行中立即生效");
      onUpdated();
    } catch (e: any) {
      setErr(e?.message || e?.detail || "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const reset = () => {
    setForm(baseline);
    setMsg(null);
    setErr(null);
  };

  const selectCls = "text-xs h-7 px-1 rounded bg-background border border-border";
  const fieldCls = "flex items-center justify-between gap-2 px-1.5 py-1 rounded bg-muted/30";

  return (
    <div className="rounded-md border border-border/40 p-2 space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Settings2 className="w-3.5 h-3.5 text-muted-foreground" />
          <span className="text-xs font-medium">会话配置</span>
          {canEdit ? (
            <Badge variant="outline" className="text-[9px] text-profit border-profit/40">运行中可改</Badge>
          ) : (
            <Badge variant="outline" className="text-[9px]">已停止·只读</Badge>
          )}
        </div>
        {canEdit && dirty && (
          <div className="flex gap-1">
            <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={reset} disabled={saving}>
              还原
            </Button>
            <Button size="sm" className="h-7 text-xs" onClick={handleSave} disabled={saving}>
              {saving ? <Loader2 className="w-3 h-3 animate-spin mr-1" /> : null}
              保存生效
            </Button>
          </div>
        )}
      </div>
      <p className="text-[10px] text-muted-foreground">
        风险/回撤/并发/交易所/AI槽位可热改。交易模式与绑定账户需停止后重建。
      </p>
      {msg && <div className="text-[10px] text-profit">{msg}</div>}
      {err && <div className="text-[10px] text-loss">{err}</div>}

      <div className="grid grid-cols-2 gap-2 text-[10px]">
        <div className={fieldCls}>
          <span className="text-muted-foreground">风险等级</span>
          <select className={selectCls} disabled={!canEdit} value={form.risk_level}
            onChange={(e) => setForm({ ...form, risk_level: e.target.value })}>
            <option value="conservative">保守</option>
            <option value="moderate">稳健</option>
            <option value="aggressive">激进</option>
          </select>
        </div>
        <div className={fieldCls}>
          <span className="text-muted-foreground">风控模式</span>
          <select className={selectCls} disabled={!canEdit} value={form.risk_mode}
            onChange={(e) => setForm({ ...form, risk_mode: e.target.value })}>
            <option value="ai_dynamic">AI动态</option>
            <option value="conservative">保守</option>
            <option value="aggressive">激进</option>
          </select>
        </div>
        <div className={fieldCls}>
          <span className="text-muted-foreground">最大并发策略</span>
          <input type="number" min={1} max={100} disabled={!canEdit}
            className="w-14 text-xs h-6 px-1 rounded bg-background border border-border"
            value={form.max_concurrent_strategies}
            onChange={(e) => setForm({ ...form, max_concurrent_strategies: Number(e.target.value) })} />
        </div>
        <div className={fieldCls}>
          <span className="text-muted-foreground">最大回撤</span>
          <div className="flex items-center gap-1">
            <input type="number" step={1} min={1} max={100} disabled={!canEdit}
              className="w-14 text-xs h-6 px-1 rounded bg-background border border-border"
              value={Math.round(Number(form.max_total_drawdown_pct) * 100)}
              onChange={(e) => setForm({ ...form, max_total_drawdown_pct: Number(e.target.value) / 100 })} />
            <span>%</span>
          </div>
        </div>
        <div className={fieldCls}>
          <span className="text-muted-foreground">日亏损限</span>
          <div className="flex items-center gap-1">
            <input type="number" step={0.5} min={0.5} max={100} disabled={!canEdit}
              className="w-14 text-xs h-6 px-1 rounded bg-background border border-border"
              value={Number((Number(form.daily_loss_limit_pct) * 100).toFixed(1))}
              onChange={(e) => setForm({ ...form, daily_loss_limit_pct: Number(e.target.value) / 100 })} />
            <span>%</span>
          </div>
        </div>
        <div className={fieldCls}>
          <span className="text-muted-foreground">交易所</span>
          <select className={selectCls} disabled={!canEdit} value={form.active_exchange}
            onChange={(e) => setForm({ ...form, active_exchange: e.target.value })}>
            <option value="">跟随账户</option>
            <option value="hyperliquid">Hyperliquid</option>
            <option value="binance">Binance</option>
            <option value="okx">OKX</option>
            <option value="bybit">Bybit</option>
            <option value="asterdex">Asterdex</option>
          </select>
        </div>
        <div className={cn(fieldCls, "col-span-2")}>
          <span className="text-muted-foreground">
            AI选币槽位{!canAutoCoin ? "（需VIP）" : "（仅约束AI池，固定币不占）"}
          </span>
          <select
            className={selectCls}
            disabled={!canEdit || !canAutoCoin}
            value={form.auto_coin_max_slots}
            onChange={(e) => setForm({ ...form, auto_coin_max_slots: Number(e.target.value) })}
          >
            {[5, 6, 7, 8, 9, 10].map((n) => (
              <option key={n} value={n}>{n} 个</option>
            ))}
          </select>
        </div>
      </div>

      {canEdit && (
        <div className="flex items-center justify-between gap-2 pt-1">
          <span className="text-[10px] text-muted-foreground">
            {dirty ? "有未保存修改" : "与服务器一致"}
          </span>
          <Button size="sm" className="h-7 text-xs" onClick={handleSave} disabled={saving || !dirty}>
            {saving ? <Loader2 className="w-3 h-3 animate-spin mr-1" /> : null}
            保存配置
          </Button>
        </div>
      )}
    </div>
  );
}
