"use client";

/**
 * LongReportsPanel — 三周期报告观测面板（日报/周报/趋势周期）。
 * 数据源：/api/period/reports/daily|weekly、/api/period/cycles（fetchPublic 走运行时后端地址）。
 */
import { useCallback, useEffect, useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Loader2, RefreshCw, TrendingUp } from "lucide-react";
import { fetchPublic } from "@/lib/api";
import { cn } from "@/lib/utils";

type Horizon = "scalp" | "midlong" | "long";
type ViewTab = "daily" | "weekly" | "cycles";

const HORIZON_LABEL: Record<Horizon, string> = { scalp: "短线", midlong: "中线", long: "长线" };

export function LongReportsPanel() {
  const [view, setView] = useState<ViewTab>("daily");
  const [horizon, setHorizon] = useState<Horizon>("long");
  const [daily, setDaily] = useState<any>(null);
  const [weekly, setWeekly] = useState<any>(null);
  const [cycles, setCycles] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (view === "daily") {
        const d = await fetchPublic(`/period/reports/daily?horizon=${horizon}&days=7`);
        setDaily(d);
      } else if (view === "weekly") {
        const w = await fetchPublic(`/period/reports/weekly`);
        setWeekly(w);
      } else {
        const c = await fetchPublic(`/period/cycles`);
        setCycles(c);
      }
    } catch (e: any) {
      setError(String(e?.message || e || "加载失败"));
    } finally {
      setLoading(false);
    }
  }, [view, horizon]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 flex-wrap">
        {(["daily", "weekly", "cycles"] as ViewTab[]).map((v) => (
          <Button
            key={v}
            variant={view === v ? "default" : "outline"}
            size="sm"
            onClick={() => setView(v)}
          >
            {v === "daily" ? "日报" : v === "weekly" ? "周报" : "趋势周期"}
          </Button>
        ))}
        {view === "daily" && (
          <div className="flex items-center gap-1 ml-2">
            {(Object.keys(HORIZON_LABEL) as Horizon[]).map((h) => (
              <Button
                key={h}
                variant={horizon === h ? "secondary" : "ghost"}
                size="sm"
                onClick={() => setHorizon(h)}
              >
                {HORIZON_LABEL[h]}
              </Button>
            ))}
          </div>
        )}
        <Button variant="ghost" size="sm" onClick={load} disabled={loading}>
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
        </Button>
      </div>

      {error && <Card className="p-3 text-sm text-red-500">加载失败：{error}（日报/周报在每日 08:05 / 周一 08:30 由后台生成）</Card>}

      {view === "daily" && daily && (
        <div className="space-y-3">
          {((daily.reports || []) as any[]).map((r, i) => (
            <DailyCard key={i} r={r} horizon={horizon} />
          ))}
          {(!daily.reports || daily.reports.length === 0) && (
            <Card className="p-4 text-sm text-muted-foreground">
              暂无 {HORIZON_LABEL[horizon]} 日报数据（每日 08:05 生成，首次需等后端重启后生效）。
            </Card>
          )}
        </div>
      )}

      {view === "weekly" && weekly && !weekly.error && (
        <div className="space-y-3">
          {(Object.keys(weekly.sections || {}) as Horizon[]).map((h) => (
            <WeeklyCard key={h} horizon={h} sec={weekly.sections[h]} />
          ))}
          {weekly.llm_summary && (
            <Card className="p-4">
              <div className="text-sm font-medium mb-1 flex items-center gap-1"><TrendingUp className="w-4 h-4" /> LLM 周报总结</div>
              <p className="text-sm whitespace-pre-wrap text-muted-foreground">{weekly.llm_summary}</p>
            </Card>
          )}
        </div>
      )}
      {view === "weekly" && weekly?.error && (
        <Card className="p-4 text-sm text-red-500">{weekly.error}</Card>
      )}

      {view === "cycles" && cycles && !cycles.error && (
        <Card className="p-4">
          <div className="text-sm font-medium mb-2">趋势周期（TrendCycle 归档）</div>
          {cycles.stats && (
            <div className="grid grid-cols-4 gap-2 mb-3 text-xs text-muted-foreground">
              <span>周期数 {cycles.stats.n}</span>
              <span>总 R {cycles.stats.total_r}</span>
              <span>均值 R {cycles.stats.mean_r}</span>
              <span>胜率 {(cycles.stats.win_rate * 100).toFixed(0)}%</span>
            </div>
          )}
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-muted-foreground border-b">
                <th className="py-1">币</th><th>开始</th><th>结束</th>
                <th>总R</th><th>峰值R</th><th>持有天</th><th>退出原因</th>
              </tr>
            </thead>
            <tbody>
              {(cycles.cycles || []).map((c: any) => (
                <tr key={c.id} className="border-b last:border-0">
                  <td className="py-1 font-medium">{c.symbol}</td>
                  <td>{String(c.start_ts).slice(0, 10)}</td>
                  <td>{c.end_ts ? String(c.end_ts).slice(0, 10) : "—"}</td>
                  <td className={c.total_r >= 0 ? "text-green-600" : "text-red-600"}>{c.total_r ?? "—"}</td>
                  <td>{c.peak_r ?? "—"}</td>
                  <td>{c.hold_days != null ? Number(c.hold_days).toFixed(1) : "—"}</td>
                  <td className="text-muted-foreground">{c.exit_reason ?? "—"}</td>
                </tr>
              ))}
              {(!cycles.cycles || cycles.cycles.length === 0) && (
                <tr><td colSpan={7} className="py-3 text-muted-foreground text-center">暂无归档（长线平仓后自动归档）</td></tr>
              )}
            </tbody>
          </table>
        </Card>
      )}
      {view === "cycles" && cycles?.error && (
        <Card className="p-4 text-sm text-red-500">{cycles.error}</Card>
      )}
    </div>
  );
}

