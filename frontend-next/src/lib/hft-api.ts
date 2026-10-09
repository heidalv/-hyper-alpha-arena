/**
 * [F250] 中短期高频交易模块 · API 客户端
 *
 * ## 为什么独立于 `trading-api.ts`
 *
 * 用户要求：**新模块，以 L1 赛道为借鉴，不用套利中心**。
 * `trading-api.ts` 是套利中心的契约（`/api/trading/*`：车道注册表/账本/晋升/风控），
 * 本模块是另一个业务对象（`/api/hft/*`：宇宙选币 / 20 档深度 / 队列前方量）。
 *
 * 合并会带来已实际发生的问题：套利中心入口被 `hidden: true` 时，
 * 本模块的页面**连入口都看不见**（东西做完了用户找不到）。
 *
 * ## 符号命名（本仓库有三套并存，本文件只接受**裸标的**）
 *
 *     `asterdex_book_ticker.symbol`     = `BTCUSDT`
 *     `asterdex_depth_snapshots.symbol` = `ASTERUSDT`
 *     本模块对外                        = `ASTER`
 */
import { apiRequest } from "./api";

// ── 类型（与 backend/api/hft_routes.py 的返回契约一一对应） ──

/** `[价, 量, 累计量]`。档位按「价格由优到劣」：`asks` 升序（首个最优），
 *  `bids` 升序（**末位**最优）。 */
export type HftLadderLevel = [number, number, number];

export interface HftBoardCard {
  symbol: string;
  /** 是否有 20 档深度。false 时**绝不画假深度**。 */
  has_depth: boolean;
  /** 该币是否在深度采集名单内（区分「本就无采集」与「采集停了」） */
  depth_expected: boolean;
  depth_age_ms: number | null;
  /**
   * **当前盘口中间价**（来自 20 档深度，与 `ladder` 同源同节奏，2s 刷新）。
   *
   * ⚠️ 历史坑：这里曾经是引擎的 `quote_mid`（上一次报价时刻记的中价）⇒
   * 页面上的 mid 可以几十分钟不动，而同一张卡片的深度梯在刷 ⇒
   * 用户反馈「持仓（实时）数据没有任何变化」。**mid 必须是行情价。**
   */
  mid: number | null;
  /** mid 是否来自实时盘口。false ⇒ 无盘口数据、退回 `ref_mid`，前端应显著标注。 */
  mid_is_live?: boolean;
  /**
   * 引擎的**报价基准中价**（上一次报价时刻的价）。与 `mid` 是两个量：
   * `mid` 回答"现在什么价"，`ref_mid` 回答"引擎按什么价挂的"。
   */
  ref_mid?: number | null;
  top: { bid: number | null; bid_qty: number | null; ask: number | null; ask_qty: number | null } | null;
  top_age_ms: number | null;
  spread_bp: number | null;
  ladder: { bids: HftLadderLevel[]; asks: HftLadderLevel[]; levels: number; ts_ms: number } | null;
  mine: {
    bid: number | null;
    ask: number | null;
    bid_width_bp: number | null;
    ask_width_bp: number | null;
    /** 我方挂单价**前方**（更优价）累计名义额 USD —— "深度"对做市的意义所在 */
    bid_queue_ahead_usd: number | null;
    ask_queue_ahead_usd: number | null;
    quoted_age_ms: number | null;
  };
  position: {
    qty: number;
    avg_px: number | null;
    avg_mid: number | null;
    opened_ts: number | null;
    last_ts: number | null;
    unrealized_usd: number | null;
    hold_ms?: number;
  };
  recent_fills: Array<{
    ts: string | null;
    side: string | null;
    price: number | null;
    qty: number | null;
    net_bp: number | null;
    is_close: boolean;
  }>;
}

export interface HftUniverseScoreRow {
  symbol: string;
  spread_med_bp: number | null;
  spread_p25_bp: number | null;
  book_updates: number;
  trades_total: number;
  has_depth: boolean;
  score: number | null;
  selected?: boolean;
  reject_reason?: string | null;
}

