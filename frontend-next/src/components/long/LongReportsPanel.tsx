"use client";

/**
 * LongReportsPanel — 双车道周期报告面板（轮63 重构，2026-09-18）。
 *
 * ## 为什么重写
 *
 * 旧版是「外层 tab 选周期 + 卡片里没有任何周期身份」：
 *   - tab 只有「上线 / 长线」两个值，却去查后端 `horizon=midlong|long` 的**另一套**枚举；
 *   - 卡片标题只写「{日期} · {周期} 日报」，周期名来自外层 tab 而不是数据本身；
 *   - 后端返回的 section 键里还有前端词表没有的 `scalp`，一旦切到周报就渲染成 `undefined 周报`；
 *   - 旧版把同一个 `horizon` 变量既当查询参数又当显示名，两块语义打架时无从发现。
 *
 * 现在：**周期身份随数据一起下发**（`lane_identity`），前端不再自己维护一份周期映射。
 * 每条车道各自成卡，卡片顶部固定展示「车道名 · 主看周期 · 期望持仓」，两条车道的
 * 颜色/图标/持仓区间都不同，从结构上不可能再看混。
 *
 * 数据源：/api/period/lanes、/api/period/reports/daily?lane=、/api/period/reports/weekly、/api/period/cycles
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Loader2, RefreshCw, TrendingUp, Clock, AlertTriangle, Activity } from "lucide-react";
import { fetchPublic } from "@/lib/api";
import { cn } from "@/lib/utils";

type ViewTab = "daily" | "weekly" | "cycles";

/** 车道身份由后端下发；这里是兜底，仅在后端字段缺失时使用。 */
type LaneIdentity = {
  lane: string;
  label: string;
  label_full: string;
  tier?: string;
  nature?: string;
  report_timeframe: string;
  confirm_timeframes?: string[];
  expected_hold_hours?: number;
  hold_bracket_hours?: [number, number];
  actual_hold_hours?: number;
  hold_bracket_label?: string;
};

/** 车道视觉身份：两条车道配色/图标不同，避免只靠文字区分。 */
const LANE_STYLE: Record<string, { accent: string; chip: string; icon: typeof Clock }> = {
  intraday: {
    accent: "border-l-4 border-l-sky-500",
    chip: "bg-sky-50 text-sky-700 border-sky-200",
    icon: Clock,
  },
  trend: {
    accent: "border-l-4 border-l-violet-500",
    chip: "bg-violet-50 text-violet-700 border-violet-200",
    icon: TrendingUp,
  },
};

function laneStyle(lane: string) {
  return LANE_STYLE[lane] ?? {
    accent: "border-l-4 border-l-gray-400",
    chip: "bg-gray-50 text-gray-700 border-gray-200",
    icon: Activity,
  };
}

