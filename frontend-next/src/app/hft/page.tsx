"use client";

/**
 * [F250] 中短期高频交易模块 · 总览页
 *
 * ## 与套利中心的关系（设计约束）
 *
 * 用户要求：**新模块，以 L1 赛道为借鉴，不用套利中心**。
 * 这里的"借鉴"是**复用实现**（深度梯组件、tick 数据层、选币链路），
 * 而不是"挂在套利中心的页面/入口下"。本页在顶层导航 `/hft` 独立可见。
 *
 * ## 页面结构（三块，对应模块的三层）
 *
 *   ① 宇宙 —— 固定币（人工指定）∪ AI 选币（机械评分选出）；含硬闸拒绝原因
 *   ② 深度 —— 20 档真实价量价格梯（上下显示），我方挂单高亮 + 队列前方量
 *   ③ 执行 —— 当前挂单/持仓/最近成交（数据来自运行态，无则留白）
 *
 * ## 一条必须遵守的展示原则
 *
 * **只展示真实存在的数据。** 无深度采集的币如实留白（不画假深度）；
 * 运行态取不到时显示"—"而不是 0（0 会被误读为"没有持仓"）。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Layers, Radio, Target } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { fmtNum } from "@/lib/format";
import { DataState, PageShell, TradingBoard } from "@/components/arbitrage";
import { HftControlPanel } from "@/components/hft/HftControlPanel";
import { EquitySeriesCard } from "@/components/charts/EquitySeriesCard";
import { HftFillsTable } from "@/components/hft/HftFillsTable";
import { HftPositionsPanel } from "@/components/hft/HftPositionsPanel";
import {
  useHftOverview, useHftBoard, useHftUniverseLive, useHftAccount, useHftConfig, useHftFills,
  useHftEvolution, useHftHero,
} from "@/hooks/useHftData";

function isStaleTs(ts: number | null, ms = 120_000): boolean {
  return ts != null && Date.now() - ts > ms;
}

/** 选币器全量评估间隔（秒）。与后端 MM_UNIVERSE_RADAR_SEC 一致(实时化后=60s)。 */
const EVAL_INTERVAL_SEC = 60;
function useNextEvalCountdown(universeAsOf?: string | null): number | null {
  const [left, setLeft] = useState<number | null>(null);
  useEffect(() => {
    if (!universeAsOf) { setLeft(null); return; }
    const last = new Date(universeAsOf).getTime() / 1000;
    if (!Number.isFinite(last)) { setLeft(null); return; }
    const tick = () => {
      const elapsed = Date.now() / 1000 - last;
      // 超过间隔 ⇒ 按「距上次过去多久」对间隔取模,继续倒数(不卡在「评估中」)
      const remain = EVAL_INTERVAL_SEC - (elapsed % EVAL_INTERVAL_SEC);
      setLeft(Math.max(0, Math.round(remain)));
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [universeAsOf]);
  return left;
}

/** 模式/状态徽章（本模块自带，不复用套利中心的三份拷贝映射） */
const MODE_TONE: Record<string, string> = {
  paper: "border-cyan-400/30 bg-cyan-400/10 text-cyan-300",
  live: "border-loss/30 bg-loss/10 text-loss",
  disabled: "border-muted/40 bg-muted/20 text-muted-foreground",
};
const STATUS_TONE: Record<string, string> = {
  active: "border-profit/30 bg-profit/10 text-profit",
  paused: "border-warning/30 bg-warning/10 text-warning",
  stopped: "border-muted/40 bg-muted/20 text-muted-foreground",
};

function Badge({ label, tone }: { label: string; tone: string }) {
  return (
    <span className={cn("rounded border px-1.5 py-[1px] text-[10px] font-medium", tone)}>{label}</span>
  );
}

export default function HftPage() {
  // [2026-09-23] 深度区折叠状态（默认折叠：20 档全展开太占地方）
  const [depthExpanded, setDepthExpanded] = useState(false);
  const overview = useHftOverview();
  const hero = useHftHero();
  const evo = useHftEvolution();
  // [2026-09-27] 分币种实盘判定取代旧的「选币评分 + 硬闸拒绝」两张卡（已停用口径）
  const live = useHftUniverseLive();
  const account = useHftAccount();
  const config = useHftConfig(true);
  const fills = useHftFills(50, 24);
  // 深度梯直接跟随宇宙（overview 未就绪时传 undefined ⇒ 后端用默认宇宙）
  const symbols = useMemo(() => overview.data?.symbols ?? undefined, [overview.data]);
  const board = useHftBoard(symbols, 20);

  const ov = overview.data;
  const nextEvalSec = useNextEvalCountdown(ov?.universe_as_of);

  const onChanged = useCallback(() => {
    account.refresh();
    overview.refresh();
    config.refresh();
    fills.refresh();
  }, [account, overview, config, fills]);

  return (
    <PageShell
      title="模拟高频交易"
      subtitle="30s–5min 高频形态组合 · 形态触发 × OFI 流同向（统一流定律）· 纸面资金"
      icon={<Radio className="h-4 w-4" />}
      mode="polling"
      asOf={ov?.as_of ?? null}
      onRefresh={() => { overview.refresh(); live.refresh(); board.refresh(); account.refresh(); config.refresh(); }}
      refreshing={overview.loading && !overview.data}
      breadcrumb={[{ label: "交易核心" }, { label: "实盘高频交易" }, { label: "模拟高频交易" }]}
      className="mx-auto w-full max-w-[1600px]"
    >
      {/* ══ 第1层 · 今日战绩（超大字，占满一屏宽，一眼看清赚没赚） ══ */}
      {hero.data && (
        <Card className="glass px-6 py-6">
          <div className="flex flex-wrap items-center gap-x-12 gap-y-5">
            {/* 今日盈亏：超大字 + 在线状态灯 */}
            <div className="flex items-center gap-4">
              <span
                className={cn(
                  "h-4 w-4 flex-shrink-0 rounded-full",
                  hero.data.worker_alive ? "animate-pulse bg-profit" : "bg-loss"
                )}
                title={hero.data.worker_alive ? "交易程序在线" : "交易程序离线！"}
              />
              <div>
                <div className="text-base text-muted-foreground">今日盈亏</div>
                <div
                  className={cn(
                    "font-mono text-7xl font-extrabold leading-none tabular-nums",
                    hero.data.today_pnl_usd > 0
                      ? "text-profit"
                      : hero.data.today_pnl_usd < 0
                        ? "text-loss"
                        : "text-foreground"
                  )}
                >
                  {hero.data.today_pnl_usd >= 0 ? "+" : "−"}$
                  {Math.abs(hero.data.today_pnl_usd).toFixed(2)}
                </div>
              </div>
            </div>
            <div className="h-16 w-px bg-muted/40" />
            <div>
              <div className="text-sm text-muted-foreground">账户权益</div>
              <div className="font-mono text-4xl font-bold tabular-nums">${(hero.data.equity ?? 0).toFixed(2)}</div>
            </div>
            <div>
              <div className="text-sm text-muted-foreground">今日胜率</div>
              <div className="font-mono text-4xl font-bold tabular-nums">{((hero.data.win_rate ?? 0) * 100).toFixed(0)}%</div>
            </div>
            <div>
              <div className="text-sm text-muted-foreground">盈亏比</div>
              <div className={cn("font-mono text-4xl font-bold tabular-nums", (hero.data.pl_ratio ?? 0) >= 1 ? "text-profit" : "text-loss")}>
                {(hero.data.pl_ratio ?? 0).toFixed(2)}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                赢 {(hero.data.avg_win_bp ?? 0).toFixed(1)}bp / 亏 {Math.abs(hero.data.avg_loss_bp ?? 0).toFixed(1)}bp
              </div>
            </div>
            <div>
              <div className="text-sm text-muted-foreground">今日成交</div>
              <div className="font-mono text-4xl font-bold tabular-nums">
                {hero.data.today_fills}<span className="text-lg font-normal text-muted-foreground"> 笔</span>
              </div>
            </div>
            <div className="h-16 w-px bg-muted/40" />
            <div>
              <div className="text-sm text-muted-foreground">总盈亏</div>
              <div className={cn("font-mono text-4xl font-bold tabular-nums",
                (hero.data.total_pnl_usd ?? 0) > 0 ? "text-profit" : (hero.data.total_pnl_usd ?? 0) < 0 ? "text-loss" : "text-foreground")}>
                {hero.data.total_pnl_usd == null ? "—"
                  : `${hero.data.total_pnl_usd >= 0 ? "+" : "−"}$${Math.abs(hero.data.total_pnl_usd).toFixed(2)}`}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">自上次重置</div>
            </div>
            <div>
              <div className="text-sm text-muted-foreground">总手续费</div>
              <div className="font-mono text-4xl font-bold tabular-nums text-loss">
                {hero.data.fee_lifetime_usd == null ? "—"
                  : `−$${Math.abs(hero.data.fee_lifetime_usd).toFixed(2)}`}
              </div>
              <div className="mt-1 text-xs text-muted-foreground">自上次重置 · 挂单免费</div>
            </div>
          </div>
        </Card>
      )}

      {/* ══ 第3层 · 交易宇宙 + 趋势分 + 倒计时 + 操作（提到前面，紧急能马上按） ══ */}
      <Card className="glass px-4 py-3">
        <DataState
          loading={overview.loading}
          error={overview.error}
          hasData={!!ov}
          onRetry={overview.refresh}
          stale={isStaleTs(overview.lastUpdated, 180_000)}
        >
          {ov && (
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
              <span className="flex items-center gap-2">
                <span className="text-base font-semibold">{ov.lane_id}</span>
                {ov.mode && <Badge label={ov.mode} tone={MODE_TONE[ov.mode] ?? "border-muted/40 bg-muted/20"} />}
                {ov.status && <Badge label={ov.status} tone={STATUS_TONE[ov.status] ?? "border-muted/40 bg-muted/20"} />}
                <span className="text-xs text-muted-foreground">maker 0% / taker 4bp</span>
              </span>
              <span className="text-sm text-muted-foreground">
                交易宇宙{" "}
                {(ov.symbols ?? []).map((s) => {
                  const t = (ov as any).trend?.[s];
                  const arrow = t?.dir === "up" ? "↑" : t?.dir === "down" ? "↓" : "·";
                  const tone = t?.dir === "up" ? "text-profit" : t?.dir === "down" ? "text-loss" : "text-muted-foreground";
                  return (
                    <span key={s} className="mr-2.5 text-base font-semibold text-foreground" title={t ? `趋势强度 ${t.score}` : ""}>
                      {s}
                      {t && <span className={cn("ml-1 text-xs", tone)}>{arrow}{t.score}</span>}
                    </span>
                  );
                })}
              </span>
              {ov.universe_source === "universe_optimizer" ? (
                <span className="text-sm text-muted-foreground">
                  末位淘汰 <span className="text-foreground">趋势强度换币</span>
                  {ov.universe_as_of ? ` · 上次 ${ov.universe_as_of.slice(11, 16)}` : ""}
                  {nextEvalSec != null && (
                    <span className="font-mono font-semibold text-cyan-300">
                      {" · 下次 "}
                      {nextEvalSec <= 0
                        ? "评估中…"
                        : `${Math.floor(nextEvalSec / 60)}:${String(nextEvalSec % 60).padStart(2, "0")}`}
                    </span>
                  )}
                </span>
              ) : ov.universe_source === "v5_radar" ? (
                <span className="text-muted-foreground">
                  选币口径 <span className="text-foreground">v5 滚动质量雷达</span>
                  {ov.universe_as_of ? ` · 上次评估 ${ov.universe_as_of.slice(11, 16)}` : ""}
                </span>
              ) : ov.universe_source === "event_study" ? (
                <span className="text-muted-foreground">
                  选币口径 <span className="text-foreground">事件研究排名</span>
                  {ov.trial?.judge_at ? ` · 判定 ${ov.trial.judge_at.slice(11, 16)}` : ""}
                </span>
              ) : (
                <span className="text-muted-foreground">
                  固定 <span className="text-foreground">{(ov.fixed ?? []).length}</span>
                  {" · "}AI 选币 <span className="text-foreground">{(ov.ai ?? []).join(" ") || "—"}</span>
                </span>
              )}
              <span className="text-sm text-muted-foreground"
                title="深度覆盖 = 现役宇宙里有 20 档深度的币 / 宇宙总数；采集池 = 深度数据采集配置的候选币数（不是在交易的币数）">
                深度覆盖 <span className="text-foreground">{ov.depth_in_universe?.length ?? 0}/{ov.symbols.length}</span>
              </span>
            </div>
          )}
        </DataState>
      </Card>

      {/* ══ 第2层 · 当前在干什么：持仓 + 最近成交（左右两栏） ══ */}

      {/* ── [h749] 熔断警报横幅:车道被暂停时红条提示原因与复开时间 ── */}
      {evo.data && evo.data.lane_pause && evo.data.lane_pause.last && (
        <Card className="glass border-loss/50 bg-loss/10 px-4 py-2">
          <div className="flex items-center gap-2 text-[12px] font-semibold text-loss">
            <span>⚠️ 车道暂停中</span>
            <span className="font-normal text-loss/90">
              {evo.data.lane_pause.last.reason}
              {evo.data.lane_pause.last.symbol ? ` · ${evo.data.lane_pause.last.symbol}` : ""}
            </span>
          </div>
        </Card>
      )}

      {/* ── [h722] 自进化层状态条:连续正净/闸门提案/bandit/待判/定律 ── */}
      {evo.data && (
        <Card className="glass px-4 py-2">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-[11px]">
            <span className="text-muted-foreground">
              连续正净 <span className={cn("font-semibold", evo.data.streak_positive_days >= 5 ? "text-profit" : "text-foreground")}>
                {evo.data.streak_positive_days}/5 天
              </span>
            </span>
            <span className="text-muted-foreground">
              闸门提案 <span className="font-medium text-foreground">{evo.data.gate_proposals.length}</span>
              {evo.data.gate_proposals.length > 0 && (
                <span className="text-warning">
                  {" · "}{evo.data.gate_proposals.map((g) => `${g.param} ${g.current}→${g.proposed} (t=${g.t})`).join("; ")}
                </span>
              )}
            </span>
            {evo.data.bandit.proposed.length > 0 && (
              <span className="text-muted-foreground">
                bandit 提案 <span className="text-foreground">{evo.data.bandit.proposed.join(" ")}</span>
                {" · 换入 "}<span className="text-profit">{evo.data.bandit.new_in.join(" ") || "—"}</span>
                {" · 换出 "}<span className="text-loss">{evo.data.bandit.dropped.join(" ") || "—"}</span>
              </span>
            )}
            <span className="text-muted-foreground">
              待判 <span className="font-medium text-foreground">{evo.data.pending_verdicts.length}</span>
              {evo.data.pending_verdicts.length > 0 && (
                <span className="text-muted-foreground">
                  {" · "}{evo.data.pending_verdicts.map((p) => `${p.param}=${p.new}`).join(" ")}
                </span>
              )}
            </span>
            <span className="text-muted-foreground">
              定律 <span className="text-foreground">{evo.data.playbook_laws}</span>
            </span>
            {evo.data.markout_kpi.verdict && (
              <span className="text-muted-foreground">
                逆向/捕获 <span className={cn("font-medium",
                  evo.data.markout_kpi.verdict.startsWith("healthy") ? "text-profit"
                  : evo.data.markout_kpi.verdict.startsWith("warn") ? "text-warning"
                  : "text-loss")}>
                  {evo.data.markout_kpi.adverse_capture_ratio?.toFixed(2)}
                </span>
                <span className="text-[10px] text-muted-foreground">
                  {" "}({evo.data.markout_kpi.verdict})
                </span>
              </span>
            )}
          </div>
        </Card>
      )}

      {/* ── 第2层 · 当前在干什么：持仓 + 最近成交（左右两栏，大字浮盈） ──
          账户是 2s（浮动盈亏随行情变），持仓块与深度同源同节奏；
          `stateAgeMs` / `stateSource` 如实透传（运行态来自 worker 快照,有 0~15s 滞后）。 */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <HftPositionsPanel
          cards={board.data?.cards}
          stateAgeMs={board.data?.state_age_ms}
          stateSource={board.data?.state_source}
        />
        <HftFillsTable data={fills.data} hours={24} />
      </div>

      {/* ══ 第4层 · 账户与控制（折叠，要看再展开） ══ */}
      <details className="group rounded-lg border border-border/40">
        <summary className="cursor-pointer select-none px-4 py-3 text-sm font-semibold text-muted-foreground hover:text-foreground">
          账户与控制（模拟账户 / 开关 / 金额 / 参数）—— 点击展开
        </summary>
        <div className="px-1 pb-2">
          <HftControlPanel
            account={account.data}
            config={config.data}
            onChanged={onChanged}
            disabled={!account.data}
          />
        </div>
      </details>

      {/* ── 第4层 · 实时深度（折叠） ── */}
      <Card className="glass p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <span className="flex h-6 w-6 items-center justify-center rounded-lg border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
              <Layers className="h-3.5 w-3.5" />
            </span>
            实时深度（20 档 · 我方挂单高亮）
          </h2>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-muted-foreground">2s 刷新</span>
            {/* [2026-09-23 用户反馈] 20 档全展开太占地方 ⇒ 默认折叠到 300px，按需展开。
                只在本页折叠，不改共享的 TradingBoard/DepthLadder（套利页仍全展开）。 */}
            <button
              type="button"
              onClick={() => setDepthExpanded((v) => !v)}
              data-testid="depth-toggle"
              className="rounded border border-border/50 px-2 py-0.5 text-[10px] text-muted-foreground hover:text-foreground"
            >
              {depthExpanded ? "收起" : "展开全部 20 档"}
            </button>
          </div>
        </div>
        <DataState
          loading={board.loading}
          error={board.error}
          hasData={!!board.data}
          onRetry={board.refresh}
          empty={!!board.data && (board.data.cards ?? []).length === 0}
          emptyHint={board.data?.error ?? "本模块宇宙为空"}
        >
          {board.data && (
            <>
              <div
                data-testid="depth-body"
                data-expanded={depthExpanded ? "1" : "0"}
                className={cn("relative", depthExpanded ? "max-h-[600px] overflow-y-auto" : "max-h-[300px] overflow-hidden")}
              >
                <TradingBoard cards={board.data.cards ?? []} />
                {!depthExpanded && (
                  <>
                    {/* 渐隐 + 可点的展开提示：折叠处的硬切边看起来像"数据被截断"，
                        这里明确告诉用户"下面还有档位"，并把提示本身做成展开入口。 */}
                    <div className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-background via-background/90 to-transparent" />
                    <button
                      type="button"
                      onClick={() => setDepthExpanded(true)}
                      data-testid="depth-expand-hint"
                      className="absolute bottom-1.5 left-1/2 -translate-x-1/2 rounded-full border border-cyan-400/30 bg-background/90 px-2.5 py-0.5 text-[10px] text-cyan-300 hover:border-cyan-400/60"
                    >
                      下方档位已折叠 · 展开全部 20 档 ↓
                    </button>
                  </>
                )}
              </div>
              {depthExpanded && (
                <p className="mt-2 text-[11px] text-muted-foreground">
                  深度来自 <span className="font-mono">asterdex_depth_snapshots</span>
                  （20 档真实价量，p50 105ms）。无深度采集的币如实留白，不画假深度；
                  「队列前」= 我方挂单价前方（更优价）的累计名义额。
                </p>
              )}
            </>
          )}
        </DataState>
      </Card>

      {/* ── ⓪ 账户收益曲线（HFT 净收益，lane_ledger 事件溯源；2026-09-23 用户要求）── */}
      <EquitySeriesCard
        source="hft"
        title="账户收益曲线（HFT 净收益）"
        initialPeriod="30d"
        className="min-h-[260px]"
      />

      {/* ── ① 分币种实盘判定（现行口径）──
          [2026-09-27] 取代旧的「选币评分（点差×吞吐）」+「硬闸拒绝」两张卡：
          那两张属已停用的机械选币口径（"入选"列恒空），且硬闸与现行宇宙自相矛盾
          （ETH/ARB 既在宇宙里、又被列"点差过窄/更新过少⇒拒绝"）。
          现行选币口径 = 事件研究排名（h355b/h370）+ 分币种实盘判定去留（#2 试跑），
          本表就是判定要用的同一套数字。 */}
      <Card className="glass p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <span className="flex h-6 w-6 items-center justify-center rounded-lg border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
              <Target className="h-3.5 w-3.5" />
            </span>
            分币种实盘判定（#2 试跑口径）
          </h2>
          <span className="text-[11px] text-muted-foreground">
            {live.data
              ? `${live.data.totals.legs} 腿 / ${live.data.hours_elapsed ?? "—"}h · 净 ${live.data.totals.net_bp >= 0 ? "+" : ""}${live.data.totals.net_bp}bp · 每腿 ${live.data.totals.net_bp_per_leg ?? "—"}bp · 腿速 ${live.data.totals.legs_per_hour ?? "—"}/h`
              : "加载中…"}
          </span>
        </div>
        <DataState
          loading={live.loading}
          error={live.error}
          hasData={!!live.data}
          onRetry={live.refresh}
        >
          {live.data && (
            <>
              {/* [h850 用户"这两个有bug 没有做滚屏导致页面被拉长"] 分币种表会随币数
                 无限增长(现在 30+ 行)⇒ 加 max-h + overflow-y-auto,表体内部滚动。 */}
              <div className="max-h-[420px] overflow-x-auto overflow-y-auto">
                <table className="data-table w-full text-xs">
                  <thead className="sticky top-0 z-10 bg-background/95 backdrop-blur">
                    <tr className="text-left text-[10px] text-muted-foreground">
                      <th className="py-1 pr-2">标的</th>
                      <th className="py-1 pr-2 text-right">腿数</th>
                      <th className="py-1 pr-2 text-right">净 bp</th>
                      <th className="py-1 pr-2 text-right">bp/腿</th>
                      <th className="py-1 pr-2 text-right">腿速/h</th>
                      <th className="py-1 pr-2 text-right">净额 $</th>
                      <th className="py-1 pr-2 text-right">价格腿 $</th>
                      <th className="py-1 pr-2 text-right">价差腿 $</th>
                      <th className="py-1 pr-2 text-center">判定</th>
                    </tr>
                  </thead>
                  <tbody>
                    {live.data.rows.map((r) => (
                      <tr key={r.symbol} className="border-t border-muted/20">
                        <td className="py-1 pr-2 font-medium">{r.symbol}</td>
                        <td className="py-1 pr-2 text-right font-mono tabular-nums">{r.legs}</td>
                        <td
                          className={cn(
                            "py-1 pr-2 text-right font-mono tabular-nums",
                            r.net_bp > 0 ? "text-profit" : r.net_bp < 0 ? "text-loss" : "text-muted-foreground"
                          )}
                        >
                          {r.net_bp >= 0 ? "+" : ""}
                          {fmtNum(r.net_bp, 2)}
                        </td>
                        <td
                          className={cn(
                            "py-1 pr-2 text-right font-mono tabular-nums",
                            (r.net_bp_per_leg ?? 0) > 0
                              ? "text-profit"
                              : (r.net_bp_per_leg ?? 0) < 0
                                ? "text-loss"
                                : "text-muted-foreground"
                          )}
                        >
                          {r.net_bp_per_leg == null ? "—" : fmtNum(r.net_bp_per_leg, 4)}
                        </td>
                        <td className="py-1 pr-2 text-right font-mono tabular-nums text-muted-foreground">
                          {r.legs_per_hour == null ? "—" : fmtNum(r.legs_per_hour, 1)}
                        </td>
                        <td className="py-1 pr-2 text-right font-mono tabular-nums">
                          {fmtNum(r.net_usd, 3)}
                        </td>
                        <td className="py-1 pr-2 text-right font-mono tabular-nums text-muted-foreground">
                          {fmtNum(r.price_usd, 3)}
                        </td>
                        <td className="py-1 pr-2 text-right font-mono tabular-nums text-muted-foreground">
                          {fmtNum(r.spread_usd, 3)}
                        </td>
                        <td className="py-1 pr-2 text-center">
                          <span
                            className={cn(
                              "rounded border px-1.5 py-[1px] text-[10px]",
                              r.hint === "保留"
                                ? "border-profit/40 bg-profit/10 text-profit"
                                : r.hint === "摘除候选"
                                  ? "border-loss/40 bg-loss/10 text-loss"
                                  : "border-muted/40 bg-muted/20 text-muted-foreground"
                            )}
                          >
                            {r.hint}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="mt-2 text-[11px] text-muted-foreground">{live.data.note}</p>
            </>
          )}
        </DataState>
      </Card>

      {/* ── ③ 执行状态（挂单/持仓来自运行态；取不到如实留白） ── */}
      <Card className="glass p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <span className="flex h-6 w-6 items-center justify-center rounded-lg border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
              <Activity className="h-3.5 w-3.5" />
            </span>
            执行状态（我方挂单 / 持仓）
          </h2>
          <span className="text-[11px] text-muted-foreground">
            来自引擎运行态；未运行时显示 —
          </span>
        </div>
        <DataState loading={board.loading} error={board.error} hasData={!!board.data} onRetry={board.refresh}>
          {board.data && (
            /* 高度约束 + 表头吸顶：避免行数增长时把页面无限撑长 */
            <div className="max-h-[360px] overflow-auto rounded border border-muted/30">
              <table className="data-table w-full text-xs">
                <thead className="sticky top-0 z-10 bg-card/95 backdrop-blur">
                  <tr className="text-left text-[10px] text-muted-foreground">
                    <th className="py-1.5 pl-2 pr-2">标的</th>
                    <th className="py-1.5 pr-2 text-right">我方买</th>
                    <th className="py-1.5 pr-2 text-right">距中价</th>
                    <th className="py-1.5 pr-2 text-right">队列前</th>
                    <th className="py-1.5 pr-2 text-right">我方卖</th>
                    <th className="py-1.5 pr-2 text-right">距中价</th>
                    <th className="py-1.5 pr-2 text-right">队列前</th>
                    <th className="py-1.5 pr-2 text-right">持仓</th>
                    <th className="py-1.5 pr-2 text-right">浮盈</th>
                  </tr>
                </thead>
                <tbody>
                  {(board.data.cards ?? []).map((c) => {
                    const m = c.mine;
                    const p = c.position;
                    return (
                      <tr key={c.symbol} className="border-t border-muted/20">
                        <td className="py-1 pl-2 pr-2 font-medium">{c.symbol}</td>
                        <td className="py-1 pr-2 text-right font-mono tabular-nums text-cyan-300">
                          {m.bid == null ? "—" : fmtNum(m.bid, 6)}
                        </td>
                        <td className="py-1 pr-2 text-right font-mono tabular-nums text-muted-foreground">
                          {m.bid_width_bp == null ? "—" : `${fmtNum(m.bid_width_bp, 2)}bp`}
                        </td>
                        <td className="py-1 pr-2 text-right font-mono tabular-nums">
                          {m.bid_queue_ahead_usd == null ? "—" : `$${fmtNum(m.bid_queue_ahead_usd, 0)}`}
                        </td>
                        <td className="py-1 pr-2 text-right font-mono tabular-nums text-cyan-300">
                          {m.ask == null ? "—" : fmtNum(m.ask, 6)}
                        </td>
                        <td className="py-1 pr-2 text-right font-mono tabular-nums text-muted-foreground">
                          {m.ask_width_bp == null ? "—" : `${fmtNum(m.ask_width_bp, 2)}bp`}
                        </td>
                        <td className="py-1 pr-2 text-right font-mono tabular-nums">
                          {m.ask_queue_ahead_usd == null ? "—" : `$${fmtNum(m.ask_queue_ahead_usd, 0)}`}
                        </td>
                        <td
                          className={cn(
                            "py-1 pr-2 text-right font-mono tabular-nums",
                            p.qty > 0 ? "text-profit" : p.qty < 0 ? "text-loss" : "text-muted-foreground"
                          )}
                        >
                          {p.qty === 0 ? "0" : fmtNum(p.qty, 6)}
                        </td>
                        <td
                          className={cn(
                            "py-1 pr-2 text-right font-mono tabular-nums",
                            (p.unrealized_usd ?? 0) > 0
                              ? "text-profit"
                              : (p.unrealized_usd ?? 0) < 0
                                ? "text-loss"
                                : "text-muted-foreground"
                          )}
                        >
                          {p.unrealized_usd == null ? "—" : `$${fmtNum(p.unrealized_usd, 4)}`}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </DataState>
      </Card>
    </PageShell>
  );
}