function LossBlock({ la }: { la: any }) {
  if (!la) return null;
  if (!la.active) {
    return <div className="text-xs text-muted-foreground mt-1">{la.note}</div>;
  }
  return (
    <div className="mt-2 rounded-md bg-red-50 border border-red-200 p-2">
      <div className="text-xs font-semibold text-red-700 mb-1">
        亏损归因（近 {la.window_days} 天：{la.total_pnl}，{la.n_losses}/{la.n_trades} 笔亏损）
      </div>
      {la.by_symbol?.length > 0 && (
        <div className="text-xs text-red-600">币种：{la.by_symbol.map((x: any) => `${x.key}(${x.pnl})`).join("，")}</div>
      )}
      {la.by_exit_reason?.length > 0 && (
        <div className="text-xs text-red-600">退出原因：{la.by_exit_reason.map((x: any) => `${x.key}(${x.pnl})`).join("，")}</div>
      )}
    </div>
  );
}

function DailyCard({ r, horizon }: { r: any; horizon: Horizon }) {
  const p = r.payload || {};
  const trades = p.trades_24h || {};
  const poss = p.open_positions || [];
  return (
    <Card className="p-4 space-y-2">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium">{r.date} · {HORIZON_LABEL[horizon]} 日报</div>
        <div className={cn("text-sm font-semibold", (trades.total_pnl ?? 0) >= 0 ? "text-green-600" : "text-red-600")}>
          24h PnL {trades.total_pnl ?? 0}
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2 text-xs text-muted-foreground">
        <span>平仓 {trades.n_closed ?? 0} 笔</span>
        <span>胜率 {trades.win_rate ? (trades.win_rate * 100).toFixed(0) : 0}%</span>
        <span>持仓 {poss.length} 个</span>
      </div>
      {horizon === "long" && p.l1_panel && (
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(p.l1_panel).map(([sym, c]: [string, any]) => (
            <span key={sym} className={cn(
              "text-xs px-2 py-0.5 rounded border",
              c.state === "up" ? "bg-green-50 border-green-200 text-green-700" :
              c.state === "down" ? "bg-red-50 border-red-200 text-red-700" :
              "bg-gray-50 border-gray-200 text-gray-600"
            )}>
              {sym} {c.state}({c.score})
            </span>
          ))}
        </div>
      )}
      {horizon === "long" && (p.actions_24h || []).length > 0 && (
        <div className="text-xs">
          <span className="text-muted-foreground">动作流水：</span>
          {(p.actions_24h as any[]).slice(0, 8).map((a, i) => (
            <span key={i} className="mr-2">{a.symbol}·{a.action}</span>
          ))}
        </div>
      )}
      <LossBlock la={p.loss_attribution} />
      {r.llm_summary && <p className="text-xs whitespace-pre-wrap border-t pt-2 text-muted-foreground">{r.llm_summary}</p>}
    </Card>
  );
}

function WeeklyCard({ horizon, sec }: { horizon: Horizon; sec: any }) {
  const trades = sec?.trades_7d || {};
  const la = sec?.loss_attribution;
  return (
    <Card className="p-4 space-y-2">
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium">{HORIZON_LABEL[horizon]} 周报</div>
        <div className={cn("text-sm font-semibold", (trades.total_pnl ?? 0) >= 0 ? "text-green-600" : "text-red-600")}>
          7d PnL {trades.total_pnl ?? 0}
        </div>
      </div>
      <div className="grid grid-cols-3 gap-2 text-xs text-muted-foreground">
        <span>平仓 {trades.n_closed ?? 0} 笔</span>
        <span>胜率 {trades.win_rate ? (trades.win_rate * 100).toFixed(0) : 0}%</span>
        <span>持仓 {sec?.open_positions?.length ?? 0} 个</span>
      </div>
      {horizon === "long" && sec?.trend_cycles && !sec.trend_cycles.error && (
        <div className="grid grid-cols-4 gap-2 text-xs text-muted-foreground">
          <span>周期 {sec.trend_cycles.cycles ?? 0}</span>
          <span>总 R {sec.trend_cycles.total_r ?? 0}</span>
          <span>均值 R {sec.trend_cycles.mean_r ?? 0}</span>
          <span>胜率 {sec.trend_cycles.win_rate != null ? (sec.trend_cycles.win_rate * 100).toFixed(0) : 0}%</span>
        </div>
      )}
      <LossBlock la={la} />
    </Card>
  );
}