function fmtPnl(v: number | null | undefined) {
  const n = Number(v ?? 0);
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}`;
}

function pnlClass(v: number | null | undefined) {
  return Number(v ?? 0) >= 0 ? "text-profit" : "text-loss";
}

function fmtHours(v: number | null | undefined) {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n >= 48 ? `${(n / 24).toFixed(1)}d` : `${n.toFixed(1)}h`;
}

export function LongReportsPanel() {
  const [view, setView] = useState<ViewTab>("daily");
  const [lane, setLane] = useState<string>("intraday");
  const [laneCatalog, setLaneCatalog] = useState<LaneIdentity[]>([]);
  const [daily, setDaily] = useState<any>(null);
  const [weekly, setWeekly] = useState<any>(null);
  const [cycles, setCycles] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 车道词表来自后端（单一真源），前端不再硬编码周期枚举
  useEffect(() => {
    let alive = true;
    fetchPublic(`/period/lanes`)
      .then((r: any) => {
        if (!alive) return;
        const lanes: LaneIdentity[] = r?.lanes ?? [];
        setLaneCatalog(lanes);
        if (lanes.length && !lanes.some((l) => l.lane === lane)) {
          setLane(lanes[0].lane);
        }
      })
      .catch(() => {
        /* 词表拿不到时退回兜底常量 */
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (view === "daily") {
        const d = await fetchPublic(`/period/reports/daily?lane=${lane}&days=7`);
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
  }, [view, lane]);

  useEffect(() => {
    load();
  }, [load]);

  const laneOptions: LaneIdentity[] = useMemo(() => {
    if (laneCatalog.length) return laneCatalog;
    return [
      { lane: "intraday", label: "日内", label_full: "日内波段（中线槽位）", report_timeframe: "1h" },
      { lane: "trend", label: "长线趋势", label_full: "长线趋势", report_timeframe: "4h" },
    ];
  }, [laneCatalog]);

  const activeLane = laneOptions.find((l) => l.lane === lane) ?? laneOptions[0];

  return (
    <div className="space-y-4">
      {/* ── 视图切换 + 车道切换 ─────────────────────────────────────── */}
      <div className="space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          {(["daily", "weekly", "cycles"] as ViewTab[]).map((v) => (
            <Button key={v} variant={view === v ? "default" : "outline"} size="sm" onClick={() => setView(v)}>
              {v === "daily" ? "日报" : v === "weekly" ? "周报" : "趋势周期"}
            </Button>
          ))}
          <Button variant="ghost" size="sm" onClick={load} disabled={loading}>
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          </Button>
        </div>

        {/* 车道切换：每条按钮直接显示该车道的周期身份，切换前后都清楚自己在看哪条 */}
        {(view === "daily" || view === "weekly") && (
          <div className="flex items-stretch gap-2 flex-wrap">
            {laneOptions.map((l) => {
              const st = laneStyle(l.lane);
              const Icon = st.icon;
              const on = lane === l.lane;
              return (
                <button
                  key={l.lane}
                  onClick={() => setLane(l.lane)}
                  className={cn(
                    "text-left rounded-lg border px-3 py-2 transition-colors min-w-[210px]",
                    on ? "border-primary bg-primary/5" : "border-border hover:bg-muted/50",
                  )}
                >
                  <div className="flex items-center gap-1.5">
                    <Icon className={cn("w-3.5 h-3.5", on ? "text-primary" : "text-muted-foreground")} />
                    <span className="text-sm font-medium">{l.label_full}</span>
                  </div>
                  <div className="text-[11px] text-muted-foreground mt-0.5">
                    主看 {l.report_timeframe}
                    {l.confirm_timeframes?.length ? ` · 确认 ${l.confirm_timeframes.join("/")}` : ""}
                    {l.hold_bracket_label
                      ? ` · 期望持仓 ${l.hold_bracket_label}`
                      : l.expected_hold_hours
                        ? ` · 期望持仓 ${l.expected_hold_hours}h`
                        : ""}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {error && (
        <Card className="p-3 text-sm text-loss">
          加载失败：{error}（日报每日 08:05、周报每周一 08:30 由后台生成）
        </Card>
      )}

      {/* ── 日报 ───────────────────────────────────────────────────── */}
      {view === "daily" && daily && (
        <div className="space-y-3">
          {!!error === false && (daily.reports || []).length === 0 && (
            <Card className="p-4 text-sm text-muted-foreground">
              暂无「{activeLane?.label_full}」日报数据（每日 08:05 生成；也可用 rebuild_recent_reports 回填历史）。
            </Card>
          )}
          {(daily.reports || []).map((r: any, i: number) => (
            <DailyLaneCard key={`${r.date}-${r.lane}-${i}`} r={r} />
          ))}
        </div>
      )}

      {/* ── 周报 ───────────────────────────────────────────────────── */}
      {view === "weekly" && weekly && !weekly.error && (
        <div className="space-y-3">
          {(weekly.sections?.[lane] || weekly.sections?.[activeLane?.lane]) && (
            <WeeklyLaneCard
              lane={lane}
              sec={weekly.sections[lane] ?? weekly.sections[activeLane?.lane]}
              windowDays={weekly.window_days}
            />
          )}
          {/* 若后端还没产出该车道段，给一句明确说明而不是留白 */}
          {!weekly.sections?.[lane] && (
            <Card className="p-4 text-sm text-muted-foreground">
              周报里暂无「{activeLane?.label_full}」车道段（每周一 08:30 生成）。
            </Card>
          )}
        </div>
      )}
      {view === "weekly" && weekly?.error && <Card className="p-4 text-sm text-loss">{weekly.error}</Card>}

      {/* ── 趋势周期归档（仅长线趋势车道）────────────────────────────── */}
      {view === "cycles" && cycles && !cycles.error && <CyclesCard cycles={cycles} />}
      {view === "cycles" && cycles?.error && <Card className="p-4 text-sm text-loss">{cycles.error}</Card>}
    </div>
  );
}

/** 车道身份条：两个视图共用，保证任何时候都能看出「这是哪条车道」。 */
function LaneIdentityBar({ ident, right }: { ident?: LaneIdentity; right?: React.ReactNode }) {
  if (!ident) return null;
  const st = laneStyle(ident.lane);
  const Icon = st.icon;
  return (
    <div className="flex items-start justify-between gap-2">
      <div className="space-y-1">
        <div className="flex items-center gap-1.5">
          <Icon className="w-4 h-4 text-muted-foreground" />
          <span className="text-sm font-semibold">{ident.label_full}</span>
          <span className={cn("text-[10px] px-1.5 py-0.5 rounded border", st.chip)}>
            主看 {ident.report_timeframe}
          </span>
        </div>
        <div className="text-[11px] text-muted-foreground">
          期望持仓 {ident.hold_bracket_label ?? `${ident.expected_hold_hours ?? "—"}h`}
          {ident.tier || ident.nature ? ` · tier=${ident.tier ?? "—"} / nature=${ident.nature ?? "—"}` : ""}
        </div>
      </div>
      <div className="text-right shrink-0">{right}</div>
    </div>
  );
}

/** 持仓时长块：报告要能自证「这一段是不是这条周期该有的行为」。 */
function HoldBlock({ hold, ident }: { hold?: any; ident?: LaneIdentity }) {
  if (!hold || hold.n === 0) return <span className="text-muted-foreground">无平仓样本</span>;
  const bracket = ident?.hold_bracket_hours;
  const median = hold.median;
  // 中位持仓落在期望区间外 → 明确告警（这正是轮44 量化过的「周期错配」）
  const outside =
    bracket && median !== null && median !== undefined && (median > bracket[1] || median < bracket[0]);
  return (
    <span className={cn(outside && "text-warning")}>
      中位 {fmtHours(median)} · p90 {fmtHours(hold.p90)}
      {hold.over_24h > 0 ? ` · >24h ${hold.over_24h} 笔` : ""}
      {outside ? " ⚠ 偏离期望区间" : ""}
    </span>
  );
}

function LossBlock({ la }: { la: any }) {
  if (!la) return null;
  if (!la.active) {
    return <div className="text-xs text-muted-foreground">{la.note ?? "无亏损归因"}</div>;
  }
  return (
    <div className="rounded-md bg-loss/5 border border-loss/20 p-2 space-y-1">
      <div className="text-xs font-semibold text-loss">
        亏损归因（近 {la.window_days} 天：{la.total_pnl}，{la.n_losses}/{la.n_trades} 笔亏损）
      </div>
      {la.by_symbol?.length > 0 && (
        <div className="text-xs text-loss/90">
          币种：{la.by_symbol.map((x: any) => `${x.key}(${x.pnl}${x.n ? `/${x.n}笔` : ""})`).join("，")}
        </div>
      )}
      {la.by_exit_reason?.length > 0 && (
        <div className="text-xs text-loss/90">
          退出原因：{la.by_exit_reason.map((x: any) => `${x.key}(${x.pnl})`).join("，")}
        </div>
      )}
    </div>
  );
}

function QualityBlock({ q }: { q: any }) {
  if (!q || (!q.mismatched_labels && !q.untagged_trades)) return null;
  return (
    <div className="text-[11px] text-warning flex items-start gap-1">
      <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />
      <span>
        {q.mismatched_labels ? `${q.mismatched_labels} 笔标签矛盾` : ""}
        {q.mismatched_labels && q.untagged_trades ? "；" : ""}
        {q.untagged_trades ? `${q.untagged_trades} 笔无 tier/nature 标签` : ""}
        （已按 nature 优先归入本车道）
      </span>
    </div>
  );
}

function DailyLaneCard({ r }: { r: any }) {
  const p = r.payload || {};
  const ident: LaneIdentity | undefined = r.lane_identity ?? p.lane_identity;
  const trades = p.trades_24h || {};
  const poss: any[] = p.open_positions || [];
  const st = laneStyle(ident?.lane ?? r.lane);
  return (
    <Card className={cn("p-4 space-y-2", st.accent)}>
      <LaneIdentityBar
        ident={ident}
        right={
          <>
            <div className="text-[11px] text-muted-foreground">{r.date}</div>
            <div className={cn("text-sm font-semibold", pnlClass(trades.total_pnl))}>
              24h {fmtPnl(trades.total_pnl)}
            </div>
          </>
        }
      />
      <div className="grid grid-cols-3 gap-2 text-xs text-muted-foreground border-t pt-2">
        <span>平仓 {trades.n_closed ?? 0} 笔</span>
        <span>胜率 {trades.win_rate ? (trades.win_rate * 100).toFixed(0) : 0}%</span>
        <span>持仓 {poss.length} 个</span>
      </div>
      <div className="text-xs text-muted-foreground flex items-center gap-1">
        <Clock className="w-3 h-3" />
        <HoldBlock hold={trades.hold_hours} ident={ident} />
      </div>

      {/* 在手仓位：显式标出引擎写下的 nature/tier，避免「报告说中线、引擎写 scalp」二次错位 */}
      {poss.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {poss.map((pos, i) => (
            <span
              key={`${pos.symbol}-${i}`}
              className="text-xs px-2 py-0.5 rounded border bg-muted/40 border-border"
              title={`tier=${pos.tier ?? "—"} nature=${pos.nature ?? "—"} 已持有 ${fmtHours(pos.held_hours)}`}
            >
              {pos.symbol} <span className="text-muted-foreground">{pos.side}</span>{" "}
              <span className={pnlClass(pos.unrealized_pnl)}>{fmtPnl(pos.unrealized_pnl)}</span>
              {pos.held_hours != null && (
                <span className="text-muted-foreground"> · {fmtHours(pos.held_hours)}</span>
              )}
            </span>
          ))}
        </div>
      )}

      {ident?.lane === "trend" && p.l1_panel && (
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(p.l1_panel).map(([sym, c]: [string, any]) => (
            <span
              key={sym}
              className={cn(
                "text-xs px-2 py-0.5 rounded border",
                c.state === "up"
                  ? "bg-profit/10 border-profit/30 text-profit"
                  : c.state === "down"
                    ? "bg-loss/10 border-loss/30 text-loss"
                    : "bg-muted border-border text-muted-foreground",
              )}
            >
              {sym} {c.state}({c.score})
            </span>
          ))}
        </div>
      )}

      {ident?.lane === "trend" && (p.actions_24h || []).length > 0 && (
        <div className="text-xs">
          <span className="text-muted-foreground">动作流水：</span>
          {(p.actions_24h as any[]).slice(0, 8).map((a, i) => (
            <span key={i} className="mr-2">
              {a.symbol}·{a.action}
            </span>
          ))}
        </div>
      )}

      {ident?.lane === "intraday" && p.exit_stats?.total_exits > 0 && (
        <div className="text-xs text-muted-foreground">
          退出事件 {p.exit_stats.total_exits} 次 · 超时平仓 {p.exit_stats.max_hold_timeout} 次
        </div>
      )}

      <LossBlock la={p.loss_attribution} />
      <QualityBlock q={p.data_quality} />
      {r.llm_summary && (
        <p className="text-xs whitespace-pre-wrap border-t pt-2 text-muted-foreground">{r.llm_summary}</p>
      )}
    </Card>
  );
}

function WeeklyLaneCard({ lane, sec, windowDays }: { lane: string; sec: any; windowDays?: number }) {
  const ident: LaneIdentity | undefined = sec?.lane_identity;
  const trades = sec?.trades_7d || {};
  const st = laneStyle(ident?.lane ?? lane);
  return (
    <Card className={cn("p-4 space-y-2", st.accent)}>
      <LaneIdentityBar
        ident={ident}
        right={
          <>
            <div className="text-[11px] text-muted-foreground">近 {windowDays ?? 7} 天</div>
            <div className={cn("text-sm font-semibold", pnlClass(trades.total_pnl))}>
              {windowDays ?? 7}d {fmtPnl(trades.total_pnl)}
            </div>
          </>
        }
      />
      <div className="grid grid-cols-3 gap-2 text-xs text-muted-foreground border-t pt-2">
        <span>平仓 {trades.n_closed ?? 0} 笔</span>
        <span>胜率 {trades.win_rate ? (trades.win_rate * 100).toFixed(0) : 0}%</span>
        <span>持仓 {sec?.open_positions?.length ?? 0} 个</span>
      </div>
      <div className="text-xs text-muted-foreground flex items-center gap-1">
        <Clock className="w-3 h-3" />
        <HoldBlock hold={trades.hold_hours} ident={ident} />
      </div>
      {sec?.trend_cycles && !sec.trend_cycles.error && (
        <div className="grid grid-cols-4 gap-2 text-xs text-muted-foreground">
          <span>周期 {sec.trend_cycles.cycles ?? 0}</span>
          <span>总 R {sec.trend_cycles.total_r ?? 0}</span>
          <span>均值 R {sec.trend_cycles.mean_r ?? 0}</span>
          <span>
            胜率{" "}
            {sec.trend_cycles.win_rate != null ? (sec.trend_cycles.win_rate * 100).toFixed(0) : 0}%
          </span>
        </div>
      )}
      <LossBlock la={sec?.loss_attribution} />
      {sec?.llm_summary && (
        <p className="text-xs whitespace-pre-wrap border-t pt-2 text-muted-foreground">{sec.llm_summary}</p>
      )}
    </Card>
  );
}

function CyclesCard({ cycles }: { cycles: any }) {
  const ident: LaneIdentity | undefined = cycles.lane_identity;
  return (
    <Card className="p-4 space-y-2">
      <LaneIdentityBar ident={ident} right={<span className="text-[11px] text-muted-foreground">TrendCycle 归档</span>} />
      <p className="text-[11px] text-muted-foreground border-t pt-2">
        仅长线趋势车道归档；日内车道不产生趋势周期（它的持仓本身在一个交易日内结束）。
      </p>
      {cycles.stats && (
        <div className="grid grid-cols-4 gap-2 text-xs text-muted-foreground">
          <span>周期数 {cycles.stats.n}</span>
          <span>总 R {cycles.stats.total_r}</span>
          <span>均值 R {cycles.stats.mean_r}</span>
          <span>胜率 {(cycles.stats.win_rate * 100).toFixed(0)}%</span>
        </div>
      )}
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-muted-foreground border-b">
            <th className="py-1">币</th>
            <th>开始</th>
            <th>结束</th>
            <th>总R</th>
            <th>峰值R</th>
            <th>持有天</th>
            <th>退出原因</th>
          </tr>
        </thead>
        <tbody>
          {(cycles.cycles || []).map((c: any) => (
            <tr key={c.id} className="border-b last:border-0">
              <td className="py-1 font-medium">{c.symbol}</td>
              <td>{String(c.start_ts).slice(0, 10)}</td>
              <td>{c.end_ts ? String(c.end_ts).slice(0, 10) : "—"}</td>
              <td className={pnlClass(c.total_r)}>{c.total_r ?? "—"}</td>
              <td>{c.peak_r ?? "—"}</td>
              <td>{c.hold_days != null ? Number(c.hold_days).toFixed(1) : "—"}</td>
              <td className="text-muted-foreground">{c.exit_reason ?? "—"}</td>
            </tr>
          ))}
          {(!cycles.cycles || cycles.cycles.length === 0) && (
            <tr>
              <td colSpan={7} className="py-3 text-muted-foreground text-center">
                暂无归档（长线趋势平仓后自动归档）
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </Card>
  );
}
