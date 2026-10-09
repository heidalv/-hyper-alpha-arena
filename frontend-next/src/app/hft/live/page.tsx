"use client";

/**
 * [h665] 实盘高频交易页面 —— 与模拟高频交易(/hft)完全分离。
 *
 * 设计铁律:
 *  1. 整页红色"实盘"主题,任何时刻都显示醒目警示条;
 *  2. 只展示真实数据:未运行/未配置时显示"—"与原因,不画假数据;
 *  3. 危险操作(启动/撤单)必须输入确认词,双保险;
 *  4. 与模拟页互不共享任何状态与参数。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity, AlertTriangle, Ban, KeyRound, Layers, Play, Pause, RefreshCw, Shield,
  Wallet, TrendingUp, Loader2, CircleSlash,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/layout/PageHeader";
import { confirmDialog } from "@/lib/confirm";
import { hftApi, HftLiveStatus } from "@/lib/hft-api";
import { cn } from "@/lib/utils";

const fmt = (v: number | undefined | null, d = 2) =>
  v === undefined || v === null || isNaN(Number(v)) ? "—" : Number(v).toFixed(d);

const fmtUsd = (v: number | undefined | null, d = 2) =>
  v === undefined || v === null || isNaN(Number(v))
    ? "—"
    : `${Number(v) < 0 ? "-" : ""}$${Math.abs(Number(v)).toLocaleString(undefined, {
        minimumFractionDigits: d,
        maximumFractionDigits: d,
      })}`;

const STATUS_TONE: Record<string, string> = {
  active: "border-profit/30 bg-profit/10 text-profit",
  paused: "border-warning/30 bg-warning/10 text-warning",
  stopped: "border-muted/40 bg-muted/20 text-muted-foreground",
};

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <Card className="glass p-3.5">
      <div className="text-xs text-muted-foreground mb-1">{label}</div>
      <div className={cn("text-xl font-bold tabular-nums", tone ?? "text-foreground")}>{value}</div>
      {sub ? <div className="text-xs text-muted-foreground mt-1 truncate">{sub}</div> : null}
    </Card>
  );
}

function CapBar({ label, used, cap }: { label: string; used: number; cap: number }) {
  const pct = cap > 0 ? Math.min(100, (used / cap) * 100) : 0;
  return (
    <div>
      <div className="flex justify-between text-xs text-muted-foreground mb-1">
        <span>{label}</span>
        <span className="tabular-nums">
          {fmtUsd(used)} / {fmtUsd(cap)}
        </span>
      </div>
      <div className="h-1.5 rounded bg-muted/40 overflow-hidden">
        <div
          className={cn("h-full rounded", pct >= 90 ? "bg-loss" : pct >= 70 ? "bg-warning" : "bg-profit")}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export default function HftLivePage() {
  const [status, setStatus] = useState<HftLiveStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState<{ text: string; err: boolean } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const s = await hftApi.liveStatus();
      setStatus(s);
      setError("");
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const t = setInterval(() => void refresh(), 3000);
    return () => clearInterval(t);
  }, [refresh]);

  const bridge = status?.snapshot?.bridge ?? null;
  const bal = bridge?.balance ?? null;
  const positions = bridge?.positions ?? [];
  const orders = bridge?.orders ?? [];
  const totalNotional = useMemo(
    () => positions.reduce((s, p) => s + (p.notional ?? 0), 0),
    [positions]
  );
  const caps = status?.caps;
  // [h674] 有效上限随权益浮动(绝对值只是天花板)
  const eff = bridge?.caps_effective ?? null;
  const totalCap = eff?.total ?? caps?.total_notional_usd ?? 0;
  const symCap = eff?.per_symbol ?? caps?.per_symbol_usd ?? 0;
  const dailyLossCap = eff?.daily_loss ?? caps?.daily_loss_usd ?? 0;
  const isActive = status?.status === "active";

  const doControl = async (next: "active" | "paused" | "stopped", confirmWord?: string) => {
    if (next === "active") {
      const ok = await confirmDialog({
        title: "启动实盘高频交易车道?",
        description:
          `确认后引擎将用真实资金在 Asterdex 挂单。总敞口 ≤ ${fmtUsd(totalCap)}(随权益)、` +
          `单币 ≤ ${fmtUsd(symCap)}、日亏 ≤ ${fmtUsd(dailyLossCap)} 自动停。`,
        tone: "danger",
        confirmText: "启动",
        requireText: "启动",
      });
      if (!ok) return;
    }
    setBusy("control");
    setMsg(null);
    try {
      await hftApi.liveControl(next, next === "active" ? "启动" : "");
      setMsg({ text: `已提交:${next === "active" ? "启动" : next === "paused" ? "暂停" : "停止"}`, err: false });
      await refresh();
    } catch (e: any) {
      setMsg({ text: e?.message ?? String(e), err: true });
    } finally {
      setBusy(null);
    }
  };

  const doKill = async () => {
    const ok = await confirmDialog({
      title: "实盘一键撤单?",
      description: "撤掉实盘车道在 Asterdex 的全部挂单并把车道停止。",
      tone: "danger",
      confirmText: "撤单",
      requireText: "撤单",
    });
    if (!ok) return;
    setBusy("kill");
    setMsg(null);
    try {
      const r = await hftApi.liveKill("撤单");
      setMsg({ text: `已撤 ${r.cancelled} 张挂单,车道已停止`, err: false });
      await refresh();
    } catch (e: any) {
      setMsg({ text: e?.message ?? String(e), err: true });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="p-4 space-y-4 mx-auto w-full max-w-[1600px]">
      <PageHeader
        icon={<TrendingUp className="w-4 h-4" />}
        title="实盘高频交易"
        subtitle="真实资金 · Asterdex · 与模拟完全分离"
        refreshHint="3s 轮询"
        breadcrumb={[{ label: "交易核心" }, { label: "实盘高频交易" }]}
        actions={
          <Button size="sm" variant="outline" onClick={() => void refresh()}>
            <RefreshCw className={cn("w-3.5 h-3.5 mr-1", loading && "animate-spin")} />
            刷新
          </Button>
        }
      />

      {/* 实盘警示条(恒显) */}
      <div className="flex items-center gap-2 text-xs px-3 py-2 rounded bg-loss/10 text-loss border border-loss/30 font-medium">
        <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
        实盘页面:本页所有操作作用于真实资金。总敞口 ≤ {fmtUsd(totalCap)}(随权益,天花板 $
        {fmt(caps?.total_notional_usd, 0)})、单币 ≤ {fmtUsd(symCap)}、日亏 ≤ {fmtUsd(dailyLossCap)} 自动停车。
      </div>

      {!status?.keys_configured && (
        <div className="flex items-center gap-2 text-xs px-3 py-2 rounded bg-warning/10 text-warning border border-warning/20">
          <KeyRound className="w-3.5 h-3.5" />
          未配置 Asterdex API Key(环境变量 ASTERDEX_V3_USER/SIGNER/PRIVATE_KEY,或旧式
          ASTERDEX_API_KEY/SECRET)。下单与启动均被禁止;页面其余部分照常显示。
        </div>
      )}

      {msg && (
        <div
          className={cn(
            "text-xs px-3 py-2 rounded border",
            msg.err ? "bg-loss/10 text-loss border-loss/20" : "bg-profit/10 text-profit border-profit/20"
          )}
        >
          {msg.text}
        </div>
      )}

      {/* 车道状态 + 控制 */}
      <Card className="glass">
        <div className="flex items-center justify-between px-4 pt-3.5 pb-3 border-b border-border/40 flex-wrap gap-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={cn("w-7 h-7 rounded-lg flex items-center justify-center border",
              "bg-loss/10 border-loss/30 text-loss")}>
              <Shield className="w-3.5 h-3.5" />
            </span>
            <span className="text-sm font-medium">车道 {status?.lane_id ?? "mm_asterdex_live"}</span>
            <Badge variant="outline" className={cn("text-xs", STATUS_TONE[status?.status ?? "stopped"] ?? "text-muted-foreground")}>
              {status?.status === "active" ? "运行中" : status?.status === "paused" ? "已暂停" : "已停止"}
            </Badge>
            <Badge variant="outline" className="text-xs text-loss border-loss/30">实盘 LIVE</Badge>
            {bridge?.cooldown && (
              <Badge variant="outline" className="text-xs text-warning border-warning/40">下单熔断冷却中</Badge>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button size="sm" disabled={isActive || busy !== null || !status?.keys_configured}
              onClick={() => void doControl("active")}>
              {busy === "control" ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Play className="w-3.5 h-3.5 mr-1" />}
              启动
            </Button>
            <Button size="sm" variant="outline" disabled={!isActive || busy !== null}
              onClick={() => void doControl("paused")}>
              {busy === "control" ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Pause className="w-3.5 h-3.5 mr-1" />}
              暂停
            </Button>
            <Button size="sm" variant="outline" disabled={status?.status === "stopped" || busy !== null}
              onClick={() => void doControl("stopped")}>
              {busy === "control" ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <CircleSlash className="w-3.5 h-3.5 mr-1" />}
              停止
            </Button>
            <Button size="sm" variant="destructive" disabled={busy !== null} onClick={() => void doKill()}>
              {busy === "kill" ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Ban className="w-3.5 h-3.5 mr-1" />}
              一键撤单
            </Button>
          </div>
        </div>

        {/* KPI */}
        <div className="p-4 grid grid-cols-2 md:grid-cols-4 gap-3">
          <Kpi label="总权益(交易所)" value={fmtUsd(bal?.total_equity)}
            sub={bal ? `日亏基线 ${fmtUsd(bal.day_start_equity)}` : "实盘 worker 未运行"}
            tone={(bal?.total_equity ?? 0) > 0 ? "text-profit" : undefined} />
          <Kpi label="可用余额" value={fmtUsd(bal?.available_balance)} />
          <Kpi
            label="浮盈"
            value={bal?.unrealized_pnl == null ? "—" : `${bal.unrealized_pnl >= 0 ? "+" : ""}${fmtUsd(bal.unrealized_pnl, 4)}`}
            tone={bal?.unrealized_pnl == null ? undefined : bal.unrealized_pnl >= 0 ? "text-profit" : "text-loss"}
          />
          <Kpi label="实盘持仓名义" value={fmtUsd(totalNotional)}
            sub={caps ? `上限 ${fmtUsd(totalCap)}(随权益)` : undefined}
            tone={totalCap > 0 && totalNotional > totalCap * 0.9 ? "text-loss" : undefined} />
        </div>

        {/* 风控 caps */}
        <div className="px-4 pb-4 grid grid-cols-1 md:grid-cols-3 gap-4">
          {caps ? (
            <>
              <CapBar label="总名义占用(上限随权益)" used={totalNotional} cap={totalCap} />
              <div className="flex flex-col justify-between">
                <div className="flex justify-between text-xs text-muted-foreground mb-1">
                  <span>下单熔断</span>
                  <span className="tabular-nums">
                    连续拒单 {bridge?.reject_streak ?? 0} / {caps.reject_break}
                  </span>
                </div>
                <div className="h-1.5 rounded bg-muted/40 overflow-hidden">
                  <div
                    className={cn("h-full rounded",
                      (bridge?.reject_streak ?? 0) >= caps.reject_break ? "bg-loss" : "bg-muted/60")}
                    style={{ width: `${Math.min(100, ((bridge?.reject_streak ?? 0) / Math.max(1, caps.reject_break)) * 100)}%` }}
                  />
                </div>
              </div>
              <div className="text-xs text-muted-foreground leading-relaxed">
                日亏上限 {fmtUsd(dailyLossCap)}(权益 10%,超过自动停车);熔断冷却 {fmt(caps.cooldown_s, 0)}s。
                单币名义上限 {fmtUsd(symCap)}(权益 0.5×,天花板 ${fmt(caps.per_symbol_usd, 0)})。
              </div>
            </>
          ) : (
            <div className="text-xs text-muted-foreground">风控 caps 未配置</div>
          )}
        </div>
      </Card>

      {/* 持仓(真实) */}
      <Card className="glass overflow-hidden">
        <div className="flex items-center justify-between px-4 pt-3.5 pb-3 border-b border-border/40">
          <div className="flex items-center gap-2">
            <span className="w-7 h-7 rounded-lg bg-loss/10 border border-loss/30 flex items-center justify-center text-loss">
              <Layers className="w-3.5 h-3.5" />
            </span>
            <span className="text-sm font-medium">交易所持仓(真实)</span>
            <Badge variant="secondary" className="text-xs">{positions.length} 笔</Badge>
          </div>
          <span className="text-xs text-muted-foreground">来源:Asterdex /fapi/v2</span>
        </div>
        <div className="p-4">
          {positions.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground text-sm border border-dashed border-border/40 rounded-lg">
              {bridge ? "暂无持仓" : "实盘 worker 未运行 —— 启动车道并配置 API Key 后此处显示真实持仓"}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr className="text-muted-foreground border-b border-border">
                    <th className="text-left">币种</th>
                    <th className="text-left">方向</th>
                    <th className="text-right">数量</th>
                    <th className="text-right">开仓价</th>
                    <th className="text-right">名义</th>
                    <th className="text-right">浮盈</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p, i) => (
                    <tr key={i} className="border-b border-border/40 last:border-0">
                      <td className="py-2 pr-2 font-medium">{p.symbol}</td>
                      <td className="py-2 pr-2">
                        <Badge className={cn("text-xs", p.side === "long" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss")}>
                          {p.side === "long" ? "多" : "空"}
                        </Badge>
                      </td>
                      <td className="py-2 pr-2 text-right num">{fmt(p.size, 4)}</td>
                      <td className="py-2 pr-2 text-right num">{fmt(p.entry_price, 6)}</td>
                      <td className="py-2 pr-2 text-right num">{fmtUsd(p.notional)}</td>
                      <td className={cn("py-2 pr-2 text-right num", (p.unrealized_pnl ?? 0) >= 0 ? "text-profit" : "text-loss")}>
                        {(p.unrealized_pnl ?? 0) >= 0 ? "+" : ""}{fmtUsd(p.unrealized_pnl, 4)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Card>

      {/* 交易所挂单(真实) */}
      <Card className="glass overflow-hidden">
        <div className="flex items-center justify-between px-4 pt-3.5 pb-3 border-b border-border/40">
          <div className="flex items-center gap-2">
            <span className="w-7 h-7 rounded-lg bg-loss/10 border border-loss/30 flex items-center justify-center text-loss">
              <Activity className="w-3.5 h-3.5" />
            </span>
            <span className="text-sm font-medium">交易所挂单(真实)</span>
            <Badge variant="secondary" className="text-xs">{orders.length} 笔</Badge>
          </div>
          <span className="text-xs text-muted-foreground">post-only · 减仓侧 reduce-only</span>
        </div>
        <div className="p-4">
          {orders.length === 0 ? (
            <div className="text-center py-6 text-muted-foreground text-sm border border-dashed border-border/40 rounded-lg">
              {bridge ? "暂无挂单" : "实盘 worker 未运行"}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr className="text-muted-foreground border-b border-border">
                    <th className="text-left">交易对</th>
                    <th className="text-left">方向</th>
                    <th className="text-right">价格</th>
                    <th className="text-right">数量</th>
                    <th className="text-left">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.map((o) => (
                    <tr key={o.id} className="border-b border-border/40 last:border-0">
                      <td className="py-2 pr-2 font-medium">{o.symbol}</td>
                      <td className="py-2 pr-2">
                        <Badge className={cn("text-xs", o.side === "buy" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss")}>
                          {o.side === "buy" ? "买" : "卖"}
                        </Badge>
                      </td>
                      <td className="py-2 pr-2 text-right num">{fmt(o.price, 6)}</td>
                      <td className="py-2 pr-2 text-right num">{fmt(o.amount, 4)}</td>
                      <td className="py-2 text-muted-foreground">{o.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Card>

      <div className="text-xs text-muted-foreground leading-relaxed">
        安全说明:实盘车道默认 stopped;启动需 API Key + 确认词;下单全部 post-only(减仓侧 reduce-only);
        连续拒单 {fmt(caps?.reject_break, 0)} 次自动熔断 {fmt(caps?.cooldown_s, 0)} 秒;日亏达
        {fmtUsd(dailyLossCap)} 自动停车。风控上限随权益浮动(总敞口 ≤2×权益、单币 ≤0.5×权益、
        日亏 ≤10%权益),绝对值只是天花板。所有实盘参数修改走 mm_apply_params(实盘车道),
        与模拟互不触碰。12h 判决口径:d1_verdict --lane mm_asterdex_live。
      </div>
    </div>
  );
}