export interface HftLiveStatus {
  lane_id: string;
  exists: boolean;
  mode: string;
  status: "active" | "paused" | "stopped";
  keys_configured: boolean;
  caps: {
    total_notional_usd: number;
    per_symbol_usd: number;
    daily_loss_usd: number;
    reject_break: number;
    cooldown_s: number;
  };
  snapshot: {
    fills?: number;
    ticks?: number;
    day_pnl_usd?: number;
    bridge?: {
      enabled?: boolean;
      balance?: {
        total_equity?: number;
        available_balance?: number;
        unrealized_pnl?: number;
        day_start_equity?: number;
      } | null;
      positions?: Array<{
        symbol: string;
        side: string;
        size: number;
        entry_price: number;
        unrealized_pnl: number;
        notional: number;
      }>;
      orders?: Array<{
        id: string;
        symbol: string;
        side: string;
        price: number;
        amount: number;
        status: string;
      }>;
      caps?: Record<string, number>;
      /** [h674] 随权益浮动的有效上限(权益×倍数,绝对值只当天花板) */
      caps_effective?: {
        total: number;
        per_symbol: number;
        daily_loss: number;
        equity: number;
      };
      cooldown?: boolean;
      reject_streak?: number;
    } | null;
  };
  actions_available: boolean;
  as_of: string;
}

/** [h722] 自进化层状态(只读聚合) */
export interface HftEvolution {
  module: string;
  lane_id: string;
  streak_positive_days: number;
  daily: {
    date: string;
    legs: number;
    net_usd: number;
    net_bp_per_leg: number;
    active_hours: number;
  }[];
  gate_proposals: {
    gate: string;
    n: number;
    mean_cf_bp: number;
    t: number;
    param: string;
    current: number;
    proposed: number;
    rollback: number;
    reason: string;
  }[];
  bandit: {
    current: string[];
    proposed: string[];
    new_in: string[];
    dropped: string[];
    mode: string | null;
  };
  surface: {
    ts: number | null;
    per_symbol: unknown[];
    net_band: Record<string, number>;
    processed: boolean;
  };
  pending_verdicts: { param: string; new: number; rollback: number; verdict_at: number }[];
  playbook_laws: number;
  markout_kpi: {
    n: number | null;
    markout_bp: number | null;
    capture_bp: number | null;
    adverse_capture_ratio: number | null;
    verdict: string | null;
    ts: number | null;
  };
  lane_pause: {
    counts: Record<string, number>;
    last: { reason: string; ts: number; symbol: string } | null;
  };
  as_of: string;
}

export interface HftOverview {
  module: string;
  lane_id: string;
  mode: string | null;
  status: string | null;
  symbols: string[];
  fixed: string[];
  ai: string[];
  universe_as_of: string | null;
  /** "event_study" = 事件研究排名（试跑口径）；"coin_selector" = 旧机械选币 */
  universe_source?: string;
  /** 试跑上下文（无试跑时各字段为 null） */
  trial?: {
    stage?: string | null;
    started_at?: string | null;
    judge_at?: string | null;
    verdict_p2?: string | null;
    verdict_uni?: string | null;
  };
  rejected: Record<string, string>;
  vetoed: Record<string, string>;
  scored: HftUniverseScoreRow[];
  /** 运行时实际在采集合（进程刚重启时可能为空，会随 /hft/board 逐步填充） */
  depth_symbols: string[];
  depth_symbols_count: number;
  /** 深度采集**配置口径**名单（稳定分母，来自采集器 --depth-symbols） */
  depth_configured?: string[];
  depth_configured_count?: number;
  /** 本宇宙内有多少币有深度采集 */
  depth_in_universe?: string[];
  as_of: string;
}

export interface HftBoardResponse {
  module?: string;
  lane_id: string;
  as_of: string;
  depth_symbols?: string[];
  cards: HftBoardCard[];
  /**
   * 运行态（我方挂单/持仓）的来源：
   * `snapshot_file` = worker 每 15s 原子写出的跨进程快照（**准实时，正确来源**）；
   * `in_process_runner` = HTTP 进程内缓存实例（该进程不 tick，会陈旧）。
   */
  state_source?: string | null;
  /** 运行态快照的年龄（ms）。前端应如实展示，不要假装是实时的。 */
  state_age_ms?: number | null;
  error?: string;
}

