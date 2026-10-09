"use client";

/**
 * [F250] 中短期高频交易 · 账户与控制面板
 *
 * ## 包含什么（按用户 2026-09-20 要求）
 *   ⓪ **关键金额大号块**（账户权益 / 可用余额 / 已实现 / 合计盈亏）—— 4 位小数、text-xl 粗体
 *   ① 模拟账户明细（单腿名义、账户 ID、影子权益、账本已实现）
 *   ② 暂停 / 开启 / 停止
 *   ③ 模拟账户配置（可改金额并重置）
 *   ④ 模拟盘开关（paper / disabled）
 *   ⑤ 实盘配置（凭据就绪度 + 明确的不可用原因）
 *
 * ## 轮询节奏
 *
 * 本卡片的金额由页面用 **2s** 的 `useHftAccount` 驱动（原先 10s）。
 * 理由：浮动盈亏随行情变化，而深度梯是 2s —— 两个节奏并存会让
 * "挂单价动了、持仓浮盈没动"看起来像数据坏了。
 * 后端的"逻辑更新时刻"（`paper.updated_at`）仍按需显示，不与轮询频率混淆。
 *
 * ## 两条刻意的设计约束
 *
 * **1. 实盘开关 fail-closed。** 实测 `exchange_credentials` 长期为 0 行（后配了 1 条），
 *    而本模块的基础研究结论是**负期望**（H13：无可用信号；往返净 ≈ 点差但完成率过低）。
 *    后端对 `mode=live` 直接返回 409。前端**必须把拒绝原因显示出来**，
 *    不能只让按钮变灰——用户会以为是自己点错了。
 *
 * **2. 参数说明由后端提供。** `config.notes` 带每项中文含义，前端不硬编码标签表。
 *    套利中心的 `LaneParamEditor` 就是因为前端硬编码了 10 键的 `KEY_META`，
 *    而后端白名单有 12 键 ⇒ 两个键无标签、且「恢复默认」写入空串导致保存按钮永久禁用。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, CircleDollarSign, Power, RotateCcw, Settings2, ShieldAlert } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { fmtNum, fmtUsd } from "@/lib/format";
import { DataState } from "@/components/arbitrage";
import { hftApi } from "@/lib/hft-api";
import type { HftAccount, HftConfig } from "@/lib/hft-api";
import { toast } from "@/lib/toast";

/** [h677] 参数值渲染:对象/字典不再显示 [Object Object](per_symbol_spread_mult 事故)。 */
function fmtParamValue(v: unknown, enumLabel?: string): string {
  if (enumLabel) return enumLabel;
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return fmtNum(v, 4);
  if (typeof v === "boolean") return v ? "是" : "否";
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>);
    if (entries.length === 0) return "{}";
    return entries
      .slice(0, 6)
      .map(([k, val]) => `${k} ${typeof val === "number" ? fmtNum(val, 3) : String(val)}`)
      .join(" · ") + (entries.length > 6 ? ` …(+${entries.length - 6})` : "");
  }
  return String(v);
}

/** [h677] 关键参数(影响赚钱/风控的项):人话名 + 键。其余折叠进"全部参数"。 */
const KEY_PARAMS: Array<{ key: string; label: string }> = [
  { key: "side_mode", label: "方向模式" },
  { key: "w_base_bp", label: "基准半挂宽 (bp)" },
  { key: "spread_mult", label: "价差相对宽度倍数" },
  { key: "per_symbol_spread_mult", label: "逐币宽度倍数" },
  { key: "min_width_bp", label: "最小挂宽·加仓 (bp)" },
  { key: "min_width_reduce_bp", label: "最小挂宽·减仓 (bp)" },
  { key: "take_profit_bp", label: "止盈 (bp)" },
  { key: "stop_loss_bp", label: "止损 (bp)" },
  { key: "stop_maker_grace_sec", label: "止损先挂 maker 宽限 (s)" },
  { key: "timeout_hard_taker_sec", label: "超时强平 (s)" },
  { key: "trend_pause_bp", label: "趋势闸阈值 (bp)" },
  { key: "trend_flip_flatten_bp", label: "变盘先减仓 (bp)" },
  { key: "mp_block_bp", label: "微价闸 (bp)" },
  { key: "vpin_pause_threshold", label: "毒性否决 VPIN" },
  { key: "pullback_flow_block", label: "P1 薄流闸 (OFI)" },
  { key: "q_speed_gate", label: "Q 速控 (0影子/1减速)" },
  { key: "jump_pause_bp", label: "跳价暂停 (bp)" },
  { key: "compound_ratio", label: "腿量/权益 比例" },
];

