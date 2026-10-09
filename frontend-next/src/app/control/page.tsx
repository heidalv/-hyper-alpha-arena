"use client";

/**
 * 交易总控（[2026-10-03 用户需求]）
 *
 * 用户原话：「一个统一管理的位置，要不这个分散和不可见，无法真正的控制账户」。
 *
 * 为什么需要这一页：
 *   · 「能不能自动交易」= `accounts.auto_trading_enabled`（只在交易所管理页可见，一屏看不到全貌）；
 *   · 「现在在不在跑」= `full_auto_sessions.status`（只在 AI 策略 → 会话管理里可见）；
 *   · 「停手」实际要同时做两件事（停会话 + 关账户开关），分散在两页 ⇒ 出现过"以为停了其实还在跑"
 *     （实测：会话一直 running 并持续开仓，而用户以为早就停了）。
 *
 * 本页把三件事收在一处，并给出**一键停手**：
 *   1) 全局统计 + 「全部停止交易」（停全部 running/defensive/paused 会话 + 关全部账户开关）
 *   2) 逐账户：模式 / 启用 / 自动交易开关（即时生效）/ 绑定会话及其状态
 *   3) 逐会话：状态 + 停止 / 恢复
 */
import { useMemo, useState } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/layout/PageHeader";
import { cn } from "@/lib/utils";
import { confirmDialog } from "@/lib/confirm";
import { sessionApi } from "@/lib/api";
import {
  useAccounts, useSessions, useUpdateAccount, useStopSession, useResumeSession,
} from "@/hooks/useTradingData";
import { useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { SessionManager } from "@/components/trading/SessionManager";
import { ExchangeManagerPanel } from "@/components/exchange/ExchangeManagerPanel";
import { LaneConfigPanel } from "@/components/config/LaneConfigPanel";
import {
  Power, ShieldCheck, ShieldOff, Loader2, RefreshCw, Play, Square, AlertTriangle, CheckCircle2,
  Bot, Server, SlidersHorizontal,
} from "lucide-react";

const LIVE_STATUS = ["running", "defensive", "paused"];

/** 页内四区（[2026-10-03 用户需求] AI 策略只保留会话管理 → 与会话管理、交易所管理一并并入本页）。 */
type ControlTab = "control" | "sessions" | "exchange" | "lanes";
const TABS: { key: ControlTab; label: string; icon: any }[] = [
  { key: "control", label: "总控", icon: Power },
  { key: "sessions", label: "会话管理", icon: Bot },
  { key: "exchange", label: "交易所管理", icon: Server },
  { key: "lanes", label: "车道配置", icon: SlidersHorizontal },
];

export default function ControlPage() {
  const searchParams = useSearchParams();
  const tabParam = (searchParams.get("tab") || "").toLowerCase();
  // 深链兼容：/strategy?cfg=long&sub=prompts ⇒ /control?tab=lanes&cfg=long&sub=prompts
  const [tab, setTab] = useState<ControlTab>(
    (["control", "sessions", "exchange", "lanes"] as string[]).includes(tabParam)
      ? (tabParam as ControlTab)
      : searchParams.get("cfg")
        ? "lanes"
        : "control",
  );
  const { data: accounts, isLoading: accountsLoading, refetch: refetchAccounts } = useAccounts();
  const { data: sessions, refetch: refetchSessions } = useSessions();
  const updateMut = useUpdateAccount();
  const stopMut = useStopSession();
  const resumeMut = useResumeSession();
  const qc = useQueryClient();

  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const rows = useMemo(
    () =>
      ((accounts ?? []) as any[]).map((a) => {
        const bound = ((sessions ?? []) as any[]).filter(
          (s) => s.account_id === a.id || s.paper_account_id === a.id,
        );
        const live = bound.filter((s) => LIVE_STATUS.includes(String(s.status)));
        return { a, bound, live };
      }),
    [accounts, sessions],
  );

  const runningTotal = rows.reduce((n, r) => n + r.live.length, 0);
  const armed = rows.filter((r) => r.a.auto_trading_enabled);

  const run = async (key: string, fn: () => Promise<unknown>, okText: string) => {
    setBusy(key);
    setMsg(null);
    try {
      await fn();
      void qc.invalidateQueries({ queryKey: ["accounts"] });
      void qc.invalidateQueries({ queryKey: ["sessions"] });
      setMsg({ ok: true, text: okText });
    } catch (e) {
      setMsg({ ok: false, text: `操作失败：${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setBusy(null);
    }
  };

  /** 一键停手：停全部会话 + 关全部账户自动交易开关。 */
  const handleStopAll = async () => {
    if (!(await confirmDialog({
      title: "停止全部自动交易？",
      description:
        `将停止 ${runningTotal} 个运行中会话，并关闭 ${armed.length} 个账户的「自动交易」开关`
        + `（${armed.map((r) => r.a.name).join("、") || "无"}）。持仓不会被平掉。`,
      tone: "danger",
      confirmText: "全部停止",
      requireText: "停止",
    }))) return;
    await run("stopAll", async () => {
      const r = await sessionApi.stopAll(true);
      if (r?.failed?.length) {
        throw new Error(`部分失败：${JSON.stringify(r.failed).slice(0, 160)}`);
      }
      return r;
    }, "已停止全部会话并关闭所有账户的自动交易开关");
  };

  const toggleAuto = async (id: number, name: string, next: boolean) => {
    await run(`auto-${id}`,
      () => updateMut.mutateAsync({ id, data: { auto_trading_enabled: next } as any }),
      `账户「${name}」自动交易已${next ? "开启" : "关闭"}`);
  };

  return (
    <div className="p-4 space-y-4">
      <PageHeader
        icon={<Power className="w-4 h-4" />}
        title="交易总控"
        subtitle="账户 · 自动交易开关 · 会话 · 交易所 · 车道参数：唯一控制面，一屏看清谁在跑，一键停手"
        refreshHint="会话 10s 轮询"
        breadcrumb={[{ label: "策略配置" }, { label: "交易总控" }]}
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => { void refetchAccounts(); void refetchSessions(); }}
          >
            <RefreshCw className="w-3.5 h-3.5 mr-1" />刷新
          </Button>
        }
      />

      {/* 页内四区：总控 / 会话管理 / 交易所管理 / 车道配置 */}
      <div className="flex gap-1 border-b border-border overflow-x-auto">
        {TABS.map((t) => {
          const Icon = t.icon;
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              data-testid={`control-tab-${t.key}`}
              className={cn(
                "flex items-center gap-1.5 px-3 py-2 text-sm border-b-2 transition-colors -mb-px whitespace-nowrap",
                active
                  ? "border-primary text-primary font-medium"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              <Icon className="w-3.5 h-3.5" />
              {t.label}
              {t.key === "sessions" && runningTotal > 0 && (
                <span className="ml-1 text-[10px] px-1 rounded bg-loss/20 text-loss">{runningTotal}</span>
              )}
            </button>
          );
        })}
      </div>

      {tab === "sessions" && <SessionManager />}
      {tab === "exchange" && <ExchangeManagerPanel />}
      {tab === "lanes" && <LaneConfigPanel />}

      {tab === "control" && (
      <>
      {/* 全局状态 + 一键停手 */}
      <Card className="p-4 glass">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <span className={cn("w-2 h-2 rounded-full", runningTotal > 0 ? "bg-loss animate-pulse" : "bg-profit")} />
            <span className="text-sm font-medium">
              {runningTotal > 0 ? `${runningTotal} 个会话在跑` : "当前没有会话在跑"}
            </span>
          </div>
          <Badge variant="secondary" className="text-xs">账户 {rows.length} 个</Badge>
          <Badge variant={armed.length > 0 ? "destructive" : "secondary"} className="text-xs">
            自动交易已开 {armed.length} 个
          </Badge>
          {runningTotal > 0 && (
            <span className="text-xs text-loss flex items-center gap-1">
              <AlertTriangle className="w-3.5 h-3.5" />
              会话在跑时，页面上的重置/改金额都会被它继续开仓覆盖
            </span>
          )}
          <Button
            variant="destructive"
            size="sm"
            className="ml-auto h-8"
            disabled={busy !== null}
            onClick={handleStopAll}
          >
            {busy === "stopAll" ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Square className="w-3.5 h-3.5 mr-1" />}
            全部停止交易
          </Button>
        </div>
        {msg && (
          <div className={cn(
            "mt-3 rounded-md border px-3 py-2 text-xs flex items-start gap-2",
            msg.ok ? "border-profit/40 bg-profit/10 text-profit" : "border-loss/40 bg-loss/10 text-loss",
          )}>
            {msg.ok ? <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0" /> : <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />}
            <span className="break-all">{msg.text}</span>
          </div>
        )}
      </Card>

      {/* 逐账户 */}
      <Card className="p-4 glass">
        <div className="flex items-center gap-2 mb-3">
          <ShieldCheck className="w-4 h-4 text-primary" />
          <h2 className="text-sm font-medium">账户</h2>
          <span className="text-xs text-muted-foreground">
            两个独立事实：<strong>状态</strong>=现在有没有在跑（有 running/defensive 会话才显示「运行中」）；
            <strong>自动交易</strong>=允不允许自动开仓（开关即时生效）。会话本身要单独「停会话」。
          </span>
        </div>
        {accountsLoading ? (
          <div className="flex justify-center py-8"><Loader2 className="w-5 h-5 animate-spin text-muted-foreground" /></div>
        ) : rows.length === 0 ? (
          <div className="text-center py-6 text-sm text-muted-foreground">暂无账户</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table w-full text-xs">
              <thead>
                <tr className="text-muted-foreground border-b border-border">
                  <th className="text-left py-2 px-2">账户</th>
                  <th className="text-left py-2 px-2">模式</th>
                  <th className="text-left py-2 px-2">状态</th>
                  <th className="text-left py-2 px-2">自动交易</th>
                  <th className="text-left py-2 px-2">绑定会话</th>
                  <th className="text-right py-2 px-2">操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ a, bound, live }) => (
                  <tr key={a.id} className="border-b border-border/40">
                    <td className="py-2 px-2">
                      <div className="font-medium">{a.name}</div>
                      <div className="text-[10px] text-muted-foreground font-mono">#{a.id}</div>
                    </td>
                    <td className="py-2 px-2">
                      <Badge variant="outline" className="text-[10px]">
                        {a.trading_mode === "live" ? "实盘" : "模拟"}
                      </Badge>
                    </td>
                    <td className="py-2 px-2" data-testid={`acct-status-${a.id}`}>
                      {/* [2026-10-03 用户口径] 「启用」名不副实（看着像"在跑"）。
                          本列只回答一个问题：**这个账户现在在不在跑** =
                          有没有 running/defensive 会话；有 → 运行中，没有 → 关闭。
                          账户本身是否被停用（is_active）是另一件事，仅在被停用时以小字标注。 */}
                      {live.length > 0 ? (
                        <span className="text-profit flex items-center gap-1.5">
                          <span className="w-1.5 h-1.5 rounded-full bg-profit animate-pulse" />
                          运行中
                        </span>
                      ) : (
                        <span className="text-muted-foreground flex items-center gap-1.5">
                          <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/50" />
                          关闭
                        </span>
                      )}
                      {!a.is_active && (
                        <span className="text-[10px] text-muted-foreground/70">（账户已停用）</span>
                      )}
                    </td>
                    <td className="py-2 px-2">
                      <Button
                        variant={a.auto_trading_enabled ? "default" : "outline"}
                        size="sm"
                        className={cn("h-6 text-[11px] min-w-[52px]",
                          a.auto_trading_enabled && "bg-loss/80 hover:bg-loss")}
                        disabled={busy !== null}
                        title={a.auto_trading_enabled
                          ? "自动交易开关：开（点击关闭）"
                          : "自动交易开关：关（点击开启）"}
                        onClick={() => toggleAuto(a.id, a.name, !a.auto_trading_enabled)}
                      >
                        {busy === `auto-${a.id}`
                          ? <Loader2 className="w-3 h-3 animate-spin" />
                          : a.auto_trading_enabled
                            ? <ShieldCheck className="w-3 h-3 mr-1" />
                            : <ShieldOff className="w-3 h-3 mr-1" />}
                        {a.auto_trading_enabled ? "开" : "关"}
                      </Button>
                    </td>
                    <td className="py-2 px-2">
                      {bound.length === 0 ? (
                        <span className="text-muted-foreground">无</span>
                      ) : (
                        <div className="flex flex-wrap gap-1">
                          {bound.map((s: any) => (
                            <span
                              key={s.session_id}
                              className={cn(
                                "text-[10px] px-1.5 py-0.5 rounded font-mono",
                                LIVE_STATUS.includes(String(s.status))
                                  ? "bg-loss/15 text-loss"
                                  : "bg-muted/40 text-muted-foreground",
                              )}
                              title={`${s.session_id} · ${s.status}`}
                            >
                              {String(s.status)} {s.active_count ? `·${s.active_count}` : ""}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                    <td className="py-2 px-2 text-right">
                      <div className="flex justify-end gap-1">
                        {live.map((s: any) => (
                          <Button
                            key={s.session_id}
                            variant="outline"
                            size="sm"
                            className="h-6 text-[11px] text-loss"
                            disabled={busy !== null}
                            onClick={() => run(`stop-${s.session_id}`,
                              () => stopMut.mutateAsync(s.session_id),
                              `会话 ${s.session_id} 已停止`)}
                          >
                            <Square className="w-3 h-3 mr-1" />停会话
                          </Button>
                        ))}
                        {bound.filter((s: any) => !LIVE_STATUS.includes(String(s.status))).map((s: any) => (
                          <Button
                            key={s.session_id}
                            variant="outline"
                            size="sm"
                            className="h-6 text-[11px]"
                            disabled={busy !== null}
                            onClick={() => run(`resume-${s.session_id}`,
                              () => resumeMut.mutateAsync(s.session_id),
                              `会话 ${s.session_id} 已恢复运行`)}
                          >
                            <Play className="w-3 h-3 mr-1" />恢复
                          </Button>
                        ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      </>
      )}
    </div>
  );
}