export interface HftUniverseScoreResponse {
  module: string;
  fixed: string[];
  ai: string[];
  symbols: string[];
  scored: HftUniverseScoreRow[];
  rejected: Record<string, string>;
  note: string;
  as_of: string;
}

/** [2026-09-27] 分币种实盘判定（现行口径：事件研究排名 + 实盘判定去留） */
export interface HftUniverseLiveRow {
  symbol: string;
  legs: number;
  net_bp: number;
  net_bp_per_leg: number | null;
  net_usd: number;
  price_usd: number;
  spread_usd: number;
  notional_usd: number;
  legs_per_hour: number | null;
  hint: string;
}

export interface HftUniverseLiveResponse {
  module: string;
  lane_id: string;
  since: string | null;
  hours_elapsed: number | null;
  trial: {
    stage?: string | null;
    verdict_p2?: string | null;
    judge_at?: string | null;
  };
  rows: HftUniverseLiveRow[];
  totals: {
    legs: number;
    net_bp: number;
    net_usd: number;
    net_bp_per_leg: number | null;
    legs_per_hour: number | null;
  };
  note: string;
  as_of: string;
}

/** 实盘就绪度。`ready` 恒为 false —— 切实盘需人工改配置 + 独立评审。 */
export interface HftLiveReadiness {
  credentials: number;
  enabled_credentials: number;
  ready: boolean;
  reason: string;
}

export interface HftOpenPosition {
  symbol: string;
  qty: number;
  avg_mid: number | null;
  mid: number | null;
  unrealized_usd: number | null;
}