/** [h672] 临时阻止/突发警报:事件流 + Q 停加仓 + 模拟风控 + 闸门触发率。 */
function LiveAlerts({ account }: { account: HftAccount | null | undefined }) {
  const lastSnap = useRef<{ counts: Record<string, number>; t: number } | null>(null);
  const events = account?.events ?? [];
  const skip = account?.skip_counts ?? {};
  const vf = account?.venue_filters;
  const qspeed = account?.q_speed ?? {};

  const now = Date.now();
  const prev = lastSnap.current;
  const rates: Array<{ key: string; perMin: number }> = [];
  if (prev && prev.t < now) {
    const dtMin = Math.max((now - prev.t) / 60000, 0.05);
    for (const [k, v] of Object.entries(skip)) {
      const pv = prev.counts[k] ?? 0;
      const perMin = Math.max(0, v - pv) / dtMin;
      if (perMin >= 4) rates.push({ key: k, perMin });
    }
    rates.sort((a, b) => b.perMin - a.perMin);
  }
  lastSnap.current = { counts: { ...skip }, t: now };

  const pausedCoins = Object.entries(qspeed)
    .filter(([, v]) => v.mult === 0)
    .map(([s]) => s);
  const KIND_ICON: Record<string, string> = {
    q_pause: "⛔", venue_429: "🚦", reconcile: "⚠️", heal: "💊",
    universe_swap: "🔁",
  };
  // [h682] 警报自动撤销:①q_pause 事件在对应币 Q 恢复后标"(已解除)";
  // ②已解除/超过 30 分钟的旧事件不再显示(原来环形缓冲里会一直留着)。
  const nowSec = now / 1000;
  const resolvedOf = (kind: string, msg: string): boolean => {
    if (kind === "q_pause") {
      const sym = String(msg).trim().split(/\s+/)[0] || "";
      const cur = qspeed[sym];
      return !!cur && cur.mult > 0;
    }
    return false;
  };
  const visibleEvents = events
    .map((e) => ({ ...e, resolved: resolvedOf(e.kind, e.msg) }))
    .filter((e) => !e.resolved && nowSec - e.ts < 1800)
    .slice(-4)
    .reverse();
  // [h686] 停加仓是**状态**不是警报 ⇒ 从警报区移除(挪到"加仓速度"状态行);
  // 警报区只留真·突发事件(429/对账/自愈/宇宙替换)。
  const hasAlert =
    visibleEvents.length > 0 || (vf?.rate_429 ?? 0) > 0 ||
    (vf?.filter_skips ?? 0) > 0 || rates.length > 0;
  if (!hasAlert) return null;

  return (
    <div className="space-y-1 rounded border border-warning/25 bg-warning/5 px-2 py-1.5">
      <div className="flex items-center gap-1 text-[10px] font-semibold text-warning">
        <AlertTriangle className="h-3 w-3" /> 实时警报(临时阻止/突发)
      </div>
      {visibleEvents.map((e) => (
        <div key={e.ts} className="flex items-baseline gap-1 text-[10px] text-warning/90">
          <span className="font-mono text-muted-foreground/70">
            {new Date(e.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false })}</span>
          <span>{KIND_ICON[e.kind] ?? "·"}</span>
          <span className="truncate">{e.msg}</span>
          {e.n && e.n > 1 ? <span className="text-muted-foreground/70">×{e.n}</span> : null}
        </div>
      ))}
      {/* [h686] 加仓速度状态(含试探复入倒计时):这是**状态**不是警报,
          所以不做红色告警样式;BNB 这类被 Q 停的币在这里能看到还要多久复入。 */}
      {Object.keys(qspeed).length > 0 && (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-muted-foreground">
          <span>加仓速度:</span>
          {Object.entries(qspeed).map(([sym, v]) => {
            const label =
              v.mult === 0 ? "停" : v.mult >= 1 ? "满速" : "半速";
            const tone =
              v.mult === 0 ? "text-loss" : v.mult >= 1 ? "text-profit" : "text-warning";
            const retry =
              v.mult === 0 && v.retry_in_min != null
                ? `(Q${fmtNum(v.q, 2)} · ${v.retry_in_min <= 0 ? "即将试探复入" : `${fmtNum(v.retry_in_min, 0)} 分钟后复入`})`
                : `(Q${fmtNum(v.q, 2)})`;
            return (
              <span key={sym} className="tabular-nums">
                <span className="text-foreground/80">{sym}</span>{" "}
                <span className={tone}>{label}</span>
                <span className="text-muted-foreground/70">{retry}</span>
              </span>
            );
          })}
        </div>
      )}
      {((vf?.rate_429 ?? 0) > 0 || (vf?.filter_skips ?? 0) > 0) && (
        <div className="text-[10px] text-warning/90">
          模拟风控: 429×{vf?.rate_429 ?? 0} · 过滤器拒单×{vf?.filter_skips ?? 0}
        </div>
      )}
      {rates.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
          <span>高频拦截:</span>
          {rates.slice(0, 4).map((r) => (
            <span key={r.key} className="rounded bg-muted/30 px-1 tabular-nums">
              {r.key} {Math.round(r.perMin)}/min
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
import { confirmDialog } from "@/lib/confirm";

type Status = "active" | "paused" | "stopped";
type Mode = "paper" | "live" | "disabled";

const STATUS_LABEL: Record<Status, string> = {
  active: "开启中",
  paused: "已暂停",
  stopped: "已停止",
};
const STATUS_TONE: Record<Status, string> = {
  active: "border-profit/30 bg-profit/10 text-profit",
  paused: "border-warning/30 bg-warning/10 text-warning",
  stopped: "border-muted/40 bg-muted/20 text-muted-foreground",
};
const MODE_LABEL: Record<Mode, string> = {
  paper: "模拟盘",
  live: "实盘",
  disabled: "已停用",
};

function Row({ k, v, tone, big }: { k: string; v: string; tone?: string; big?: boolean }) {
  // [h678f] 标签与数值**相邻**(固定标签列宽),不再 justify-between 拉开——
  // 之前 680px 宽的行里中间空出 ~400px 空白(用户:"这个中间空着是干嘛的")。
  return (
    <div className="flex items-baseline gap-2 text-[11px]">
      <span className="w-[128px] flex-shrink-0 text-muted-foreground">{k}</span>
      <span className={cn("font-mono tabular-nums", big ? "text-base font-bold" : "", tone)}>{v}</span>
    </div>
  );
}

/**
 * 大号数字块。
 *
 * 用户 2026-09-20：「持仓数据的区域，数字太小了，也不明显」。
 * 原先账户里的盈亏是 11px 小字，与一堆参数混排 ⇒ 一眼看不到。
 * 这里把 4 个最关键的金额单独提成大号块（text-xl 粗体 + 正负配色），
 * 与持仓面板的汇总块保持同一视觉规格。
 */
function BigStat({
  label, value, tone, sub,
}: { label: string; value: string; tone?: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-muted/40 bg-muted/10 px-3 py-2">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className={cn("font-mono text-xl font-bold tabular-nums", tone)}>{value}</div>
      {sub && <div className="text-[10px] text-muted-foreground">{sub}</div>}
    </div>
  );
}

/** 盈亏配色：正=profit，负=loss，0/未知=默认 */
const pnlTone = (v: number | null | undefined) =>
  v == null ? undefined : v > 0 ? "text-profit" : v < 0 ? "text-loss" : undefined;

export function HftControlPanel({
  account,
  config,
  onChanged,
  disabled,
}: {
  account: HftAccount | null;
  config: HftConfig | null;
  /** 任何写操作成功后调用（触发 account/config 重拉） */
  onChanged: () => void;
  disabled?: boolean;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [balance, setBalance] = useState<string>("300");
  const [liveError, setLiveError] = useState<string | null>(null);

  /**
   * [F299 2026-09-21] 用户是否**亲手改过**重置金额输入框。
   *
   * ## 病根（用户实测反馈："重置没办法改金额，默认金额变成变量跟随账户余额在变，并且无法修改"）
   *
   * 原实现：
   * ```tsx
   * useEffect(() => {
   *   const eq = account?.paper?.total_equity ?? account?.paper?.shadow_equity;
   *   if (eq != null && !busy) setBalance(String(eq));      // ← 回填「当前权益」
   * }, [account?.paper?.total_equity, account?.paper?.shadow_equity, busy]);  // ← 每次都跑
   * ```
   * 两个错叠在一起：
   *   ① **依赖数组里放了会持续变化的值** ⇒ 账户数据每次轮询刷新（几秒一次）都重跑
   *      ⇒ 用户刚输入的数字**立刻被覆盖**，表现为"无法修改"；
   *   ② **回填的是「当前权益」而不是「默认起始金额」** ⇒ 重置对话框变成
   *      「重置为 $186.3671」，看起来像"默认金额变成了跟随余额的变量"。
   * 原注释写着"仅在用户尚未编辑时同步一次" —— **那句话根本没被实现**。
   *
   * ## 修法
   *   · 用 ref 记住"用户已编辑" ⇒ 一旦编辑过，后续任何刷新都**不再回填**
   *   · 只在**首次**拿到账户数据时回填一次，且回填 `default_balance`（默认起始额，
   *     通常 300），**不回填当前权益** —— 重置是"回到某个起始金额"，
   *     拿当前权益当默认值在语义上就是错的（重置成当前值 = 什么都没重置）
   */
  const balanceEditedRef = useRef(false);
  const balanceFilledRef = useRef(false);
  useEffect(() => {
    if (balanceEditedRef.current || balanceFilledRef.current) return;
    const def = account?.paper?.default_balance;
    if (def != null) {
      setBalance(String(def));
      balanceFilledRef.current = true;
    }
  }, [account?.paper?.default_balance]);

  const directionBoxRef = useRef<HTMLDivElement>(null);
  const directionLog = account?.direction_log ?? [];
  // [h626 确定性版] 方向判定状态来自心跳（API 透传），前端不再硬编码"已停"。
  const dirState = account?.direction_state;
  const dirStopped = dirState ? dirState.stopped : false;
  useEffect(() => {
    const el = directionBoxRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [directionLog.length, directionLog[directionLog.length - 1]?.ts]);

  /** 用户输入：标记为已编辑，此后不再被自动回填覆盖。 */
  const onBalanceChange = useCallback((v: string) => {
    balanceEditedRef.current = true;
    setBalance(v);
  }, []);

  /** 「默认 300」按钮 = 显式回到默认值，同样视为用户意图，不回填。 */
  const onBalanceDefault = useCallback(() => {
    balanceEditedRef.current = true;
    setBalance("300");
  }, []);

  const run = useCallback(
    async (key: string, fn: () => Promise<unknown>, okMsg: string) => {
      setBusy(key);
      setLiveError(null);
      try {
        await fn();
        toast.success(okMsg);
        onChanged();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        // 切实盘被 409 拒绝时要**显性展示原因**（fail-closed 的可观测性）
        if (key === "mode-live") setLiveError(msg);
        toast.error(`操作失败：${msg}`);
      } finally {
        setBusy(null);
      }
    },
    [onChanged]
  );

  const status = (account?.status ?? null) as Status | null;
  const mode = (account?.mode ?? null) as Mode | null;
  const paper = account?.paper;
  const live = account?.live;
  const realized = paper?.realized_usd ?? null;
  const unrealized = paper?.unrealized_usd ?? null;
  const totalPnl = paper?.total_pnl_usd ?? null;
  const openCount = paper?.open_positions?.length ?? 0;

  /**
   * 金额显示位数。
   *
   * ⚠️ 这里**不能统一用 2 位小数**：单腿名义只有 $30，一笔 1.5bp 的往返仅赚
   * $0.0045 ⇒ 2 位小数永远显示 $0.00（用户实测反馈"账户数据没有任何变化"）。
   * 现值 $300 量级、盈亏 $0.01 量级 ⇒ 需要 4 位。账户权益本身仍用 2 位。
   */
  const pnlFmt = (v: number | null) =>
    v == null ? "—" : `${v >= 0 ? "+" : "−"}$${Math.abs(v).toFixed(4)}`;

  const onSetStatus = (s: Status) =>
    run(`status-${s}`, () => hftApi.setStatus(s), `已${STATUS_LABEL[s] === "开启中" ? "开启" : STATUS_LABEL[s]}`);

  const onSetMode = async (m: Mode) => {
    if (m === "live") {
      // 二次确认：实盘是不可逆的资金风险动作
      const ok = await confirmDialog({
        title: "切换到实盘？",
        description: "实盘会使用真实资金。后端目前会硬闸拒绝（未证明正期望）。",
        confirmText: "确认切换",
        tone: "danger",
      });
      if (!ok) return;
    }
    void run(`mode-${m}`, () => hftApi.setMode(m), `已切换到${MODE_LABEL[m]}`);
  };

  const onReset = async () => {
    const v = Number(balance);
    if (!Number.isFinite(v) || v <= 0) {
      toast.error("金额必须是正数");
      return;
    }
    const ok = await confirmDialog({
      title: `重置模拟账户为 $${v}？`,
      description: "会清零当前持仓/挂单并重置统计时代（账本 lane_ledger 不动，不可篡改）。",
      confirmText: "确认重置",
      tone: "warning",
    });
    if (!ok) return;
    void run("reset", () => hftApi.resetAccount(v), `模拟账户已重置为 $${v}`);
  };

  return (
    <Card className="glass p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <span className="flex h-6 w-6 items-center justify-center rounded-lg border border-cyan-400/20 bg-cyan-400/10 text-cyan-300">
            <CircleDollarSign className="h-3.5 w-3.5" />
          </span>
          账户与控制
        </h2>
        <div className="flex items-center gap-2">
          {mode && (
            <span className={cn("rounded border px-1.5 py-[1px] text-[10px] font-medium",
              mode === "live" ? "border-loss/30 bg-loss/10 text-loss"
                : mode === "paper" ? "border-cyan-400/30 bg-cyan-400/10 text-cyan-300"
                  : "border-muted/40 bg-muted/20 text-muted-foreground")}>
              {MODE_LABEL[mode]}
            </span>
          )}
          {status && (
            <span className={cn("rounded border px-1.5 py-[1px] text-[10px] font-medium", STATUS_TONE[status])}>
              {STATUS_LABEL[status]}
            </span>
          )}
        </div>
      </div>

      <DataState loading={!account} error={null} hasData={!!account}>
        {/* ── 0. 关键金额（大号 + 4 位小数）──
            放在最上排：用户最先要看的是"钱动了没有"，不是参数。

            [F297 2026-09-21] **权益优先取 `total_equity`（活值），而不是 `shadow_equity`**。
            ⚠️ 这条把 F289b 的判断**改回来了** —— F289b 当时以为 `shadow_equity` 是
            "引擎每 tick 现算的活值"，实测**不是**：它就是 `lane_registry.meta.shadow_equity`
            这个**陈旧快照**（只在 F290 同步脚本跑时更新）。
            现场（2026-09-21 10:50）：前端显示「账户权益 $270.69」，而
              · 后端 `total_equity` = 可用 185.94 + 冻结 0 + 浮盈 −0.32 = **$185.63** ✓
              · 可用余额 $185.94、账本已实现 −$90.93 —— 与 185.63 自洽 ✓
              · `shadow_equity` = 270.69 ⇒ 与三者都矛盾 ✗
            ⇒ 用户报的「账户权益和余额对不上」就是这个：**270.69 是陈旧快照**。
            后端 `hft_routes.py` 已给出 `total_equity_source`（`available+frozen+upnl`）
            作为口径自证，本卡片把它显示出来，让"这个数字怎么来的"可见。

            取值顺序：`total_equity`（活） → `shadow_equity`（快照，兜底） → 「—」。
            **不要 fallback 成 0**，那会误导成"爆仓了"。 */}
        {/* [h678e] **顶部控制条**:运行控制/模式切换/金额重置是"一行小控件",
            原来各占主卡一整块(用户:"很小的一个功能,非要占着主模块卡")。
            现在压成一条,主卡只留数据。 */}
        <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-muted/30 bg-muted/5 px-3 py-2">
          <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <Power className="h-3 w-3" /> 运行
          </span>
          <span className="flex items-center gap-1.5">
            <button
              type="button" disabled={disabled || busy !== null || status === "active"}
              onClick={() => onSetStatus("active")}
              className="rounded-md border border-profit/30 bg-profit/10 px-2 py-0.5 text-[11px] text-profit hover:bg-profit/20 disabled:opacity-40"
            >
              {busy === "status-active" ? "…" : "开启"}
            </button>
            <button
              type="button" disabled={disabled || busy !== null || status === "paused"}
              onClick={() => onSetStatus("paused")}
              className="rounded-md border border-warning/30 bg-warning/10 px-2 py-0.5 text-[11px] text-warning hover:bg-warning/20 disabled:opacity-40"
            >
              {busy === "status-paused" ? "…" : "暂停"}
            </button>
            <button
              type="button" disabled={disabled || busy !== null || status === "stopped"}
              onClick={() => onSetStatus("stopped")}
              className="rounded-md border border-muted/40 bg-muted/20 px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-muted/30 disabled:opacity-40"
            >
              {busy === "status-stopped" ? "…" : "停止"}
            </button>
          </span>
          <span className="h-4 w-px bg-muted/40" />
          <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <Settings2 className="h-3 w-3" /> 模式
          </span>
          <span className="flex items-center gap-1.5">
            <button
              type="button" disabled={disabled || busy !== null || mode === "paper"}
              onClick={() => onSetMode("paper")}
              className="rounded-md border border-cyan-400/30 bg-cyan-400/10 px-2 py-0.5 text-[11px] text-cyan-300 hover:bg-cyan-400/20 disabled:opacity-40"
            >
              模拟盘
            </button>
            <button
              type="button" disabled={disabled || busy !== null}
              onClick={() => onSetMode("live")}
              title={live?.reason ?? ""}
              className={cn(
                "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] disabled:opacity-40",
                live?.ready
                  ? "border-loss/30 bg-loss/10 text-loss hover:bg-loss/20"
                  : "border-muted/40 bg-muted/10 text-muted-foreground cursor-not-allowed"
              )}
            >
              <ShieldAlert className="h-3 w-3" /> 实盘
            </button>
            <button
              type="button" disabled={disabled || busy !== null || mode === "disabled"}
              onClick={() => onSetMode("disabled")}
              className="rounded-md border border-muted/40 bg-muted/20 px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-muted/30 disabled:opacity-40"
            >
              停用
            </button>
          </span>
          <span className="h-4 w-px bg-muted/40" />
          <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">金额 $</span>
          <input
            value={balance}
            onChange={(e) => onBalanceChange(e.target.value)}
            inputMode="decimal"
            placeholder={String(paper?.default_balance ?? 300)}
            className="w-20 rounded border border-muted/40 bg-background/60 px-2 py-0.5 font-mono text-[11px] tabular-nums outline-none focus:border-cyan-400/50"
          />
          <button
            type="button" disabled={disabled || busy !== null}
            onClick={onReset}
            className="inline-flex items-center gap-1 rounded-md border border-cyan-400/30 bg-cyan-400/10 px-2 py-0.5 text-[11px] text-cyan-300 hover:bg-cyan-400/20 disabled:opacity-40"
          >
            <RotateCcw className="h-3 w-3" />
            {busy === "reset" ? "重置中…" : "重置"}
          </button>
          <button
            type="button" disabled={disabled || busy !== null}
            onClick={onBalanceDefault}
            className="rounded-md border border-muted/40 px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-muted/20 disabled:opacity-40"
          >
            默认 {fmtUsd(paper?.default_balance ?? 300)}
          </button>
          <span className="ml-auto flex items-center gap-1 text-[10px] text-warning" title={live?.reason ?? ""}>
            <AlertTriangle className="h-3 w-3" />
            实盘不可用 · 凭据 {live?.enabled_credentials ?? 0}/{live?.credentials ?? 0}
          </span>
        </div>

        <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-5">
          <BigStat
            label="账户权益"
            value={
              paper?.total_equity != null ? fmtUsd(paper.total_equity)
                : paper?.shadow_equity != null ? fmtUsd(paper.shadow_equity)
                  : "—"
            }
            sub={
              paper?.total_equity != null
                ? `起始 ${fmtUsd(paper.default_balance)}${
                    paper.total_equity_source ? ` · ${paper.total_equity_source}` : ""}`
                : undefined
            }
          />
          <BigStat
            label="可用余额"
            value={paper?.available_balance == null ? "—" : fmtUsd(paper.available_balance)}
          />
          <BigStat
            label="已实现盈亏（自上次重置）"
            value={pnlFmt(realized)}
            tone={pnlTone(realized)}
            sub="只有「重置账户」会清零（部署/试跑不清）"
          />
          {/* [F297] **累计手续费独立成一格**。
              依据（本轮实测）：入场腿（maker）12,269 笔平均 fee = **0.0000bp**（免费），
              强平腿（taker）917 笔平均 −3.9956bp，强平腿累计 taker 费 −$50.87，
              而整夜亏损 −$52.20 ⇒ **97% 的亏损就是 taker 费**。
              ⇒ 手续费不是"明细项"，它是这条车道的第一成本项，必须显眼。

              [F300] ⚠️ 口径是**当前时代**（按 `stats_since` 裁剪，与「已实现盈亏」同源）。
              用户重置账户后若这一格仍显示全历史的 −$51.39，会与"刚重置到 $300"直接矛盾
              （实测反馈"哪里好像还是不对"）。**同一张卡片的数字必须同一个时代。** */}
          <BigStat
            label="手续费（自上次重置）"
            value={paper?.fee_usd == null ? "—" : pnlFmt(paper.fee_usd)}
            tone={paper?.fee_usd == null ? undefined : pnlTone(paper.fee_usd)}
            sub={
              paper?.fee_fills == null
                ? "maker 0bp（免费）/ taker −4bp"
                : `taker ${paper.fee_fills} 笔 · 重置账户才清零`
            }
          />
          {/* 合计盈亏 = 已实现 + 未实现。持仓浮盈的逐币明细在下方「持仓（实时）」块 */}
          <BigStat
            label={`合计盈亏（持仓 ${openCount}）`}
            value={pnlFmt(totalPnl)}
            tone={pnlTone(totalPnl)}
            sub={unrealized == null ? undefined : `其中浮盈 ${pnlFmt(unrealized)}`}
          />
        </div>

        {/* [h678d 排版四修] 两列:「左=账户明细(长列表,限宽 680) / 右=控制+金额+模式+
            警报+方向判定(整列堆满)」。上一版把警报/方向判定放在左下 ⇒ 右侧空半屏。 */}
        <div className="grid grid-cols-1 items-start gap-5
                        lg:grid-cols-[minmax(0,1fr)_minmax(0,430px)]">
          {/* ① 账户明细:[h678f] 改为**两列紧凑排**——键值相邻,不再限宽留白 */}
          <div className="space-y-3">
            <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold text-muted-foreground">
              <CircleDollarSign className="h-3 w-3" /> 账户明细
              <span className="font-normal">（{paper?.name ?? "—"}）</span>
            </div>
            {/* [h678f] 行放进 2 列网格:宽度用起来,行内不再出现大片空白 */}
            <div className="grid grid-cols-1 gap-x-8 gap-y-1 sm:grid-cols-2">
              <Row
              k="单腿名义"
              v={
                account?.sizing?.compound_ratio
                  ? `${fmtUsd((paper?.total_equity ?? 0) * account.sizing.compound_ratio)}（${fmtNum(
                      account.sizing.compound_ratio * 100, 1)}%）`
                  : fmtUsd(account?.sizing?.fill_notional ?? 0)
              }
            />
            <Row k="账户 ID" v={paper?.account_id == null ? "—" : String(paper.account_id)} />
            {/* [F297] 「影子权益」保留但**明确标注是快照** —— 它不再参与「账户权益」的
                取值（见上方 F297 注释），留在这里只作口径对照，避免"两个权益数字
                互相矛盾却都不说来源"。 */}
            <Row
              k="影子权益（注册表快照）"
              v={paper?.shadow_equity == null ? "—" : fmtUsd(paper.shadow_equity)}
            />
            <Row
              k="账本已实现"
              v={paper?.realized_usd == null ? "—" : pnlFmt(paper.realized_usd)}
              tone={pnlTone(paper?.realized_usd)}
            />
            {/* [F297] 手续费明细：与上方大号格同源（`/api/hft/account`）。
                `fee_gross_usd` = 若这些成交**全按 taker** 计会是多少，
                用来直观说明 maker 免费省掉了多少。
                [F300] 加一行「历史累计」并把两者都标清口径 —— 重置只清账户不清历史，
                taker 曾吃掉 $51 这条教训要留着，但不能与当前时代混起来。 */}
            <Row
              k="累计手续费（本时代）"
              v={paper?.fee_usd == null ? "—" : pnlFmt(paper.fee_usd)}
              tone={pnlTone(paper?.fee_usd)}
            />
            <Row
              k="付费成交（taker）"
              v={paper?.fee_fills == null ? "—" : `${paper.fee_fills} 笔`}
            />
            {paper?.fee_gross_usd != null && (
              <Row
                k="对照：全额 taker 计"
                v={pnlFmt(paper.fee_gross_usd)}
                tone={pnlTone(paper.fee_gross_usd)}
              />
            )}
            {paper?.fee_lifetime_usd != null && (
              <Row
                k="历史累计（重置不清）"
                v={`${pnlFmt(paper.fee_lifetime_usd)}${
                  paper.fee_lifetime_fills == null ? "" : ` · ${paper.fee_lifetime_fills} 笔`}`}
                tone={pnlTone(paper?.fee_lifetime_usd)}
              />
            )}
            </div>
            {/* [h433 2026-09-28] **账户总账对账行**（用户实测反馈"每次整治后手续费清零，
                账对不上"）。数据源 = `arbitrage_paper_ledgers`（append-only：
                任何部署/重置/时代切换都不清零），恒等式：
                  起始 + 外部划拨 + 累计已实现 − 累计手续费 = 当前权益
                残差恒为 0；非 0 说明流水与余额脱钩（会显式标红）。 */}
            {paper?.book && (
              <div className="mt-1 space-y-1 rounded border border-dashed p-2">
                <div className="text-[10px] font-semibold text-muted-foreground">
                  账户总账（自上次重置起 · 部署/试跑不清零）
                </div>
                {/* [h476 2026-09-29] **把起点显式打出来**：用户两次投诉"手续费又被重置"，
                    根因是 legacy 判定脚本改写 `stats_since` 而面板正好显示的是它。
                    现在显示的是账户口径（account_reset_at），并且只有"重置账户"才会变
                    —— 打出来才能让用户一眼验证"这次真的没动"。 */}
                <Row
                  k="起点（仅重置账户时变）"
                  v={
                    paper.book.since
                      ? new Date(paper.book.since).toLocaleString("zh-CN", {
                          hour12: false,
                          timeZone: "Asia/Shanghai",
                        })
                      : "—"
                  }
                />
                {paper.book.since_basis && paper.book.since_basis !== "account_reset_at" && (
                  <Row
                    k="⚠ 起点口径回退"
                    v={`${paper.book.since_basis}（account_reset_at 缺失）`}
                    tone="text-loss"
                  />
                )}
                <Row k="起始（重置额）" v={fmtUsd(paper.book.initial_usd)} />
                <Row
                  k="外部划拨（正常≈0）"
                  v={pnlFmt(paper.book.capital_adj_usd)}
                  tone={pnlTone(paper.book.capital_adj_usd)}
                />
                <Row
                  k="已实现"
                  v={pnlFmt(paper.book.realized_all_usd)}
                  tone={pnlTone(paper.book.realized_all_usd)}
                />
                <Row
                  k="手续费"
                  v={pnlFmt(paper.book.fee_all_usd)}
                  tone={pnlTone(paper.book.fee_all_usd)}
                />
                <Row k="= 当前权益" v={fmtUsd(paper.book.equity_usd)} />
                <Row
                  k="对账残差"
                  v={
                    paper.book.recon_residual_usd == null
                      ? "—"
                      : `${paper.book.recon_residual_usd >= 0 ? "+" : ""}${paper.book.recon_residual_usd.toFixed(4)}`
                  }
                  tone={
                    Math.abs(paper.book.recon_residual_usd ?? 0) < 0.01 ? undefined : "text-loss"
                  }
                />
              </div>
            )}
            <Row k="账户状态" v={paper?.account_status ?? "—"} />
            <p className="pt-1 text-[10px] text-muted-foreground">
              「已实现/合计盈亏」由运行态持仓与成交现算，随行情实时变化；
              逐币持仓明细见下方「持仓（实时）」块。金额一律 <span className="font-mono">4 位小数</span>
              （单腿仅 {fmtUsd(account?.sizing?.fill_notional ?? 0)}，2 位会把盈亏显示成 $0.00）。
            </p>
            {paper?.updated_at && (
              <div className="text-[10px] text-muted-foreground">账户更新于 {paper.updated_at}</div>
            )}
          </div>

          {/* [h678e] 原"运行控制 + 模拟盘金额"独立块已删除 —— 已压入顶部控制条
              (用户:小功能不该占主卡一整块)。重置语义提示也随之上移。 */}
          {/* [h678e] 右列 = 警报 + 方向判定(模式切换/金额已移到顶部控制条) */}
          <div className="space-y-2">
            {liveError && (
              <div className="rounded border border-loss/30 bg-loss/10 px-2 py-1 text-[10px] text-loss">
                切换被拒绝：{liveError}
              </div>
            )}

            {/* [h678d] 警报 + 方向判定并入**右列** ⇒ 右列自上而下填满,
                不再出现"右侧空半屏、警报挤在左下角" */}
            <LiveAlerts account={account} />

            <div className="flex min-h-[9rem] flex-1 flex-col pt-1">
              <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold text-muted-foreground">
                <span
                  className={cn(
                    "h-1.5 w-1.5 flex-shrink-0 rounded-full",
                    dirStopped ? "bg-muted-foreground/60" : "animate-pulse bg-emerald-400",
                  )}
                />
                <span>
                  方向判定
                  {dirStopped
                    ? "（已停）"
                    : `（${dirState?.source === "deterministic" ? "确定性" : "运行中"}·每${(dirState?.every_sec ?? 300) / 60}分钟）`}
                </span>
                {!dirStopped && dirState?.as_of ? (
                  <span className="ml-auto font-mono text-[9px] text-muted-foreground/70">
                    {new Date(dirState.as_of * 1000).toLocaleTimeString("zh-CN", {
                      hour12: false,
                      timeZone: "Asia/Shanghai",
                    })}
                  </span>
                ) : null}
              </div>
              {/* [h665f] 实时分析预测(置顶):每币融合分数 D + 方向 + 分量 + 当前动作 */}
              {/* [h850 用户"这两个有bug 没有做滚屏导致页面被拉长"] 币数会随宇宙增长
                  (现在 30+),此前没有高度上限 ⇒ 把左列整列撑长、页面被拉长。
                  加 max-h + overflow-y-auto:面板内部滚动,页面高度稳定。 */}
              <div className="mb-1 max-h-64 overflow-y-auto rounded border border-cyan-400/20 bg-cyan-400/5 px-2 py-1">
                <div className="mb-0.5 flex items-center gap-1 text-[9px] font-semibold text-cyan-300/80">
                  <span>实时分析预测(微价·流·趋势融合)</span>
                  <span className="text-muted-foreground/60">每 tick 更新</span>
                </div>
                {Object.keys(account?.direction_score ?? {}).length === 0 ? (
                  <div className="text-[10px] text-muted-foreground">暂无信号读数…</div>
                ) : (
                  Object.entries(account?.direction_score ?? {}).map(([sym, v]) => {
                    const d = v.d;
                    const dirText =
                      d == null ? "—" : d > 0.15 ? "看多" : d < -0.15 ? "看空" : "中性";
                    const tone =
                      d == null
                        ? "text-muted-foreground"
                        : d > 0.15
                          ? "text-profit"
                          : d < -0.15
                            ? "text-loss"
                            : "text-foreground/80";
                    const block = account?.direction_state?.block_add?.[sym];
                    return (
                      <div key={sym} className="flex items-center gap-2 border-b border-cyan-400/10 py-0.5 font-mono text-[10px] last:border-0">
                        <span className="w-10 font-semibold text-foreground/85">{sym}</span>
                        <span className={cn("text-[13px] font-bold", tone)}>
                          {d == null ? "—" : `${d >= 0 ? "+" : ""}${d.toFixed(2)}`}
                        </span>
                        <span className={cn("rounded px-1 text-[9px]",
                          dirText === "看多" && "bg-profit/10 text-profit",
                          dirText === "看空" && "bg-loss/10 text-loss",
                          dirText === "中性" && "bg-muted/20 text-muted-foreground")}>
                          {dirText}
                        </span>
                        <span className="text-[9px] text-muted-foreground/70">
                          微价 {v.mp != null ? `${v.mp >= 0 ? "+" : ""}${v.mp.toFixed(2)}` : "—"} ·
                          流 {v.ofi != null ? v.ofi.toFixed(2) : "—"}
                        </span>
                        <span className="ml-auto text-[9px]">
                          {block ? (
                            <span className="text-warning">
                              {block === "buy" ? "停买加仓" : "停卖加仓"}
                            </span>
                          ) : (
                            <span className="text-muted-foreground/70">两边可挂</span>
                          )}
                        </span>
                      </div>
                    );
                  })
                )}
              </div>
              {/* 历史判定滚屏(过去样本口径,供核对) */}
              <div className="mb-0.5 text-[9px] font-semibold text-muted-foreground/70">
                历史判定(每 5 分钟 · 过去样本)
              </div>
              <div
                ref={directionBoxRef}
                className="h-32 space-y-2 overflow-y-auto rounded border border-muted/40 bg-muted/10 px-2 py-1.5"
              >
                {directionLog.length === 0 ? (
                  <div className="text-[10px] text-muted-foreground">等待第一次判定…</div>
                ) : (
                  directionLog.map((line) => {
                    const parts = String(line.text ?? "")
                      .split("；")
                      .map((p) => p.trim())
                      .filter(Boolean);
                    return (
                      <div key={line.ts} className="text-[10px] leading-snug">
                        <div className="flex items-center gap-1">
                          <span className="font-mono text-cyan-300/80">
                            {new Date(line.ts * 1000).toLocaleTimeString("zh-CN", {
                              hour12: false,
                              timeZone: "Asia/Shanghai",
                            })}
                          </span>
                          {parts.length > 1 ? (
                            <span className="text-[9px] text-muted-foreground/60">
                              {parts.length} 币
                            </span>
                          ) : null}
                        </div>
                        {parts.map((p, i) => (
                          <div key={i} className="break-words pl-12 text-foreground/90">
                            {p}
                          </div>
                        ))}
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          </div>
        </div>

        {/* 报价参数(h672→h677 重构:人话分组 + 关键项优先 + 其余折叠) */}
        {config && (
          <div className="mt-4 border-t border-muted/30 pt-3">
            <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold text-muted-foreground">
              <Settings2 className="h-3 w-3" /> 当前生效参数
              <span className="text-muted-foreground/60">(只读,说明来自后端)</span>
            </div>
            {/* 关键项:影响赚钱/风控的 18 项,人话名 + 值 + 悬浮含义 */}
            <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3 lg:grid-cols-6">
              {KEY_PARAMS.map(({ key, label }) => {
                const params = config.params ?? {};
                const notes = config.notes ?? {};
                const v = params[key];
                if (v === undefined || v === null || v === "") return null;
                const enumLabel = config.enum_labels?.[key]?.[String(v)];
                const shown = fmtParamValue(v, enumLabel);
                return (
                  <div key={key} title={`${key}\n${notes[key] ?? ""}`} className="min-w-0">
                    <div className="truncate text-[10px] text-muted-foreground">{label}</div>
                    <div className="truncate font-mono text-[11px] tabular-nums text-foreground/90"
                      title={shown}>
                      {shown}
                    </div>
                  </div>
                );
              })}
            </div>
            {/* 其余参数折叠 */}
            <details className="mt-3">
              <summary className="cursor-pointer select-none text-[10px] text-muted-foreground hover:text-foreground">
                显示全部参数({Object.keys(config.params ?? {}).length} 项,技术口径)
              </summary>
              <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3 lg:grid-cols-4">
                {Object.entries(config.params ?? {})
                  // ⚠️ 不要按"值必须是数字"过滤:side_mode 是字符串枚举。
                  .filter(([k, v]) => v !== null && v !== undefined && v !== ""
                    && !KEY_PARAMS.some((kp) => kp.key === k))
                  .map(([k, v]) => {
                    const label = config.enum_labels?.[k]?.[String(v)];
                    const shown = fmtParamValue(v, label);
                    return (
                      <div key={k} title={(config.notes ?? {})[k] ?? ""} className="min-w-0">
                        <div className="truncate font-mono text-[10px] text-muted-foreground" title={k}>
                          {k}
                        </div>
                        <div className={cn("truncate font-mono text-[11px] tabular-nums",
                          label && "text-cyan-300")} title={shown}>
                          {shown}
                        </div>
                      </div>
                    );
                  })}
              </div>
            </details>
          </div>
        )}
      </DataState>
    </Card>
  );
}