export interface HftAccount {
  module: string;
  lane_id: string;
  mode: string | null;
  status: string | null;
  strategy_type: string;
  venue: string;
  paper: {
    account_id: number | null;
    default_balance: number;
    shadow_equity: number;
    /** 持仓浮盈（后端用运行态持仓 × 当前盘口 mid 现算） */
    unrealized_usd: number;
    realized_usd: number | null;
    /** 已实现 + 未实现 */
    total_pnl_usd: number | null;
    open_positions: HftOpenPosition[];
    /**
     * [F297 2026-09-21] 累计手续费（**负值 = 支出**）。
     *
     * 为什么这条车道必须把它显出来：入场腿（maker）费率恒 0 —— 完全免费；
     * 强平腿（taker）恒 −4bp。实测强平腿累计 taker 费 −$50.87，而整夜亏损
     * −$52.20 ⇒ **97% 的亏损就是 taker 费**。它不是"明细项"，是第一成本项。
     */
    fee_usd: number | null;
    /** 付费成交笔数（= taker 腿数；maker 费率 0 不计入） */
    fee_fills: number | null;
    /** 对照口径：这些成交若**全按 taker** 计会是多少（用来说明 maker 免费省了多少） */
    fee_gross_usd: number | null;
    /**
     * [F300] **全历史**手续费（审计口径，不参与对账）。
     *
     * 重置只把账户设回起始额、把统计时代清空，**不清历史**。
     * 之所以要单独显示：实测 taker 费曾占整夜亏损 **97.4%**（−$51.29 / −$52.20），
     * 这条教训值得留着；但绝不能让读者把它和「当前时代」的 `fee_usd` 混起来
     * （用户实测就报过这个困惑："重置了，哪里好像还是不对"）。
     */
    fee_lifetime_usd: number | null;
    fee_lifetime_fills: number | null;
    /**
     * [h433 2026-09-28] **不可清零的账户总账**（来源 `arbitrage_paper_ledgers`，
     * append-only：任何参数部署 / 统计时代切换 / 账户重置都不清零）。
     *
     * 病根：`realized_pnl` 是账户行字段（重置清零）、`fee_usd` 按 `stats_since`
     * 裁剪——而每次参数部署都会重写 `stats_since` ⇒ 面板数字被反复清零，
     * 与"承载全部历史的余额"永远对不上（用户实测反馈）。
     *
     * 恒等式：`initial + capital_adj + realized_all + fee_all = equity`
     * （`recon_residual_usd` 恒为 0；非 0 即流水与余额脱钩）。
     */
    book?: {
      /**
       * [h476 2026-09-29] **账户口径起点**（= `account_reset_at`，只在"重置账户"时变）。
       *
       * 事故：此前这里显示的是 `stats_since`（"试跑时代起点"），而 legacy 判定脚本
       * 每次 rollback 都会把它改写 ⇒ 面板上的起点跳一次，看起来就是"手续费又被重置"
       * （数值块用的是 `account_reset_at`，是对的，展示与数值不同源 ⇒ 才造成误读）。
       * 现与数值同源，且 `since_basis` 明确告知用的是哪个键。
       */
      since?: string | null;
      /** `since` 的来源键：`account_reset_at`（正常）或 `stats_since`（回退） */
      since_basis?: string | null;
      /** 试跑时代起点：**会变**，仅参考，不是账户口径 */
      stats_since?: string | null;
      stats_since_note?: string | null;
      initial_usd: number;
      /** 外部划拨（注资/抽离/重置补平）——反解值，正=净流入 */
      capital_adj_usd: number;
      realized_all_usd: number;
      fee_all_usd: number;
      equity_usd: number;
      recon_residual_usd: number;
      n_ledger_entries: number;
      note: string;
    } | null;
    name: string | null;
    total_equity: number | null;
    /**
     * [F297] `total_equity` 的来源口径，由后端给出，用于让"这个数字怎么来的"可见。
     * 实测值 `"available+frozen+upnl"`（活值）。**前端取权益必须优先用它**，
     * 而不是 `shadow_equity` —— 后者是注册表快照、会陈旧。
     */
    total_equity_source: string | null;
    available_balance: number | null;
    realized_pnl: number | null;
    account_status: string | null;
    updated_at: string | null;
  };
  live: HftLiveReadiness;
  sizing: {
    compound_ratio: number;
    fill_notional: number;
    min_notional_usd: number;
  };
  /** 每 5 分钟一条的方向判定摘要，新的在后面 */
  direction_log?: { ts: number; text: string }[];
  /** [h626 确定性版] 方向判定状态（标题/徽标用；不再由前端硬编码"已停"） */
  direction_state?: {
    stopped: boolean;
    source: string;
    every_sec: number | null;
    as_of: number | null;
    /** [h665f] 方向卡当前动作:每币封哪边加仓("buy"/"sell") */
    block_add?: Record<string, "buy" | "sell">;
  };
  /** [h672] 前端警报事件流(最近 20 条:闸门风暴/熔断/自愈/对账/429) */
  events?: Array<{ ts: number; kind: string; msg: string; n?: number }>;
  /** [h672] 闸门计数(前端 3s 差分算触发率) */
  skip_counts?: Record<string, number>;
  venue_filters?: { rate_used_per_min?: number; rate_429?: number; filter_skips?: number };
  q_speed?: Record<string, {
    q: number; action: string; mult: number;
    /** [h686] 停加仓时长(分钟)与试探复入倒计时(分钟) */
    paused_min?: number; retry_in_min?: number;
  }>;
  /** [h664] 方向分数影子快照：每币 {d, mp, ofi}（融合分数，影子不作用交易） */
  direction_score?: Record<string, { d: number | null; mp?: number; ofi?: number }>;
  as_of: string;
}

export interface HftFill {
  symbol: string;
  event: string;
  ts: string | null;
  notional_usd: number;
  spread_bp: number;
  funding_bp: number;
  price_bp: number;
  fee_bp: number;
  slippage_bp: number;
  net_bp: number;
  net_usd: number;
  position_id: string | null;
  position_id_state: string | null;
}

export interface HftFillsResponse {
  module: string;
  lane_id: string;
  hours: number;
  count: number;
  summary: {
    fills: number;
    spread_bp_sum: number;
    price_bp_sum: number;
    fee_bp_sum: number;
    net_bp_sum: number;
    notional_sum: number;
    /**
     * 金额（USD）。**逐行** `net_bp/1e4 × notional` 求和 ⇒ 与 `items[].net_usd`
     * 的求和逐分一致。
     *
     * ⚠️ 早期版本算的是 `Σnet_bp/1e4 × Σnotional`（两个"和"相乘），把每笔的 bp
     * 当成了全部名义的 bp：实测近 24h 虚报成 **-$15.83**，而真实是 **-$0.22**
     * （模拟账户同期已实现 -$0.15）——71 倍虚亏。**不要再写成 Σbp × Σnotional。**
     */
    net_usd_sum: number;
    spread_usd_sum?: number;
    price_usd_sum?: number;
    fee_usd_sum?: number;
    slippage_usd_sum?: number;
    /** 名义加权 bp = 金额 ÷ 名义 × 1e4；唯一有金融含义的"平均 bp"（分母为 0 时 null） */
    net_bp_w?: number | null;
    spread_bp_w?: number | null;
    price_bp_w?: number | null;
    fee_bp_w?: number | null;
  };
  items: HftFill[];
  as_of: string;
}

export interface HftConfig {
  module: string;
  lane_id: string;
  mode: string | null;
  status: string | null;
  paper: { account_id: number | null; shadow_equity: number; default_balance: number };
  live: HftLiveReadiness;
  params: Record<string, number | string | null>;
  limits: Record<string, number | string | null>;
  /** 每项参数的中文含义（后端自带，前端不硬编码标签表） */
  notes: Record<string, string>;
  /** 枚举值的中文标签，如 `side_mode: { both: "双侧", ... }` */
  enum_labels?: Record<string, Record<string, string>>;
  as_of: string;
}

/** [hero 2026-10-07] 页面顶部大字区：一眼看清"今天赚没赚"。字段与后端 /api/hft/hero 一一对应。 */
export interface HftHero {
  /** 当前权益（USD） */
  equity: number;
  /** 今日盈亏（USD，正=赚 负=亏） */
  today_pnl_usd: number;
  /** 今日成交笔数 */
  today_fills: number;
  /** 今日胜率 0~1 */
  win_rate: number;
  /** 盈亏比 = 平均赢 ÷ 平均亏绝对值（<1 即"赚小亏大"） */
  pl_ratio: number;
  /** 平均盈利单 bp */
  avg_win_bp: number;
  /** 平均亏损单 bp（负值） */
  avg_loss_bp: number;
  /** 总盈亏（USD，当前权益 − 起始额） */
  total_pnl_usd?: number | null;
  /** 总手续费（USD，历史累计，重置不清） */
  fee_lifetime_usd?: number | null;
  /** 高频交易 worker 是否在线 */
  worker_alive: boolean;
  as_of: string;
}

// ── 客户端 ──────────────────────────────────────────────

/**
 * ⚠️ 超时必须显式放大：`apiRequest` 默认 **30s**，而本模块两个端点是重查询——
 * 实测 `/hft/overview` ≈ 9s、`/hft/board` ≈ 10.6s（首次会做一次深度名单全表发现）。
 * 默认超时在网络稍慢时会把首屏打成"失败"，而实际只是慢。
 */
const HFT_TIMEOUT_MS = 90_000;

/**
 * ⚠️ [F329 2026-09-21] `/hft/universe/score` 需要**单独**的超时，比 HFT_TIMEOUT_MS 更大。
 *
 * 实测（H192，2026-09-21）：该端点冷查询 **119.5s**，而 HFT_TIMEOUT_MS=90s
 * ⇒ **必然 abort** ⇒ 界面两张卡片同时显示
 *     `刷新失败: signal is aborted without reason`
 * （「选币评分」和「硬性拒绝」都读这一个端点，所以一起挂）。
 *
 * 底层 `compute_hft_stats` 带 300s 进程内缓存，因此每 5 分钟会有一次冷查询；
 * 前端轮询是 120s ⇒ 命中缓存的那些请求 <0.3s，但每 2~3 次就会撞上一次冷的。
 *
 * 取 240s：> 实测 119.5s，且 > 300s 缓存周期的一半，
 * 保证冷查询不会因为前端先放弃而白跑（后端仍会把结果写进缓存）。
 * 真正的修法是让那个查询变快（见 H192 的 profiling），这是**止血**。
 */
const UNIVERSE_SCORE_TIMEOUT_MS = 240_000;

export const hftApi = {
  /** 最小存活探针（前端首屏判断模块是否可用，不触碰重查询） */
  ping: () =>
    apiRequest<{ ok: boolean; module: string; symbols: string[]; as_of: string }>(
      "/hft/ping",
      { timeout: HFT_TIMEOUT_MS }
    ),

  /** [hero] 顶部大字区：今日盈亏/权益/胜率/盈亏比（轻量，2s 轮询） */
  hero: () => apiRequest<HftHero>("/hft/hero", { timeout: HFT_TIMEOUT_MS }),

  /** [h665] 实盘高频交易车道状态（与模拟高频交易完全分离） */
  liveStatus: () =>
    apiRequest<HftLiveStatus>("/hft/live/status", { timeout: HFT_TIMEOUT_MS }),

  /** [h665] 实盘一键撤单（kill switch，须输入确认词"撤单"） */
  liveKill: (confirm: string) =>
    apiRequest<{ ok: boolean; cancelled: number; lane_id: string; status: string }>(
      "/hft/live/kill",
      { method: "POST", body: JSON.stringify({ confirm }), timeout: HFT_TIMEOUT_MS }
    ),

  /** [h665] 实盘车道启停（切 active 须确认词"启动"） */
  liveControl: (status: "active" | "paused" | "stopped", confirm: string) =>
    apiRequest<{ ok: boolean; lane_id: string; status: string }>(
      "/hft/live/control",
      { method: "POST", body: JSON.stringify({ status, confirm }), timeout: HFT_TIMEOUT_MS }
    ),

  /** 模块总览：宇宙构成（固定 ∪ AI 选币）+ 运行状态 + 深度覆盖 */
  overview: () => apiRequest<HftOverview>("/hft/overview", { timeout: HFT_TIMEOUT_MS }),

  /** [h722] 自进化层状态（闸门提案/bandit/机械曲面/快判/连续正净,120s 轮询） */
  evolution: () =>
    apiRequest<HftEvolution>("/hft/evolution", { timeout: HFT_TIMEOUT_MS }),

  /** [2026-09-23] HFT 净收益曲线（lane_ledger 事件溯源：notional × net_bp / 1e4） */
  equitySeries: (days: number = 30) =>
    apiRequest<{
      period: string;
      source: string;
      lane_id: string;
      initial_balance: number;
      start_equity: number;
      realized_end: number;
      floating_now: number | null;
      equity_total_now: number;
      balance_total_equity: number | null;
      peak_equity: number;
      max_drawdown_usd: number;
      max_drawdown_pct: number;
      counts: { closed_in_window: number; closed_before_window: number; open_now: number };
      costs_in_window: { fees: number; entry_fees: number | null; exit_fees: number | null; funding_net: number | null };
      points: { t: number; v: number }[];
      reconcile: {
        realized_end: number;
        floating_now: number | null;
        realized_plus_floating: number;
        balance_total_equity: number | null;
        diff: number | null;
        ok: boolean | null;
        note?: string;
      };
      baseline?: { note?: string };
    }>(`/hft/equity-series?days=${days}`, { timeout: HFT_TIMEOUT_MS }),

  /** 实时深度看板：20 档真实价量 + 我方挂单 + 队列前方量 */
  board: (symbols?: string[], depth = 20) => {
    const q = new URLSearchParams({ depth: String(depth) });
    if (symbols && symbols.length) q.set("symbols", symbols.join(","));
    return apiRequest<HftBoardResponse>(`/hft/board?${q.toString()}`, {
      timeout: HFT_TIMEOUT_MS,
    });
  },

  /** 选币评分明细（机械分 + 硬闸拒绝原因）—— 冷查询实测 119.5s，见 UNIVERSE_SCORE_TIMEOUT_MS */
  universeScore: (aiSlots = 5) =>
    apiRequest<HftUniverseScoreResponse>(`/hft/universe/score?ai_slots=${aiSlots}`, {
      timeout: UNIVERSE_SCORE_TIMEOUT_MS,
    }),

  /**
   * [2026-09-27] 分币种实盘判定（当前试跑时代）—— 现行选币口径的实时事实。
   *
   * 取代旧的「选币评分（点差×吞吐）」+「硬闸拒绝」两张卡：那两张属已停用的
   * 机械选币口径，且硬闸与现行宇宙自相矛盾（ETH/ARB 既在宇宙又被列"拒绝"）。
   */
  universeLive: () =>
    apiRequest<HftUniverseLiveResponse>("/hft/universe/live", { timeout: HFT_TIMEOUT_MS }),

  // ── 账户与控制 ──────────────────────────────────────

  /** 模拟账户 + 运行状态 + 实盘就绪度 */
  account: () => apiRequest<HftAccount>("/hft/account", { timeout: HFT_TIMEOUT_MS }),

  /** 重置模拟账户金额（默认 300U）。走 runner 的运行时安全重置。 */
  resetAccount: (balance: number) =>
    apiRequest<{ ok: boolean; lane_id: string; balance: number }>("/hft/account/reset", {
      method: "POST",
      body: JSON.stringify({ balance }),
      timeout: HFT_TIMEOUT_MS,
    }),

  /** 开启 / 暂停 / 停止 */
  setStatus: (status: "active" | "paused" | "stopped") =>
    apiRequest<{ ok: boolean; lane_id: string; status: string }>("/hft/control/status", {
      method: "POST",
      body: JSON.stringify({ status }),
      timeout: HFT_TIMEOUT_MS,
    }),

  /**
   * 模拟盘 / 实盘 / 停用 切换。
   *
   * ⚠️ 传 `live` 会收到 **409**（后端 fail-closed：未证明正期望）。
   * 调用方必须把 detail 文案展示给用户，不要静默吞掉。
   */
  setMode: (mode: "paper" | "live" | "disabled") =>
    apiRequest<{ ok: boolean; lane_id: string; mode: string }>("/hft/control/mode", {
      method: "POST",
      body: JSON.stringify({ mode }),
      timeout: HFT_TIMEOUT_MS,
    }),

  /** 报价参数与风控限额（只读，含每项中文说明） */
  config: () => apiRequest<HftConfig>("/hft/config", { timeout: HFT_TIMEOUT_MS }),

  /** 成交记录（六维账本；含区间汇总） */
  fills: async (limit = 50, hours = 24) => {
    const raw = await apiRequest<HftFillsResponse & { fills?: Array<Record<string, unknown>> }>(
      `/hft/fills?limit=${limit}&hours=${hours}`,
      { timeout: HFT_TIMEOUT_MS }
    );
    if (Array.isArray(raw.items) && raw.items.length > 0) return raw;
    // 重建后的接口把明细放在 fills，字段名也不完全一样。这里补成页面认识的形状，避免整页崩掉。
    const rows = Array.isArray(raw.fills) ? raw.fills : [];
    const items: HftFill[] = rows.map((f) => {
      const notional = Number(f.notional_usd ?? f.notional ?? 0);
      const netBp = Number(f.net_bp ?? 0);
      return {
        symbol: String(f.symbol ?? ""),
        event: String(f.event ?? "fill"),
        ts: f.ts == null ? null : String(f.ts),
        notional_usd: notional,
        spread_bp: Number(f.spread_bp ?? 0),
        funding_bp: Number(f.funding_bp ?? 0),
        price_bp: Number(f.price_bp ?? 0),
        fee_bp: Number(f.fee_bp ?? 0),
        slippage_bp: Number(f.slippage_bp ?? 0),
        net_bp: netBp,
        net_usd: Number(f.net_usd ?? (netBp / 1e4) * notional),
        position_id: f.position_id == null ? null : String(f.position_id),
        position_id_state: f.position_id_state == null ? null : String(f.position_id_state),
      };
    });
    const netUsd = items.reduce((s, x) => s + x.net_usd, 0);
    const notionalSum = items.reduce((s, x) => s + x.notional_usd, 0);
    return {
      ...raw,
      items,
      summary: raw.summary ?? {
        fills: items.length,
        spread_bp_sum: 0,
        price_bp_sum: 0,
        fee_bp_sum: 0,
        net_bp_sum: items.reduce((s, x) => s + x.net_bp, 0),
        notional_sum: notionalSum,
        net_usd_sum: netUsd,
        net_bp_w: notionalSum > 0 ? (netUsd / notionalSum) * 1e4 : null,
      },
    };
  },
};
