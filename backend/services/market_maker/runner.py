# -*- coding: utf-8 -*-
"""[F60] L1 做市车道驱动器（影子期 = 模拟账户直跑）。

设计依据：《复合策略与交易系统全面改造设计_V2》§1.3 / §3.2

分层：
  - **纯逻辑层**（本文件上半部分，无 IO）：`SymbolState` / `plan_tick` / `TickDecision`。
    一个 tick = 「检查旧挂单成交 → 记账 → 超时平仓 → 重挂新单」，
    与 F59 回放**同口径**（`fill_side` + `InventoryBook`），保证影子结论可复算。
  - **驱动层**（本文件下半部分）：`ShadowRunner` 负责读盘口/成交、持久化状态、
    写 `lane_ledger`、刷新 `lane_registry`、产出影子期报告。

为什么单独写驱动器而不是复用 paper_engine.place_order：
  `paper_engine.place_order` 面向「方向性开仓」，会经过 trade_gate / scalp 开仓闸门 /
  持仓合并，且每次下单落 PaperOrder 行；做市一秒数张挂单会把它压垮。做市需要的是
  「常驻挂单 + 区间成交判定」，因此用独立车道账本（`lane_ledger`）+ 独立状态表，
  资金仍挂在模拟账户权益下（`ShadowRunner.account_id`）。
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

DEFAULT_LANE_ID = "mm_asterdex"
DEFAULT_VENUE = "asterdex"
# [F300 2026-09-16] 兜底宇宙（仅在 lane_registry.meta.symbols 缺失时生效）。
# 选标的证据：事件研究（9,379 个被动成交事件，`research_l1/engine/event_study*.py`）
# 按「点差 ≥1bp × 有真实流量 × 漂移中性化边际 > 0」三条同时满足筛出的集合。
# 旧值 ["BTC","ETH","BNB","XRP","SOL","DOGE"] 已证不适用：
#   · ETH/XRP 漂移中性化后为负（−2.9bp / −2.1bp），ETH 点差仅 0.04bp；
#   · BTC 的 min leg = 0.001×$75,818 = $75.82 > $30 单腿预算 ⇒ 规格上不可下单。
# ⚠️ [F249 2026-09-20] 兜底宇宙改为**空 = fail-closed**（"幽灵标的"根因之二）。
#
#   原值 `["ZEC","ASTER","SOL","DOGE","TAO"]` 是 F77 时代的默认宇宙。它现在有两个问题：
#     ① ZEC/TAO **已停采**（09-17 起无 book/depth）⇒ 挂单永远不会成交；
#     ② 它是 `meta.symbols` **为空时的兜底**（见 get_runner）⇒ 任何注册表未就绪的
#        时刻（首次建表、竞态、手工清空）都会让车道去挂这两个僵尸币。
#   这正是"TAO/ZEC 总是幽灵出现"的机制：**只要有兜底名单，它们就会回来**。
#   ⇒ 改成空列表 + 下方 `get_runner` 的空宇宙保护：**没配置宇宙就不跑**，
#     而不是"跑一个过时的默认宇宙"。空转比挂僵尸币安全。
DEFAULT_SYMBOLS: list = []
FILL_NOTIONAL = float(os.getenv("F60_FILL_NOTIONAL", "100"))
TAKER_FEE_BP = float(os.getenv("F60_TAKER_FEE_BP", "4"))
MAX_MAKER_FEE_BP = float(os.getenv("F60_MAX_MAKER_FEE_BP", "0.5"))  # 费率闸门
# 数据新鲜度闸门：盘口快照超过这个年龄就**不报价**。
# 事故背景：2026-08-18 之后 asterdex 的盘口/成交采集停止，最新快照已陈旧 22 天，
# 若不加闸门，驱动器会拿 22 天前的价格挂单（BTC 64k vs 实际 78k）。
MAX_DATA_AGE_SEC = float(os.getenv("MM_MAX_DATA_AGE_SEC", "180"))
# [F134] "地板报价"判定阈值（bp）：挂单宽度 ≤ 该值视为贴地板的减仓腿
# （`min_width_reduce_bp` 目前 1.0bp，取 1.5 留出浮点与偏斜余量）。
FLOOR_QUOTE_BP = float(os.getenv("MM_FLOOR_QUOTE_BP", "1.5"))
# 队列保守假设：价格需穿过挂单价多少 bp 才算我们成交（0=假设排在队列最前）
PENETRATION_BP = float(os.getenv("F60_PENETRATION_BP", "0.0"))
# [F92 2026-09-14] 成交桶网格：market_trades_aggregated 与盘口快照同为 15s 桶、
# 时间戳=桶起点，实测「桶结束 + ~1s」落库且不再回填。窗口必须锚在桶标签上。
SEG_BUCKET_MS = int(os.getenv("MM_SEG_BUCKET_MS", "15000"))
# [F279 2026-09-16] 断点判定阈值：快照间隔 > 该值 ⇒ 视为"停机断点"，按
# `LaneRiskLimits.mid_splice_on_gap` 决定是否补齐。默认 90s = 6 个快照周期
# （实测网格 15s ✓；90s 内的抖动属正常迟到，不当断点 ✗）。
MID_SPLICE_MIN_GAP_MS = int(os.getenv("MM_MID_SPLICE_MIN_GAP_MS", "90000"))


# ═══════════════════════ 纯逻辑层 ═══════════════════════

@dataclass
class SymbolState:
    """一个币的做市运行态（可持久化）。"""

    symbol: str
    qty: float = 0.0
    avg_px: float = 0.0
    avg_mid: float = 0.0
    # [h442 2026-09-28] 最近一条加仓腿的中价——止损参考价备选（stop_ref_last_leg>0 时
    # 取"最差入场"口径：多头用 max(avg,last)、空头用 min(avg,last) ⇒ 更快触发，
    # 减少多腿摊低后 avg 拖后导致的深亏与"盈利仓被止损"两个偏差）。平仓即清零。
    last_entry_mid: float = 0.0
    opened_ts: float = 0.0
    # 真实开仓时刻。opened_ts 在「成交时已经吃亏」时会被改成 now-60 秒，
    # 用来催离场；那个改写不能拿去算持仓多久，否则学习日志的持仓分桶是假的。
    opened_ts_true: float = 0.0
    last_ts: float = 0.0
    quote_bid: float = 0.0
    quote_ask: float = 0.0
    quote_mid: float = 0.0
    quote_ts: float = 0.0
    toxic_streak: int = 0
    # 波动归一的滚动窗口（相对价差），窗口长度固定 20。
    # 不能用「上次挂单时的中价」做基准：断流 22 天后中价差 22%，
    # 会算出 sigma=450 并永久 vol_pause（实测事故）。
    spread_hist: List[float] = field(default_factory=list)
    spread_baseline: float = 0.0
    mid_hist: List[float] = field(default_factory=list)      # 近 N 期中价（趋势闸门）
    vol_baseline_bp: float = 0.0                             # 已实现波动基准（F71b）
    # [F342 2026-09-22] 突发事件闸（暴涨/暴跌）的运行态。
    # `sudden_move_until` = 冷却期截止时刻（epoch 秒）；期间"停新挂单、
    # 但允许已有挂单被动成交"。**必须自解除**（与 `toxic_streak` 同一个坑：
    # 若只有"进入"没有"退出"，会变成永久停摆 —— 本会话已在 F98 栽过一次）。
    sudden_move_until: float = 0.0
    sudden_move_hits: int = 0
    # [F92 2026-09-14] 已消费到的成交桶标签（桶起点 ms）：窗口下界，持久化以便
    # 重启后不漏桶。0 = 尚未消费（冷启动回退一个桶）。
    last_seg_ms: int = 0
    # [F102 2026-09-14] `mid_hist` 最后一次追加所用的**快照时间戳**。
    # 缺陷现场：此前每个 tick 都无条件 append，而 tick 快于快照更新（实测 240 个
    # 样本里只有 52~109 个**不同**中价，重复率 55~78%）⇒ 240 样本窗口覆盖的**墙钟
    # 时间被拉长 2~4 倍**，两个依赖 mid_hist 的信号全部失真：
    #   - 冻结行情检测 `slow_move_bp`（240 期单步最大移动）被拉长 ⇒ 更容易超过
    #     frozen_max_move(8bp) ⇒ **冻结档（3bp 窄挂）很少生效**，实盘长期按 6~9bp 挂，
    #     而回放（每快照一条、无重复）经常进入冻结档 ⇒ 实测实盘成交只有回放的 0.38×
    #     （蒙特卡洛 12 个实现的分布 107~119 笔 vs 实盘 43 笔）；
    #   - `vol_cur`（20 期已实现波动）被重复值注入 0 收益 ⇒ 波动低估。
    # 修：仅在**快照更新**时追加（与回放「每快照一条」完全同口径）。
    last_mid_src_ms: int = 0
    # [F235 2026-09-15] 止损"先 maker 后 taker"的宽限计时：首次触发止损条件的时刻；
    # 0 = 未在宽限中。`stop_maker_grace_sec > 0` 时，触发后先让减仓侧 maker 单
    # 继续挂 N 秒（正常报价流程每个 tick 都在挂减仓侧），超时才 taker 平仓；
    # 止损条件解除（价格回摆）⇒ 归零。持久化以便重启后续接 ✓。
    stop_since: float = 0.0
    # [F296 2026-09-21] 超时出库被"只挂单不 taker"挡下的累计次数。
    # 用途：这是「超时改为被动出库」这条改动的**核心监控指标**。
    #   · 若该值持续增长而 `flatten` 不增长 ⇒ 改动生效：出库改走被动了 ✓
    #   · 若该值增长且该币 `qty` 长期不归零 ⇒ 有币"卡住不回摆"，需人工看
    #   · 若同时 `skip_counts.symbol_exposure/net_exposure` 上行 ⇒ 敞口被占满、
    #     新仓被阻塞（这是本改动的已知代价，须权衡）
    timeout_exit_blocked: int = 0
    # [F301 2026-09-21] 止盈主动平仓的累计触发次数（监控用）：
    # 与 `timeout_exit_blocked` 一起读，判断出库路径的构成变化 ——
    # 止盈命中变多 ⇒ 出库从"等被动"转向"落袋赢家"，需同时看每笔净额是否改善。
    take_profit_hits: int = 0
    # 止盈"先 maker 后 taker"的宽限计时（`take_profit_maker_grace_sec > 0` 时才用）
    tp_since: float = 0.0
    # [F346 2026-09-23] 反转衰减离场的宽限计时（`reversal_decay_grace_sec > 0` 时用）：
    # 30s 趋势重新朝不利方向延伸 ≥ `reversal_decay_bp` 时触发，先让减仓侧 maker
    # 单挂 N 秒，超时才 taker；条件解除（价格回摆）⇒ 归零。
    decay_since: float = 0.0
    # [h338 2026-09-26] VPIN 的滚动 |OFI| 历史（20 桶 = 5 分钟，与 mid_hist 同口径：
    # 只在快照更新时追加）。持久化以便重启后不丢窗口。
    ofi_abs_hist: List[float] = field(default_factory=list)
    # [h454 2026-09-28] 自适应趋势门槛的分布输入：近 240 期 |r300|（≈1h）。
    # 门槛 = max(trend_only_bp, 该分布的分位 trend_only_q) ⇒ 通过率与市场活跃度无关。
    ar300_hist: List[float] = field(default_factory=list)
    # [h389 2026-09-27] 尾随锁利状态：当前持仓的峰值浮盈（bp，MFE 口径）与
    # 其所锚定的 avg_mid（avg_mid 变化=换仓/平仓/新开 ⇒ 自动重置）。
    mfe_bp: float = 0.0
    mfe_avg_mid: float = 0.0
    # [h395 2026-09-27] #16③ 止损后降腿量：最近一次强制止损离场
    # （stop_loss_taker / trail_lock_taker）的时刻；30min 内加仓腿名义衰减。
    last_stop_ts: float = 0.0
    # [h403 2026-09-27] #8/#15 分形态持有期：本仓开仓时刻的形态标记
    # （"P1"/"P45"/""=未标记；平仓即清）。超时上界按标记取 p1/p45_hold_sec。
    pattern_tag: str = ""
    # ── [h624] markout 探针：待解析队列 + 已解析滚动样本（设计 §4.1）────────
    #
    # pending: {ts, side, fill_px, capture_bp, notional}
    # samples: {markout_bp, capture_bp, notional, ts}（名义加权滚动）
    pending_markouts: List[Dict[str, float]] = field(default_factory=list)
    markout_samples: List[Dict[str, float]] = field(default_factory=list)
    # [h624] jump_pause 冷却截止（epoch s）；0 = 未暂停
    jump_pause_until: float = 0.0
    # [h624] 近窗止损腿占比估计（由 runner 回填；计划态可读）
    stop_share_hat: float = 0.0
    # [h625] 深度选档停留：until 之前沿用 book_slot_open，避免一跳价就换币
    book_slot_until: float = 0.0
    book_slot_open: bool = True
    # [h740 2026-10-03] R2b 二期:软模式单侧调制标记。""/buy/sell/both。
    # 每个 tick 由 plan_tick 复位,各闸门在软模式下不封侧、只在此标记应缩量
    # 的方向;fill 段记账前按此缩加仓腿量(与 book 同源,不分叉)。
    soft_side: str = ""
    # 主动流：进场时选定的持有秒数，以及当时的可执行期望（bp）。
    flow_hold_sec: float = 0.0
    flow_mu: float = 0.0
    # [h865] 本仓属于哪套策略(S1/S3/S4),供往返账本按策略分开学习
    flow_strategy: str = ""
    # [h878] 成交后方向重算是否已做(每仓只算一次)
    flow_rechecked: bool = False
    # 这一张挂单前面还剩多少数量，以及已经打到这个价的数量。
    flow_queue_ahead: float = 0.0
    flow_queue_cum: float = 0.0
    # ── [2026-10-09 重复来回做市 ping-pong] 双侧挂单运行态 ──────────────
    # 空仓后歇息截止时刻（epoch s）：之前不挂新的进场对（默认 15s）。
    pp_rest_until: float = 0.0
    # 进场单武装时前档挂单量（薄量参考）与前排队列，按侧分开：
    pp_front0_bid: float = 0.0
    pp_front0_ask: float = 0.0
    pp_ahead_bid: float = 0.0
    pp_ahead_ask: float = 0.0
    # 打到我们挂单价的累计成交量（进场单队列消耗判定）：
    pp_cum_bid: float = 0.0
    pp_cum_ask: float = 0.0
    # [2026-10-09 进化重挂] 入场语境（哪侧先成交/价差/波动/前档量/regime），
    # 平仓时写进往返账作分桶键（进化层按此学习，不再用方向标签）。
    pp_entry_ctx: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol, "qty": round(self.qty, 10),
            "avg_px": round(self.avg_px, 10), "avg_mid": round(self.avg_mid, 10),
            "opened_ts": self.opened_ts,
            "opened_ts_true": self.opened_ts_true,
            "last_ts": self.last_ts,
            "last_entry_mid": round(self.last_entry_mid, 10),
            "quote_bid": self.quote_bid, "quote_ask": self.quote_ask,
            "quote_mid": self.quote_mid, "quote_ts": self.quote_ts,
            "toxic_streak": self.toxic_streak,
            "spread_hist": [round(x, 8) for x in (self.spread_hist or [])[-20:]],
            "spread_baseline": round(self.spread_baseline, 8),
            "mid_hist": [round(x, 10) for x in (self.mid_hist or [])[-240:]],
            "vol_baseline_bp": round(self.vol_baseline_bp, 4),
            "last_seg_ms": int(self.last_seg_ms or 0),
            "last_mid_src_ms": int(self.last_mid_src_ms or 0),
            "stop_since": float(self.stop_since or 0.0),
            "timeout_exit_blocked": int(self.timeout_exit_blocked or 0),
            "take_profit_hits": int(self.take_profit_hits or 0),
            "tp_since": float(self.tp_since or 0.0),
            "decay_since": float(self.decay_since or 0.0),
            "ofi_abs_hist": [round(x, 6) for x in (self.ofi_abs_hist or [])[-20:]],
            "ar300_hist": [round(x, 4) for x in (self.ar300_hist or [])[-240:]],
            "mfe_bp": round(self.mfe_bp, 4),
            "mfe_avg_mid": round(self.mfe_avg_mid, 10),
            "last_stop_ts": float(self.last_stop_ts or 0.0),
            "pattern_tag": str(self.pattern_tag or ""),
            "pending_markouts": list(self.pending_markouts or [])[-40:],
            "markout_samples": list(self.markout_samples or [])[-80:],
            "jump_pause_until": float(self.jump_pause_until or 0.0),
            "stop_share_hat": round(float(self.stop_share_hat or 0.0), 4),
            "book_slot_until": float(self.book_slot_until or 0.0),
            "book_slot_open": bool(self.book_slot_open),
            "soft_side": str(self.soft_side or ""),
            "flow_hold_sec": float(self.flow_hold_sec or 0.0),
            "flow_strategy": str(self.flow_strategy or ""),
            "flow_rechecked": bool(self.flow_rechecked),
            "flow_mu": float(self.flow_mu or 0.0),
            "flow_queue_ahead": float(self.flow_queue_ahead or 0.0),
            "flow_queue_cum": float(self.flow_queue_cum or 0.0),
            # [2026-10-09 ping-pong]
            "pp_rest_until": float(self.pp_rest_until or 0.0),
            "pp_front0_bid": float(self.pp_front0_bid or 0.0),
            "pp_front0_ask": float(self.pp_front0_ask or 0.0),
            "pp_ahead_bid": float(self.pp_ahead_bid or 0.0),
            "pp_ahead_ask": float(self.pp_ahead_ask or 0.0),
            "pp_cum_bid": float(self.pp_cum_bid or 0.0),
            "pp_cum_ask": float(self.pp_cum_ask or 0.0),
            "pp_entry_ctx": dict(self.pp_entry_ctx or {}),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SymbolState":
        return cls(
            symbol=str(d.get("symbol") or ""),
            qty=float(d.get("qty") or 0.0),
            avg_px=float(d.get("avg_px") or 0.0),
            avg_mid=float(d.get("avg_mid") or 0.0),
            last_entry_mid=float(d.get("last_entry_mid") or 0.0),
            opened_ts=float(d.get("opened_ts") or 0.0),
            opened_ts_true=float(d.get("opened_ts_true") or 0.0),
            last_ts=float(d.get("last_ts") or 0.0),
            quote_bid=float(d.get("quote_bid") or 0.0),
            quote_ask=float(d.get("quote_ask") or 0.0),
            quote_mid=float(d.get("quote_mid") or 0.0),
            quote_ts=float(d.get("quote_ts") or 0.0),
            toxic_streak=int(d.get("toxic_streak") or 0),
            spread_hist=[float(x) for x in (d.get("spread_hist") or [])],
            spread_baseline=float(d.get("spread_baseline") or 0.0),
            mid_hist=[float(x) for x in (d.get("mid_hist") or [])],
            vol_baseline_bp=float(d.get("vol_baseline_bp") or 0.0),
            last_seg_ms=int(d.get("last_seg_ms") or 0),
            last_mid_src_ms=int(d.get("last_mid_src_ms") or 0),
            stop_since=float(d.get("stop_since") or 0.0),
            timeout_exit_blocked=int(d.get("timeout_exit_blocked") or 0),
            take_profit_hits=int(d.get("take_profit_hits") or 0),
            tp_since=float(d.get("tp_since") or 0.0),
            decay_since=float(d.get("decay_since") or 0.0),
            ofi_abs_hist=[float(x) for x in (d.get("ofi_abs_hist") or [])],
            ar300_hist=[float(x) for x in (d.get("ar300_hist") or [])],
            mfe_bp=float(d.get("mfe_bp") or 0.0),
            mfe_avg_mid=float(d.get("mfe_avg_mid") or 0.0),
            last_stop_ts=float(d.get("last_stop_ts") or 0.0),
            pattern_tag=str(d.get("pattern_tag") or ""),
            pending_markouts=[dict(x) for x in (d.get("pending_markouts") or [])
                              if isinstance(x, dict)],
            markout_samples=[dict(x) for x in (d.get("markout_samples") or [])
                             if isinstance(x, dict)],
            jump_pause_until=float(d.get("jump_pause_until") or 0.0),
            stop_share_hat=float(d.get("stop_share_hat") or 0.0),
            book_slot_until=float(d.get("book_slot_until") or 0.0),
            book_slot_open=bool(d.get("book_slot_open", True)),
            soft_side=str(d.get("soft_side") or ""),
            flow_hold_sec=float(d.get("flow_hold_sec") or 0.0),
            flow_strategy=str(d.get("flow_strategy") or ""),
            flow_rechecked=bool(d.get("flow_rechecked") or False),
            flow_mu=float(d.get("flow_mu") or 0.0),
            flow_queue_ahead=float(d.get("flow_queue_ahead") or 0.0),
            flow_queue_cum=float(d.get("flow_queue_cum") or 0.0),
            # [2026-10-09 ping-pong]
            pp_rest_until=float(d.get("pp_rest_until") or 0.0),
            pp_front0_bid=float(d.get("pp_front0_bid") or 0.0),
            pp_front0_ask=float(d.get("pp_front0_ask") or 0.0),
            pp_ahead_bid=float(d.get("pp_ahead_bid") or 0.0),
            pp_ahead_ask=float(d.get("pp_ahead_ask") or 0.0),
            pp_cum_bid=float(d.get("pp_cum_bid") or 0.0),
            pp_cum_ask=float(d.get("pp_cum_ask") or 0.0),
            pp_entry_ctx=dict(d.get("pp_entry_ctx") or {}),
        )


def update_sigma(state: SymbolState, rel_spread: float, *,
                 window: int = 20) -> float:
    """用滚动相对价差算波动归一（与 F59 回放同口径）。

    sigma_norm = 窗口均值 / 基准值 − 1，下限 0。
    基准取窗口填满时的均值——**不依赖任何跨时段的价格水平**，
    因此断流/重启后不会算出荒谬值。
    """
    if rel_spread is None or rel_spread <= 0:
        return 0.0
    state.spread_hist.append(float(rel_spread))
    if len(state.spread_hist) > window:
        state.spread_hist = state.spread_hist[-window:]
    if state.spread_baseline <= 0 and len(state.spread_hist) >= window:
        state.spread_baseline = sum(state.spread_hist) / len(state.spread_hist)
    if state.spread_baseline <= 0:
        return 0.0
    avg = sum(state.spread_hist) / len(state.spread_hist)
    return max(0.0, avg / state.spread_baseline - 1.0)


def _resolve_pending_markouts(state: "SymbolState", *, mid: float,
                              now_ts: float, horizon_sec: float) -> None:
    """[h624] 到期 pending → markout_samples。horizon<=0 不解析。"""
    hz = float(horizon_sec or 0.0)
    if hz <= 0 or mid <= 0:
        return
    from backend.services.market_maker.markout import markout_bp as _mk_bp

    pending = list(getattr(state, "pending_markouts", None) or [])
    if not pending:
        return
    keep: List[Dict[str, float]] = []
    samples = list(getattr(state, "markout_samples", None) or [])
    for p in pending:
        try:
            ts0 = float(p.get("ts") or 0.0)
            if ts0 <= 0 or (now_ts - ts0) < hz:
                keep.append(p)
                continue
            mb = _mk_bp(side=str(p.get("side") or ""),
                        fill_px=float(p.get("fill_px") or 0.0),
                        mid_later=float(mid))
            samples.append({
                "markout_bp": float(mb),
                "capture_bp": float(p.get("capture_bp") or 0.0),
                "notional": float(p.get("notional") or 0.0),
                "ts": float(now_ts),
            })
        except Exception:  # noqa: BLE001
            continue
    state.pending_markouts = keep[-40:]
    state.markout_samples = samples[-80:]


def _enqueue_markout_sample(state: "SymbolState", *, side: str, fill_px: float,
                            capture_bp: float, notional: float,
                            now_ts: float, horizon_sec: float) -> None:
    """[h624] maker 成交入待解析队列（探针开启时）。"""
    if float(horizon_sec or 0.0) <= 0:
        return
    if float(fill_px or 0.0) <= 0 or float(notional or 0.0) <= 0:
        return
    q = list(getattr(state, "pending_markouts", None) or [])
    q.append({
        "ts": float(now_ts),
        "side": str(side or ""),
        "fill_px": float(fill_px),
        "capture_bp": float(capture_bp or 0.0),
        "notional": float(notional),
    })
    state.pending_markouts = q[-40:]


# [h741 2026-10-03] 模块级 venue 过滤计数(plan_tick 内使用;runner.report 读取)
_VENUE_FILTER_SKIPS = [0]
# [h781 2026-10-03 用户指令"继续"] **单币持仓名义硬上限(焊死在代码里)**:
# 持仓名义 ≤ 2.0 × 基础腿名义(基础腿 = compound_ratio×equity ≈ $39)。
# 用户现场证据:NEAR 仓位累积到 $189(单边上限 0.75× 被裁决误回滚后),
# 超时一刀 −35bp = −$0.67,吃掉 ~60 笔 $9-49 的小赢 ⇒ "赚小亏大"的仓位算术根因。
# **非参数、非环境变量、不随启动方式变化** —— 任何自动裁决/回滚都改不掉它。
_HARD_POS_MAX_MULT = 2.0


def symbol_markout_snapshot(state: "SymbolState", *, min_n: int = 10) -> Dict[str, float]:
    """[h624] 单币滚动 markout 快照（心跳/闸门共用）。"""
    from backend.services.market_maker.markout import rolling_markout_stats

    st = rolling_markout_stats(
        list(getattr(state, "markout_samples", None) or []),
        min_n=max(1, int(min_n or 1)),
    )
    return {
        "n": float(st.get("n") or 0.0),
        "m30": float(st.get("markout_bp") or 0.0),
        "cap": float(st.get("capture_bp") or 0.0),
        "notional": float(st.get("notional") or 0.0),
        "pending": float(len(getattr(state, "pending_markouts", None) or [])),
    }


@dataclass
class PlannedFill:
    """本 tick 判定成交的一腿。"""

    symbol: str
    side: str
    qty: float
    px: float
    mid: float
    ts: float
    is_flatten: bool = False
    # [h754 A1 2026-10-03] 严格成交审计输入(判定时的精确口径,写入账本 meta)
    judge_ts: float = 0.0
    win_lo_ms: int = 0
    win_hi_ms: int = 0
    edge_bp: float = 0.0
    spread_usd: float = 0.0
    price_usd: float = 0.0
    fee_usd: float = 0.0
    # [F279 2026-09-16] 本腿所属的**库存周期**（来自 InventoryBook.apply_fill）。
    # 用于写入 lane_ledger.position_id，使开仓腿与平仓腿可以配对。
    position_id: str = ""
    # ── [F257 2026-09-20] 成交判定依据（用于事后验证成交价是否真实存在）─────────
    # 现场问题（H28）：在流动性充足的币上，**47.7% 的引擎成交，其记录的成交价
    # 在 ±20s / ±2bp 窗口内的真实逐笔成交里找不到对应**（偏差中位 5.26bp、max 28.6bp）。
    #
    # 机制：`core.fill_side` 用**15 秒桶内的最低价**判定
    #     `hit_buy = seg_taker_sell > 0 and seg_low < quote_bid`
    # 而成交价**记作我们的挂单价**。于是"桶内极值顺带穿过挂单价"与
    # "真有成交发生在我们的价位上"被混为一谈 —— 但两者是不同的经济事件：
    # 前者意味着我们记了一个**当时市场里不存在的价**。
    #
    # 要事后区分它们，必须把**判定依据本身**落盘：桶的 low/high、我们的挂单价、
    # 以及"桶内是否真有成交落在挂单价的容差内"。没有这些字段，
    # 任何回放都无法回答"这笔成交是真的吗"。
    seg_low: float = 0.0
    seg_high: float = 0.0
    # 桶内是否真有成交落在 `quote_px ± px_hit_tol_bp` 内（由判定方给出；None=未知）
    px_exact_hit: Optional[bool] = None
    # [h402 2026-09-27] #13 挂单时刻：本腿所判定的那张挂单的挂出时刻（epoch s）。
    # 加仓腿 = state.quote_ts（judge_lag_buckets=0 时即真实挂单时刻）；
    # 强平腿（止损/超时/孤儿）= 0（非挂单驱动，无需归因）。
    quote_ts: float = 0.0
    # 平仓腿才有：成交价对成交价的往返盈亏（bp），不经过中间价拆分。
    # 开仓腿留空。中间价把价差和价格盈亏拆开时会互相抵消，单看价差会把开仓记成赚。
    rt_bp: Optional[float] = None
    rt_entry_px: float = 0.0
    hold_sec_true: float = 0.0
    # [2026-10-09 ping-pong 卫生] 进场腿：**挂单时刻**相对买一/卖一的偏移（bp）。
    # 买侧 = (px − 买一)/mid，卖侧 = (px − 卖一)/mid；构造上恒 ≤ 0。
    # >0（买）/ <0（卖）才说明真的插进了价差——用它替代事后 qpos 口径
    # （qpos 会把"成交后盘口穿过我们"误报成插价差）。
    pp_arm_rel_bp: Optional[float] = None

    @property
    def net_usd(self) -> float:
        return self.spread_usd + self.price_usd + self.fee_usd

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol, "side": self.side, "qty": round(self.qty, 10),
            "px": self.px, "mid": self.mid, "ts": self.ts,
            "is_flatten": self.is_flatten, "edge_bp": round(self.edge_bp, 4),
            "spread_usd": round(self.spread_usd, 6),
            "price_usd": round(self.price_usd, 6),
            "fee_usd": round(self.fee_usd, 6),
            "net_usd": round(self.net_usd, 6),
            "position_id": self.position_id,
            "quote_ts": round(self.quote_ts, 3),
        }


@dataclass
class TickDecision:
    symbol: str
    action: str = "pause"           # quote / flatten / pause
    bid: float = 0.0
    ask: float = 0.0
    # ── [2026-10-09 重复来回做市 ping-pong] resting 挂单的数量与减仓标记 ──
    # 旧路径不填（保持 0/False）⇒ `_live_tick_fills` 行为与之前逐字一致。
    # ping-pong 双侧 resting 单需要它们：实盘桥从 dec 直接取数量同步，
    # 而不是只能从本拍成交腿（fills）推导。
    bid_qty: float = 0.0
    ask_qty: float = 0.0
    bid_reduce: bool = False
    ask_reduce: bool = False
    w_bid_bp: float = 0.0
    w_ask_bp: float = 0.0
    mid: float = 0.0
    sigma_norm: float = 0.0
    fills: List[PlannedFill] = field(default_factory=list)
    skip: str = ""
    skip_side: str = ""
    vol_bp: float = 0.0            # 当前已实现波动（bp，F71b）
    lane_pause: str = ""           # [§82/P5-A] 车道级暂停原因（空=未暂停）
    # [F205 2026-09-15] 报价分支（normal/frozen）与该分支的基准半宽 —— 观测用。
    # 目的：让"模型参数 → 实盘行为"可核对（F189 的失败正是因为模型与实盘
    # 在冻结档上的占比不同 ✗，而此前这个量完全不可见）。
    quote_mode: str = ""
    base_bp: float = 0.0
    # [F278 2026-09-16] 跳过原因的细节（如 below_step 的具体步长/最小量）——观测用
    skip_detail: str = ""
    # [F338 2026-09-22] 本 tick 被 `max_leg_notional_mult` 截断的**加仓腿数**。
    # 观测用：这是"逆选择放大器"是否在工作的唯一读数（0 = 未触发/未启用）。
    leg_capped: int = 0
    # ── [F340 2026-09-22] 强平出口的**真实**归因 ──────────────────────────────
    # 缺陷现场（实测，14 天 1,089 条 flatten 腿）：
    #   **1,071 条（98.3%）的 `exit_reason` / `exit_action` 两个键都不存在**，
    #   只剩 18 条有归因，且在任何时间窗里都是同样那 18 条。
    # 根因：F335 用 `dec.skip` 当出口原因，而三条 taker 强平分支
    #   （①′ `stop_loss` 902 行 / ①″ `take_profit` 963 行 / ② `timeout` 1018 行）
    #   **都不设置 `dec.skip`** ⇒ `exit_reason` 恒为空串。
    # 更糟的是 `dec.skip` 可能残留同一 tick 早先闸门的名字
    #   （`vol_pause` / `trend_up` / `ofi_toxic_*`）⇒ **归因是错的**，
    #   我此前据此得出"trend_up/trend_down/ofi 是主要出口"的结论**不成立**。
    #
    # ⇒ 每条强平路径必须**显式命名自己**。这个字段是唯一可信的出口读数。
    exit_path: str = ""
    # [h804 2026-10-04 用户"建立体系"] 本 tick 的形态标签(R1~R5),观测用
    regime: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol, "action": self.action,
            "bid": self.bid, "ask": self.ask,
            "w_bid_bp": round(self.w_bid_bp, 4), "w_ask_bp": round(self.w_ask_bp, 4),
            "mid": self.mid, "sigma_norm": round(self.sigma_norm, 4),
            "vol_bp": round(self.vol_bp, 3),
            "fills": [f.to_dict() for f in self.fills],
            "skip": self.skip, "skip_side": self.skip_side,
            "skip_detail": self.skip_detail,
            "lane_pause": self.lane_pause,
            "quote_mode": self.quote_mode, "base_bp": round(self.base_bp, 4),
            "leg_capped": self.leg_capped,
            # [F340] 真实的强平出口（空 = 本 tick 无 taker 强平）
            "exit_path": self.exit_path,
        }


def lane_limits_enforce_enabled() -> bool:
    """[§82/P5-A] 车道级风控闸门开关 `MM_LANE_LIMITS_ENFORCE`（默认 **false**）。

    默认关 ⇒ 与 P5 接线前**逐字一致**（影子证据基线不被静默改写）；
    打开后 `plan_tick` 才执行 toxic_streak / 日亏 / 波动 / 权益的车道级暂停。
    一键回滚 = 置 false（或删键）+ 重启。
    """
    try:
        from backend.config.settings import MM_LANE_LIMITS_ENFORCE as _v
        return bool(_v)
    except Exception:
        return str(os.environ.get("MM_LANE_LIMITS_ENFORCE", "false")).strip().lower() in (
            "1", "true", "yes", "on",
        )


def lane_day_pnl_usd(lane_id: str, now_ts: Optional[float] = None) -> float:
    """当日（北京自然日）车道已实现净额（美元）——日亏闸 `daily_loss_stop_pct` 的输入。

    [F249 2026-09-16 口径澄清] 实际分桶由 `lane_ledger.daily_series` 完成，其
    `date_trunc('day', ts)` 用数据库会话时区（Asia/Shanghai），即**北京自然日**，
    不是 UTC 日（旧 docstring 误写 UTC）。日亏闸因此在北京时间 00:00 归零，
    而不是 08:00。

    [F345 2026-09-23 修复] 旧实现用 **UTC 日期**去匹配桶标签：北京 00:00–08:00
    期间 UTC 仍是"昨天"，会**命中昨天的桶**，把昨日净额当"今日"喂给日亏闸
    （实测 00:33 闸门因昨日的 −74.87 误锁 55 分钟，冻结整个车道）。旧 docstring
    声称"未命中走 rows[-1] 恰是当前北京日"是错的：fallback 取的是**昨天**的桶。
    修复：改用**北京日**匹配桶标签；今日无桶（今天还没成交）⇒ 返回 0.0。
    结论：闸门输入恒为「当前北京自然日净额」，与前端迷你曲线（同日桶）口径一致。

    [F98 2026-09-14] **按统计时代（当前配置代）裁剪**：口径 = `ts >= max(当日 0 点,
    stats_since)`。原因：实测今日车道净额 −$18.77，其中 **−$13.73 来自
    「修复前」的旧配置时期**（06:00 −$9.80 / 10:00 −$4.21 的漏单与幻影成交时期），
    时代内只有 −$5.04。若按自然日不裁剪，日亏闸会**用已被修掉的缺陷造成的亏损**
    去停掉现在的车道（阈值 −$30 已被占用 63%），既不公平也难排查；而且与前端展示
    （时代口径）自相矛盾。fail-open：读不到账本返回 0.0（不因读数失败停车道）。
    """
    try:
        from backend.services import lane_ledger, lane_registry

        _since = None
        try:
            _since = (lane_registry.get_lane(lane_id) or {}).get("meta", {}).get("stats_since")
        except Exception:
            _since = None
        rows = lane_ledger.daily_series(lane_id=lane_id, days=2, since=_since) or []
        if not rows:
            return 0.0
        # [F345] 桶标签是北京自然日；匹配也必须用北京日（UTC 日会命中昨天的桶）。
        _today_bj = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
        for r in reversed(rows):
            d = r.get("day") or r.get("date") or r.get("ts")
            if d is None:
                continue
            try:
                dd = d.date() if hasattr(d, "date") else datetime.fromisoformat(str(d)).date()
            except Exception:
                continue
            if dd == _today_bj:
                return float(r.get("net_usd") or 0.0)
        # 今日还没有桶（今天还没有成交）⇒ 今日净额 = 0，绝不能用昨天的 rows[-1]。
        return 0.0
    except Exception as exc:  # pragma: no cover - 账本不可用时 fail-open
        logger.warning("[F60] 日亏闸读数失败(fail-open，按 0 处理): %s", exc)
        return 0.0


def check_fee_guard(maker_fee_bp: float, *, max_bp: float = MAX_MAKER_FEE_BP) -> Tuple[bool, str]:
    """费率闸门：Aster 的 0% maker 可能是活动价，超过阈值必须停车道。

    F52/F53 实测：挂宽 5bp 时，maker 涨到 2bp 边际就接近 0，涨到 4bp 直接转负。
    """
    if maker_fee_bp < 0:
        return True, "rebate"
    if maker_fee_bp > max_bp:
        return False, f"maker_fee_too_high({maker_fee_bp:.2f}bp>{max_bp:.2f}bp)"
    return True, ""


def seg_window(prev_label_ms: int, snap_ms: int, quote_ts: float = 0.0,
               bucket_ms: int = SEG_BUCKET_MS) -> Tuple[int, int, int]:
    """[F92 2026-09-14] 区间成交桶的取数窗口（纯函数，可单测）。

    背景（重大漏单根因）：成交桶 `market_trades_aggregated` 按 15s 网格存储、
    时间戳 = **桶起点**，而桶在「桶结束 + ~1s」才落库（实测探针）。此前下界用
    `last_tick_ts`（**墙钟**，落在桶中间）⇒ `timestamp > 下界` 会系统性排除
    「标签 ≤ 下界 < 标签+15s」的那个桶——实测实盘 8.9 笔/小时 vs 同窗口回放
    101 笔/小时（**只吃到 9%**）。

    正解：窗口两端都锚在**快照桶标签**（与回放同一时间网格），而不是墙钟：
      - 下界 `lo` = 该币已消费到的最大桶标签（无则回退一个桶）；
      - 上界 `hi` = 当前快照标签（只消费到「本次决策所依据的快照」那一桶为止，
        保证不会把**未来**的成交算进本次判定）；
      - 成交判定门槛 `qts_ms` = 挂单时间：桶 `[L, L+15)` 只有在其**结束时刻晚于
        挂单时刻**时才可能打到我们的挂单（`timestamp + 15s > quote_ts`），否则
        只消费不判定——这正是 F89a 幻影成交的同类护栏。

    返回 `(lo, hi, qts_ms)`：SQL 用 `timestamp > lo AND timestamp <= hi`，并用
    `FILTER (WHERE timestamp + bucket_ms > qts_ms)` 只为「挂单存续期内」的桶聚合
    成交明细；水位推进到**实际读到的最大标签**（读不到就停住，下一个 tick 补收，
    绝不漏桶）。
    """
    lo = int(prev_label_ms or 0)
    _snap = int(snap_ms or 0)
    if lo <= 0:
        # 冷启动：回退一个桶，覆盖「上一 tick 所在桶」（含挂单后可能成交的尾巴）
        lo = max(0, _snap - int(bucket_ms))
    hi = max(lo, _snap)
    qts_ms = int(float(quote_ts or 0.0) * 1000.0)
    return lo, hi, qts_ms


def skip_key(reason: str) -> str:
    """[F95] 把 skip 原因归一成统计键（去掉括号里的参数值）。

    现场需要回答的问题：「到底哪道闸门在吃我的成交？」——回放侧有 skip 计数，
    实盘侧此前完全看不到（只能手工打一次 tick 看瞬时值）。归一化后按进程累计，
    前端「链路健康」直接展示分布。
    """
    s = str(reason or "").strip()
    if not s:
        return ""
    return s.split("(")[0].split(":")[0].strip()


def pending_contrib(state: "SymbolState", mark: float, leg: float
                    ) -> Tuple[float, float, float]:
    """[F94c 2026-09-14] 单个币的在挂腿对风险预留的贡献 `(up, down, gross)`（USD）。

    - **加仓腿**（空仓的买、空头的卖）：足额 `leg` —— 成交会新增一条腿。
    - **减仓腿**（多头的卖、空头的买）：`min(leg, |现仓|)` —— 平仓最多只能减掉
      现有仓位，不可能新增敞口。
    - `gross` 只计加仓腿（减仓只会减小总敞口）。

    为什么必须区分：此前一律按足额 `leg` 计 ⇒ 只要场上有几个减仓腿在挂，
    其余币的加仓侧就被"最坏情形"封死——实测重启后 **5 分钟 0 成交**、
    `skip=net_exposure`。减仓腿按现仓计之后，预留与实际风险一致。
    """
    q = float(getattr(state, "qty", 0.0) or 0.0)
    pos_nt = abs(q) * float(mark or 0.0)
    up = down = gross = 0.0
    if float(getattr(state, "quote_bid", 0.0) or 0.0) > 0:
        if q < -1e-12:                       # 空头买回 = 减仓
            up += min(leg, pos_nt)
        else:                                # 空仓/多头买入 = 加仓
            up += leg
            gross += leg
    if float(getattr(state, "quote_ask", 0.0) or 0.0) > 0:
        if q > 1e-12:                        # 多头卖出 = 减仓
            down += min(leg, pos_nt)
        else:                                # 空仓/空头卖出 = 加仓
            down += leg
            gross += leg
    return up, down, gross


def check_data_freshness(snapshot_ts_ms: int, now_ts: float,
                         max_age_sec: float = MAX_DATA_AGE_SEC) -> Tuple[bool, float, str]:
    """盘口数据新鲜度检查。返回 (fresh, age_sec, reason)。

    陈旧数据必须拒单：用 22 天前的价格挂单不是「做市」，是「送钱」。
    """
    if not snapshot_ts_ms:
        return False, -1.0, "no_snapshot"
    age = float(now_ts) - int(snapshot_ts_ms) / 1000.0
    if max_age_sec > 0 and age > max_age_sec:
        return False, age, f"stale_data({age/60:.1f}min>{max_age_sec/60:.1f}min)"
    return True, age, ""


def plan_orphan_exit(*, state: SymbolState, market_row: Optional[Dict[str, Any]],
                     now_ts: float, taker_fee_bp: float = 0.0
                     ) -> Tuple[List[PlannedFill], str, float]:
    """[F90 2026-09-14] 孤儿持仓退出计划（**纯函数**：无 DB、无全局状态）。

    「孤儿持仓」= 币种已被移出宇宙，但运行态里仍有仓位。此前 tick 主循环只遍历
    在营币种 ⇒ 该仓位不进净敞口上限、永不退出、盈亏永不实现（静默漏仓）。
    这里给出退出计划：用当前盘口的对手价（mid ∓ 半价差）平掉全仓，按 taker 计费。

    ⚠️ 行情陈旧返回**空成交**（`orphan_stale_data(...)`）：绝不用旧价成交——
    F89a 的 4 笔幻影成交（净敞口 -$739 > $300 上限）就是这么来的。
    返回 `(fills, skip, mid)`；`fills` 非空 ⇒ 调用方负责落账 + 清空运行态。
    """
    from backend.services.market_maker.core import InventoryBook, Position

    if abs(float(getattr(state, "qty", 0.0) or 0.0)) <= 1e-12:
        return [], "orphan_flat", 0.0
    if not market_row:
        return [], "orphan_no_market", 0.0
    fresh, _age, why = check_data_freshness(market_row.get("ts_ms"), now_ts)
    mid = float(market_row.get("mid") or 0.0)
    if not fresh or mid <= 0:
        return [], f"orphan_{why or 'no_mid'}", 0.0
    qty = abs(float(state.qty))
    half_spread = max(0.0, float(market_row.get("half_spread") or 0.0))
    side = "sell" if state.qty > 0 else "buy"
    px = (mid - half_spread) if side == "sell" else (mid + half_spread)
    book = InventoryBook()
    book.positions[state.symbol] = Position(
        qty=state.qty, avg_px=state.avg_px, avg_mid=state.avg_mid,
        opened_ts=state.opened_ts, last_ts=state.last_ts,
    )
    fd = book.apply_fill(symbol=state.symbol, side=side, qty=qty, fill_px=px,
                         mid_px=mid, fee_rate=abs(taker_fee_bp) / 1e4, now_ts=now_ts)
    return [PlannedFill(
        symbol=state.symbol, side=side, qty=qty, px=px, mid=mid, ts=now_ts,
        is_flatten=True,
        spread_usd=float(fd.get("spread_usd") or 0.0),
        price_usd=float(fd.get("price_usd") or 0.0),
        fee_usd=float(fd.get("fee_usd") or 0.0),
        position_id=str(fd.get("position_id") or ""),
    )], "orphan_flatten", mid


def _detect_pattern(mid_hist: List[float], p1_th: float = 15.0) -> str:
    """[h403 2026-09-27] 开仓时刻形态标记（研究口径固定阈值，与闸门参数无关）。

    返回 "P45"（P4 双触突破 / P5 挤压突破）、"P1"（|300s 趋势|≥p1_th 回调区）
    或 ""。检测与 h363 闸门的触发判定同式但**不看 OFI**（标记的是形态上下文，
    OFI 对齐属于闸门职责）。
    """
    if len(mid_hist) >= 9:
        w4 = list(mid_hist[-9:])
        hi4, lo4 = max(w4), min(w4)
        if hi4 > 0:
            near_hi = sum(1 for m in w4 if m >= hi4 * (1 - 5e-6))
            near_lo = sum(1 for m in w4 if m <= lo4 * (1 + 5e-6))
            if (near_hi >= 2 and w4[-1] >= hi4 * (1 - 5e-6)) or \
                    (near_lo >= 2 and w4[-1] <= lo4 * (1 + 5e-6)):
                return "P45"
    if len(mid_hist) >= 40:
        h5 = list(mid_hist)

        def _seg_vol(w):
            return sum(abs((w[t + 1] - w[t]) / w[t]) * 1e4
                       for t in range(len(w) - 1) if w[t] > 0)

        v_now = _seg_vol(h5[-5:])
        if v_now > 0:
            vs = [_seg_vol(h5[k:k + 5]) for k in range(len(h5) - 4)]
            vs = [v for v in vs if v > 0]
            if vs:
                q30 = sorted(vs)[int(len(vs) * 0.3)]
                from backend.services.market_maker.core import trend_move_bp as _tmb
                if v_now < q30 and abs(_tmb(h5, 1)) >= 2.0:
                    return "P45"
    if len(mid_hist) >= 2:
        from backend.services.market_maker.core import trend_move_bp as _tmb2
        if abs(_tmb2(mid_hist, 20)) >= float(p1_th):
            return "P1"
    return ""


def _effective_hold_sec(state, limits) -> float:
    """[h403] 分形态持有期上界：P1/P45 标记 → 对应参数；否则/参数为 0 → 默认。"""
    tag = str(getattr(state, "pattern_tag", "") or "")
    base = float(getattr(limits, "max_one_side_seconds", 0.0) or 0.0)
    if tag == "P1":
        v = float(getattr(limits, "p1_hold_sec", 0.0) or 0.0)
        return v if v > 0 else base
    if tag == "P45":
        v = float(getattr(limits, "p45_hold_sec", 0.0) or 0.0)
        return v if v > 0 else base
    return base


EXIT_PROBE_PATH = Path(
    os.getenv("MM_EXIT_PROBE_PATH")
    or (Path(__file__).resolve().parents[3] / "logs" / "mm_exit_probe.jsonl"))
_EXIT_PROBE_LAST: dict = {}
# [h490 自检] 真实事件里这两种"出场决策点"的年龄**必然有界**：
#   · giveup（ofi_flatten maker-only）要求 age > 0.5 × max_one_side_seconds（≥45s）；
#   · hardcap 由 `timeout_hard_taker_sec`（≈300s）触发，且触发即平仓 ⇒ age ≲ 300s+tick。
# 而**回放/测试**会用历史 `opened_ts` 配真实时钟 ⇒ age 动辄上万秒。
# 事故（本探针第一版）：`pytest -k "mm or lane"` 里的回放用例把 10 行假事件
# 写进了**生产的** `logs/mm_exit_probe.jsonl`（age≈42000s）⇒ 加年龄上限即可自动隔离，
# 且语义上完全正确。关闭开关：`MM_EXIT_PROBE_DISABLE=1`（回放/测试建议设）。
EXIT_PROBE_MAX_AGE_SEC = float(os.getenv("MM_EXIT_PROBE_MAX_AGE_SEC", "600"))
# [h834] 探索单配额台账(每币每小时最多 N 笔极小名义探索;模块级,进程内)
_PROBE_LOG: dict = {}
# [h848] 自愈节流时间戳(见 save_states:全账本扫描,不能每 tick 跑)
_HEAL_LAST_TS: float = 0.0
# [h845 用户"调高速度"] JSON 文件缓存(按 mtime):
# tick 里 33 个币各读一次 77KB 情况表 ⇒ 实测 73ms/tick(占决策阶段 85%)。
# 文件只在生产者运行时变 ⇒ mtime 不变就复用同一份已解析对象。
_JSON_CACHE: dict = {}


def load_json_cached(path: str):
    """按 mtime 缓存已解析的 JSON。文件缺失/坏 JSON 返回 None(不抛)。

    ⚠️ 必须**显式 import json**:本模块的 json 是在各函数内部以 `import json as _js`
    导入的,模块级没有 `json` 名字 ⇒ 直接写 `json.loads` 会 NameError,而被下面
    的裸 except 吞成"永远返回 None"(实测:门因此全判 stale,当场停交易)。
    """
    import json as _json_mod
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return None
    hit = _JSON_CACHE.get(path)
    if hit is not None and hit[0] == mt:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as fh:
            doc = _json_mod.loads(fh.read())
    except Exception as _e:
        logger.warning("[h845] 读 %s 失败(本 tick 按无文件处理): %s", path, _e)
        return None
    _JSON_CACHE[path] = (mt, doc)
    return doc


def persist_exit_probe(*, symbol: str, state, mid: float, kind: str, ofi: float = 0.0,
                       now_ts: float | None = None, min_gap_sec: float = 20.0) -> None:
    """[h490 2026-09-29] **出场决策点探针**（只落盘、零行为变化）。

    为什么需要（这是一个**测量仪器缺口**，不是策略改动）：
      · 账本 `lane_ledger.position_id` 形如 `mm:BNB:1`，**会被反复复用**
        （实测近 3h：213 行只有 12 个不同 id），`position_id_state` 恒为 'paired'
        ⇒ **往返无法可靠配平**，凡是"按往返"的统计（持仓时长、放弃时点、
        宽限期反事实）都建在流沙上（h489 就因此得出过 2 笔 1467s/2772s 的
        "硬顶出场"，与 300s 硬顶自相矛盾）；
      · 而"被动优先要不要改成 N 秒后认输"这个问题，只差**决策点那一刻的持仓状态**：
        开仓均价、当时中价、浮盈亏、年龄、OFI。

    本探针在**放弃（ofi_flatten maker-only 触发）**与**硬顶强平**两条分支落盘
    这些字段，事后即可用真实中价路径做干净的宽限期反事实，无需账本配平。
    限流：同一 symbol+kind 至少间隔 `min_gap_sec`（避免每 tick 刷屏）。
    **绝不抛异常**（与 lane_ledger.record_fill / _persist_fill_basis 同原则）。
    """
    try:
        import json as _json
        import time as _time

        if str(os.getenv("MM_EXIT_PROBE_DISABLE", "0")).strip().lower() in (
                "1", "true", "yes", "on"):
            return
        key = (symbol, kind)
        # 事件时间优先（与 `plan_tick` 的 now_ts 同源）；缺省才退回墙钟。
        # 用事件时间才能让回放/测试的年龄自洽（否则历史时间戳配墙钟 ⇒ 假的大年龄）。
        now = float(now_ts) if now_ts else _time.time()
        if now - float(_EXIT_PROBE_LAST.get(key) or 0.0) < min_gap_sec:
            return
        opened = float(getattr(state, "opened_ts", 0.0) or 0.0)
        age = (now - opened) if opened > 0 else 0.0
        # 年龄合理性：真实事件必然 ≤ 数百秒；回放/测试的历史时间戳会被挡在这里
        if not (0.0 < age <= EXIT_PROBE_MAX_AGE_SEC):
            return
        _EXIT_PROBE_LAST[key] = now
        qty = float(getattr(state, "qty", 0.0) or 0.0)
        avg = float(getattr(state, "avg_mid", 0.0) or getattr(state, "avg_px", 0.0) or 0.0)
        upnl = ((float(mid) - avg) / avg * 1e4) if (avg > 0 and qty > 0) else (
            (avg - float(mid)) / avg * 1e4 if avg > 0 else 0.0)
        rec = {"ts": round(now, 3), "iso": _time.strftime("%Y-%m-%dT%H:%M:%S"),
               "symbol": symbol, "kind": kind, "qty": round(qty, 10),
               "avg_mid": avg, "mid": float(mid),
               "unrealized_bp": round(upnl, 3), "age_s": round(age, 1),
               "ofi": round(float(ofi or 0.0), 4),
               "pattern_tag": str(getattr(state, "pattern_tag", "") or "")}
        with EXIT_PROBE_PATH.open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _force_exit_allowed(state, now_ts: float, limits) -> Tuple[bool, str]:
    """[F258] 当前是否允许**引擎主动发起**强制出库（时长窗口）。

    包一层只是为了把 `limits` 里的两个字段取出来并统一容错，
    真正的判定在 `core.hold_window_ok`（纯函数，有独立测试）。

    `min_hold_seconds` / `max_one_side_seconds` 任一为 0 或负 ⇒ 该侧关闭。
    """
    from backend.services.market_maker.core import hold_window_ok

    return hold_window_ok(
        opened_ts=float(getattr(state, "opened_ts", 0.0) or 0.0),
        now_ts=float(now_ts),
        min_hold_sec=float(getattr(limits, "min_hold_seconds", 0.0) or 0.0),
        # 上界这一侧在 ② 里已实现（`> max_one_side_seconds` 才触发），
        # 这里**不重复拦上界**，否则会出现"上界到了但被下限逻辑拒绝"的矛盾。
        max_hold_sec=0.0,
    )


def apply_env_param_overrides(params):
    """[F280] 把环境变量里显式给出的宽度参数**覆盖**到从注册表读出的 params 上。

    为什么需要（这是一个真实且危险的口径陷阱）：
    `QuoteParams` 的默认值是 `float(os.getenv("MM_SPREAD_MULT", "0.0"))` 这类写法，
    但注册表热加载是 `QuoteParams(**{k: v for k, v in stored.items() ...})` ——
    **一旦某个 key 出现在 `meta.params` 里，它就以显式实参传入，
    dataclass 的 `os.getenv` 默认值根本不会求值** ✗。
    于是"我改了 .env 但行为没变"，正是 F189 那一类事故
    （模型以为挂 12bp、实盘实际只挂 5.5bp ⇒ 拿着"改了但没生效"的配置承担真实敞口）。

    这里让**环境变量显式优先**：只有 env 真的设了值才覆盖，
    没设就保留注册表里的值 ⇒ 不改变任何既有部署的行为 ✓。

    ══ [F298 2026-09-21] **两者不一致时必须大声告警** ══
    实测踩到：把 `.env` 的 `MM_SPREAD_MULT` 由 0.9 改成 0.5 后，正在跑的 worker
    心跳**连续 100 秒仍是 0.9**。原因就是本函数读 `os.getenv` —— 那是**进程启动时**
    由 `load_dotenv` 灌进 `os.environ` 的**冻结值**，改文件对已运行进程无效。
    后果：`scripts/mm_apply_*.py` 那族脚本改完会打印"≤60s 内热采用"，
    **而对白名单键这句话是错的** ⇒ 静默偏差（F189/F280/F287/F292 同源）。

    为什么不在本函数里"让注册表赢"：那会**改变既有部署的行为**
    （生产 `.env` 与注册表目前对 `w_base_bp`/`k_inv` 本就不一致），
    风险大于收益。折中：**保持 env 优先，但把分叉显式化** ——
    注册表值与 env 值不同时打 `warning`，并指明"须重启才生效"。
    这样"改了没生效"从**静默**变成**日志里一眼可见**，且零行为变更。

    回滚：删掉调用点即回到纯注册表口径。

    ══ [F327 2026-09-21 · P2] **注册表权威模式**（`MM_REGISTRY_AUTHORITATIVE=1`）══
    P2 要让 LLM 调整参数**不重启就生效**，而这 7 个白名单键是拦路虎：
    写了注册表也不生效（env 赢），改 `.env` 又要重启。
    实测 `k_inv` 注册表 0.5 / 实盘 1.0 —— **注册表是假的**，
    任何"读注册表做归因"的下游都会拿到错误前提。

    本模式把**注册表变成唯一运行时权威**：
      · 不再 `object.__setattr__` 覆盖（热采用立刻生效，无需重启）
      · **但分叉检查与告警一条不少**（此时 env 只作对照，不再执行）
      · 默认 `0`（关闭）⇒ 既有部署行为零变更，8 项 F298 契约测试原样通过

    为什么保留告警而不是直接删掉 env 逻辑：
    env 值仍在 `.env` 里存在，若注册表被谁改回旧值，这条 warning 是唯一
    能立刻看出"`.env` 说 A、实盘跑 B"的地方 ⇒ 删掉它等于重新制造一个静默陷阱。
    """
    import os as _os

    if params is None:
        return params
    # 默认关闭；只有显式设成 1/true/yes/on 才切到注册表权威
    _authoritative = (
        str(_os.getenv("MM_REGISTRY_AUTHORITATIVE", "0")).strip().lower()
        in ("1", "true", "yes", "on")
    )
    for _key, _env in (
        ("spread_mult", "MM_SPREAD_MULT"),
        ("spread_mult_reduce", "MM_SPREAD_MULT_REDUCE"),
        ("min_edge_frac", "MM_MIN_EDGE_FRAC"),
        ("spread_cross_margin", "MM_SPREAD_CROSS_MARGIN"),
        ("w_base_bp", "MM_W_BASE_BP"),
        ("min_width_bp", "MM_MIN_WIDTH_BP"),
        ("k_inv", "MM_K_INV"),
    ):
        _raw = _os.getenv(_env)
        if _raw is None or str(_raw).strip() == "":
            continue
        try:
            _env_val = float(_raw)
        except Exception:
            logger.warning("[F280] %s=%r 不是数值，忽略", _env, _raw)
            continue
        # [F327] 注册表权威模式下 env 不再执行，只作对照
        _eff_val = getattr(params, _key, None) if _authoritative else _env_val
        # [F298] 分叉告警：注册表（运行时意图）与 env（进程启动快照）不一致
        try:
            _reg_val = getattr(params, _key, None)
            if _reg_val is not None and abs(float(_reg_val) - float(_env_val)) > 1e-9:
                if _authoritative:
                    logger.warning(
                        "[F327] 参数分叉：%s 注册表=%s 而 env(%s)=%s ⇒ "
                        "**按注册表生效（注册表权威模式）**；`.env` 里这个值是过时的，"
                        "请清理或同步，否则它会误导下一次归因。",
                        _key, _reg_val, _env, _env_val)
                else:
                    logger.warning(
                        "[F298] 参数分叉：%s 注册表=%s 但 env(%s)=%s ⇒ **按 env 生效**。"
                        " 改了 .env 文件对已运行进程无效（os.getenv 是启动时冻结值），"
                        "必须**重启 worker** 才会变；或改注册表并去掉该 env 键。",
                        _key, _reg_val, _env, _env_val)
        except Exception:
            pass
        if _authoritative:
            # 不覆盖：注册表值就是实盘值 ⇒ 热采用即刻生效
            continue
        try:
            object.__setattr__(params, _key, _eff_val)
        except Exception:
            # frozen dataclass 上 object.__setattr__ 一定成功；真失败也不能让 tick 挂掉
            logger.warning("[F280] 覆盖 %s 失败（忽略）", _key, exc_info=True)
    return params


# [R201 2026-09-29] **闸门探针（模块级）**：`skip_counts` 只记 `dec.skip`，而 `dec.skip`
# 先到先得 ⇒ 被更早闸门抢先的闸门会"在动却不可见" ✗（实测：`ofi_confirm_threshold` 带
# `and allow_*` 守卫、被趋势闸抢先 ⇒ 3500 次拦截里 0 次，看起来像没生效）；而且
# `skip_counts` 发布时**只留前 12** ⇒ 长尾闸门同样看不见 ✗。
# ⇒ 凡新增闸门，除 `dec.skip` 外**必须**在这里留一个与遮蔽无关的计数 ✓。
# 为什么放模块级：`plan_tick` 是**模块级函数**（没有 `self`）✗；`ShadowRunner.status()` 读它发布 ✓。
GATE_PROBES: Dict[str, int] = {}


def book_slot_decision(
    *,
    spread_bp: Optional[float],
    min_bp: float,
    now_ts: float,
    until: float,
    is_open: bool,
    dwell_sec: float,
) -> Tuple[bool, float, bool]:
    """[h625] 深度选档。返回 (是否允许新开, 停留截止, 本段是否开着)。

    价差变窄 ⇒ 立刻停新开，并保持 book_slot_dwell_sec 秒。
    这段时间内即使价差偶尔变宽，也不立刻恢复（避免 BTC 这种一跳就锁 3 分钟开仓）。
    价差够宽且不在关闭停留里 ⇒ 允许新开，但不把「开着」锁死。
    没有新鲜深度、且不在关闭停留里 ⇒ 允许。
    """
    if float(min_bp or 0.0) <= 0:
        return True, 0.0, True
    now = float(now_ts)
    until_f = float(until or 0.0)
    dwell = max(30.0, float(dwell_sec or 180.0))
    closed_hold = until_f > now and not bool(is_open)
    if spread_bp is not None and float(spread_bp) < float(min_bp):
        return False, now + dwell, False
    if closed_hold:
        return False, until_f, False
    if spread_bp is None:
        return True, until_f, True
    return True, now, True


# [h711] 趋势闸滞后的模块级状态(plan_tick 是模块函数,无 self):
# symbol → (last_trend_ts, blocked)。仅纸面/实盘 worker 各一个进程,键冲突无虞。
_TREND_BLOCK_HOLD: Dict[str, tuple] = {}

# [h714 阶段0a] 影子反事实:skip 原因 → 被拦侧(skip 名里的 sell/buy 指**毒性方向**,
# 不是被拦侧:ofi_toxic_sell=卖压 ⇒ 拦买;trend_h697_buy=拦买)。
_SHADOW_BLOCK_SIDES = {
    "ofi_toxic_sell": "buy", "ofi_toxic_buy": "sell",
    "trend_h697_buy": "buy", "trend_h697_sell": "sell", "trend_h697_both": "both",
    "trend_add_block": "both",
    "direction_buy": "buy", "direction_sell": "sell",
    "vpin_high": "both", "sudden_move": "both", "jump_pause": "both",
    "mp_skew_buy": "buy", "mp_skew_sell": "sell",
    "ofi_require": "both", "markout_halt": "both",
}


def plan_tick(
    *,
    state: SymbolState,
    mid: float,
    seg_low: float,
    seg_high: float,
    seg_taker_sell: float,
    seg_taker_buy: float,
    now_ts: float,
    params=None,
    limits=None,
    equity: float = 5000.0,
    fill_notional: float = FILL_NOTIONAL,
    taker_fee_bp: float = TAKER_FEE_BP,
    maker_fee_bp: float = 0.0,
    half_spread: float = 0.0,
    sigma_norm: float = 0.0,
    inv_ratio_hint: Optional[float] = None,
    book=None,
    marks: Optional[Dict[str, float]] = None,
    day_pnl_usd: float = 0.0,
    # [F86] 上一桶主动流失衡 OFI∈[-1,1]（+1=全是主动买）；用于流向毒性闸
    ofi: float = 0.0,
    # [F272 2026-09-16 · E2] microprice 相对中价的偏离（bp，+ = 买压/价大概率上行）。
    # 由调用方用盘口量加权算出（microprice = (bid·askSz + ask·bidSz)/(bidSz+askSz)）；
    # 与 `limits.mp_block_bp` 配合封锁会被逆向选择的那一侧。
    mp_skew_bp: float = 0.0,
    # [h899 2026-10-07] 顶档盘口失衡 OBI ∈ [−1,1]（趋势概率模型特征）；
    # 由调用方从深度快照算出（plan_tick 是模块级函数,没有 self,必须透传）。
    obi_top: float = 0.0,
    # [F274 2026-09-16 · E2'] 连续同向主动流的**带符号桶数**（+N = 连续 N 桶买压，
    # −N = 连续 N 桶卖压）；与 `limits.flow_persist_pause` 配合做「持续性单边流站开」。
    flow_streak: float = 0.0,
    # [F94] 本 tick 其余币**在挂同向腿**的名义（最坏情形风险预留）：调用方维护，
    # 形如 {"up": 买单在挂名义, "down": 卖单在挂名义}，跨币累加。
    pending: Optional[Dict[str, float]] = None,
    # [F98 2026-09-14] 车道级闸门开关：None = 读环境变量（生产默认行为），
    # True/False = 显式覆盖（回放/实验用）。此前闸门调用被 env 硬门控，
    # 回放**永远测不到** daily_loss_stop_pct / toxic_streak ⇒ 配置里的这两个
    # 数字从未被任何实验覆盖过（本次修复后才能真正 A/B）。
    lane_limits_enforce: Optional[bool] = None,
    # [h749 2026-10-03] 车道暂停**覆盖**(由 runner 的冷却式熔断状态机写入):
    # 非空时跳过 plan_tick 内部的车道级判据,直接采用该原因暂停(或 None=正常)。
    # 用户要求:熔断必须有时限并自动复开,且要有提示。
    lane_pause_override: Optional[str] = None,
    # [F176 2026-09-15] **被判定挂单**（可选）：`(bid, ask, mid)`。
    # 为什么需要：F171 把成交桶改成"按成交自身时间戳分桶、**桶结束后才落库**" ✓
    # （修复了覆盖率 47.5%→93.3% ✓），代价是桶会比 tick **晚 15~30s** 才可见 ✗。
    # 若仍用"当前状态里的挂单"去判定，晚到的桶要么被 `quote_ts` 过滤器排除、
    # 要么被算到**挂单存续期之外** ✗（实测实盘空分片率 47%→**85.7%** ✗✗、
    # 成交从 184/h 掉到 27.5/h ✗）。正解是**延迟一档判定**：拿"L 个桶之前那张单"
    # 去判"它当时真正存续的那段分片" ✓。默认 None = 沿用状态里的挂单（回放不变 ✓）。
    judged_quote: Optional[Tuple[float, float, float]] = None,
    # [F347] 学习工件（lane_registry.meta.ai_model，由 h300 发布；缺省 None=不启用模型模式）
    ai_model: Optional[Dict[str, Any]] = None,
    # [h354] P2 形态输入：60s 成交 VWAP（vwap_revert_bp>0 时由 fetch_market 提供）
    vwap60: float = 0.0,
    # [h752 C1a] 资金费率(分数,如 0.0001=0.01%):资金偏斜软闸的输入。
    # 由 tick 循环从 perp_funding 缓存(300s)传入;0=无数据/闸关闭。
    funding_rate: float = 0.0,
    # [h759] 实时淘汰快路:该币命中亏损淘汰判据 ⇒ 只减不加(见闸门段说明)。
    decay_blocked: bool = False,
    # [h754 A1 2026-10-03] 严格成交模型审计输入(判定时的精确窗口):
    #   judge_ts    = 被判定挂单的挂出时刻(quote_ts)
    #   win_lo_ms/win_hi_ms = 该次判定真正使用的逐笔窗口
    # 落进账本 meta ⇒ 离线审计可用**精确**窗口复算,不再靠 quote_ts 反推
    # (反推在挂单被刷新时会误判"幻影",实测 45% 的假幻影由此而来)。
    judge_ts: float = 0.0,
    win_lo_ms: int = 0,
    win_hi_ms: int = 0,
    # [h405] #12 分形态×分币种启停表：{"BTC": ["P45"]} = BTC 只在 P4/P5 形态
    # 上下文挂单（空仓时生效；有持仓豁免——减仓侧必须存活 F76）。
    # None/空 = 旧行为。由 lane meta.pattern_matrix 热采用（tick 循环传入）。
    pattern_matrix: Optional[Dict[str, list]] = None,
    # [h621 2026-09-29] **tick 级成交判定**（MM_SEG_SOURCE=tick 时由 tick 循环传入）。
    # `tick_fill=True` ⇒ 成交的**发生率与数量**都改用逐笔口径：
    #   · 发生率：要求 vol_at_price>0（真实逐笔里确有对手方主动成交打到我们价位，
    #     消灭"桶 low 穿越但逐笔无对应"的 9.9% 幻影）；
    #   · 数量：可吃量 = vol_at_price（价位上的真实量），不再用"整桶总量"外推
    #     （实测名义只覆盖 82%、p90 腿量 2.32× 真实可吃量）。
    # False（默认）⇒ 三个参数全部无效，行为与改动前**逐字一致** ✓。
    tick_fill: bool = False,
    vol_le_bid: float = 0.0,
    vol_ge_ask: float = 0.0,
    bid_qty: float = 0.0,
    ask_qty: float = 0.0,
    # [h625] 新鲜 20 档的买卖价差（bp）。None = 没有新鲜深度，选档闸不干预。
    depth_spread_bp: Optional[float] = None,
    # [h626] 方向卡点名的加仓边（buy/sell）。None = 这个币两边都能加。
    block_add_side: Optional[str] = None,
    # [h657] Q 速控加仓腿量乘子(1.0/0.5/0.0)。只乘加仓腿(与 per_symbol_size_mult
    # 同点位),减仓腿精确平仓不受影响(F91)。1.0 = 旧行为逐字一致。
    q_size_mult: float = 1.0,
    # [h692] 分侧加仓量乘子 {"bid": m, "ask": m}(仅加仓腿;与 q_size_mult 相乘)。
    # None = 两侧都不倾斜。来源:方向分数 D(fusion>=3),bid=1+kD, ask=1−kD。
    add_size_mult: Optional[Dict[str, float]] = None,
    book_stale: bool = False,
    flow_exit_only: bool = False,
) -> Tuple[TickDecision, Dict[str, Any]]:
    """一个 tick 的纯决策：成交判定 → 超时平仓 → 重挂新单。

    Args:
        state: 该币当前运行态（含上次挂单价与库存）。
        seg_*: 自 `state.quote_ts` 以来的区间成交明细（低/高/主动买/主动卖量）。
        half_spread: 当前盘口半价差（用于超时平仓打对手价；0 表示未知，退化为中价）。
        book: 可选的跨币 `InventoryBook`（用于净敞口约束）；None 时只用单币约束。
        inv_ratio_hint: 由调用方按账户级限额算出的库存偏离度（覆盖单币计算）。

    Returns:
        (decision, meta) —— meta 含 `inventory_book`（若传入则原样返回）与统计。
    """
    from backend.services.market_maker.core import (
        InventoryBook, LaneRiskLimits, Position, QuoteParams, adds_blocked_by_trend,
        check_side_allowed, compute_quote, fill_side, lane_pause_reason,
        leg_qty_compliant, should_stop_loss, sudden_move_hit,
        symbol_lookup, trend_blocked_side, trend_move_bp, vol_regime_blocked,
    )

    params = params or QuoteParams()
    limits = limits or LaneRiskLimits()
    # [h736 2026-10-03] 软模式标志**提前到这里定义**(fill 段与闸门段都要用):
    # mode>=3 时趋势闸不封侧、只缩量;fill 段在**记账前**按此缩量(与 book 同源),
    # 闸门段按此恢复被双边封停的报价。h735 曾在闸门段缩放 dec.fills ⇒
    # book/账本 qty 分叉 ⇒ reload_states 清成本价(ARB 开仓价 0 的根因)。
    _tsm = float(getattr(limits, "trend_soft_size_mult", 0.0) or 0.0)
    _tbm = float(getattr(limits, "trend_block_mode", 0.0) or 0.0)
    _soft_mode = bool(_tbm >= 3.0 and _tsm > 0)
    # [h395 2026-09-27] #16③ 止损后降腿量：该币 30min 内发生过强制止损离场
    # ⇒ 加仓腿名义 ×(1−post_stop_decay)。0=关=旧行为；减仓腿不受影响。
    _psd = float(getattr(limits, "post_stop_decay", 0.0) or 0.0)
    _psd_win = 1800.0
    _psd_active = (_psd > 0.0 and float(state.last_stop_ts or 0.0) > 0.0
                   and (float(now_ts) - float(state.last_stop_ts)) <= _psd_win)
    _eff_notional = (float(fill_notional or 0.0) * (1.0 - _psd)) \
        if _psd_active else float(fill_notional or 0.0)
    _pend = pending if pending is not None else {}
    dec = TickDecision(symbol=state.symbol, mid=float(mid or 0.0), sigma_norm=float(sigma_norm or 0.0))
    if not mid or mid <= 0:
        dec.skip = "no_mid"
        return dec, {}

    # [h624] 先解析到期 markout（用本 tick mid），供后续闸门与心跳
    _mk_hz = float(getattr(limits, "markout_horizon_sec", 0.0) or 0.0)
    if _mk_hz > 0:
        _resolve_pending_markouts(state, mid=float(mid),
                                  now_ts=float(now_ts), horizon_sec=_mk_hz)

    local_book = book if book is not None else InventoryBook()
    if book is None and abs(state.qty) > 1e-12:
        # 无外部库存时用本币库存初始化（保证单币逻辑可独立测试）
        local_book.positions[state.symbol] = Position(
            qty=state.qty, avg_px=state.avg_px, avg_mid=state.avg_mid,
            opened_ts=state.opened_ts, last_ts=state.last_ts,
        )

    # 主动流：单边挂单。异常时跳过这一秒，禁止掉回双边做市。
    _afm = float(getattr(limits, "active_flow_mode", 0.0) or 0.0)
    if _afm > 0:
        try:
            from backend.services.market_maker.active_flow import active_flow_decision
            from backend.services.market_maker.core import realized_vol_bp
            from backend.services.market_maker.flow_rules import (
                PROBE_EQUITY_FRAC, gate_decision, load_learn_params, situation_decision,
            )
            from backend.services.market_maker.regime import classify_regime
            import json as _js
            import os as _os
            _trend_af = 0.0
            try:
                _trend_af = float(trend_move_bp(list(state.mid_hist or []), 20) or 0.0)
            except Exception:
                pass
            _hs_af = max(0.0, float(half_spread or 0.0))
            # 半价差缺失时买一卖一都无效，禁止用中价冒充。
            _bb_af = (mid - _hs_af) if (_hs_af > 0 and mid > _hs_af) else 0.0
            _ba_af = (mid + _hs_af) if _hs_af > 0 else 0.0
            _vol_af = 0.0
            try:
                _vol_af = float(realized_vol_bp(list(state.mid_hist or []), 20) or 0.0)
            except Exception:
                pass
            _regime = classify_regime(
                ofi=float(ofi or 0.0), trend300_bp=_trend_af,
                sigma_norm=float(sigma_norm or 0.0))
            dec.regime = _regime
            _root = _os.path.dirname(_os.path.dirname(_os.path.dirname(
                _os.path.dirname(_os.path.abspath(__file__)))))
            _learn = load_learn_params(__import__("pathlib").Path(_root))
            _gate = {"allow": False, "side": None, "mu": None, "max_hold_sec": None}
            _probe = 0.0
            _skip_why = ""
            # [2026-10-09 进化重挂] ping-pong 桶级情形表（做/歇口径）。
            # 在这里先给 None，加载失败也保持 None ⇒ pingpong fail-open 照挂。
            _ppsit = None
            try:
                _sp = _os.path.join(_root, "data", "flow_situation_last.json")
                _sit = None
                if _os.path.exists(_sp):
                    _sit = load_json_cached(_sp)   # [h845] 按 mtime 缓存,不再每币解析
                _ppsp = _os.path.join(_root, "data", "pp_situation_last.json")
                if _os.path.exists(_ppsp):
                    _ppsit = load_json_cached(_ppsp)
                if isinstance(_sit, dict):
                    _spread_bp = ((2.0 * _hs_af / float(mid)) * 1e4) if float(mid) > 0 else 0.0
                    _gate = situation_decision(
                        _sit, str(state.symbol), float(now_ts),
                        float(bid_qty or 0.0) * _bb_af,
                        float(ask_qty or 0.0) * _ba_af,
                        _spread_bp,
                        margin_bp=float(_learn.get("entry_margin_bp") or 1.0),
                    )
                    _probe = max(0.0, float(equity or 0.0) * PROBE_EQUITY_FRAC)
                    # 模拟盘继续开仓做测试，亏了也不停。没有已证实的档时，按资金流方向挂单进场。
                    # 要停掉测试开仓：MM_FLOW_EXPLORE_ALL=0
                    _explore = str(_os.getenv("MM_FLOW_EXPLORE_ALL", "1")).strip() not in (
                        "0", "false", "False", "")
                    if not _gate.get("allow") and _explore:
                        # [h872] **负桶否决**:情况表的该(排队×价差)档已有样本且
                        # 样本外期望非正 ⇒ 探索也不进(这些档正是止损聚集地)。
                        # 只有"无证据"的档才允许探索(探索的任务就是去填那些空档)。
                        if str(_gate.get("reason") or "") == "oos_not_positive":
                            _gate = {"allow": False, "side": None, "mu": None,
                                     "max_hold_sec": None,
                                     "reason": "explore_negative_bucket"}
                            _skip_why = "explore_negative_bucket"
                        elif True:
                            # [h861] **前置跳空过滤**:第一级筛选器(每 5 分钟)已经把
                            # "24h 振幅 >15%" 的币列进 data/vol_top20.json 的 gap_excluded。
                            # 实测这些币是止损黑洞:SI −129.6bp/腿、BTW −44.5bp/腿
                            # (R4 分类器只看瞬时 σ,接不住它们)⇒ 探索也不碰。
                            # [h873 09:5x] 但 gap_excluded **漏了 LYN**(振幅 3528%、
                            # gap_prone=True 却不在名单里)⇒ 3 小时 42 条止损 −86.5bp/腿
                            # 最差 −536.6bp、合计 −29.5U —— 全场亏损的头号来源。
                            # [h874 10:5x] 分级:硬名单(gap_excluded)完全不碰;
                            # **gap_prone 软名单 = 按探索单小名义做**(fills 从 87 掉到 8/h
                            # 太死了 —— 用户要求满速跑,数据从交易本身攒;小名义把
                            # 深跳止损的成本压到 1/5,同时保持这些币的样本不断)。
                            _gex_hard = set()
                            _gex_soft = set()
                            # [h904] plan_tick 只被 worker 对本车道宇宙的币调用 ⇒
                            # 到这里来的币已是"名单管理者挑过的"。
                            # 硬排除名单把它们全挡 = 交易停摆的根因(00:38 实测)。
                            # 修法:硬名单在 plan_tick 一律降级软档(探索名义,
                            # 风险由 5% 上限压住);真正想挡的币由宇宙雷达在入场前过滤。
                            try:
                                _vt = load_json_cached(
                                    _os.path.join(_root, "data", "vol_top20.json")) or {}
                                _gex_hard = {str(x).upper() for x in (_vt.get("gap_excluded") or [])}
                                for _it in (_vt.get("detail") or []):
                                    if bool(_it.get("gap_prone")):
                                        _gex_soft.add(str(_it.get("symbol") or "").upper())
                            except Exception:
                                _gex_hard = set()
                                _gex_soft = set()
                            # [h904] 硬/软名单在 plan_tick 统一软档(见上注释);
                            # 这里的 _lane_universe 引用已移除(plan_tick 无 self)。
                            if str(state.symbol).upper() in _gex_hard \
                                    or str(state.symbol).upper() in _gex_soft:
                                # [h889 桥 15:58] gap_prone 币**硬排除**(回退 h874 的软档):
                                # [h896 用户"开仓腿数不正常"] h889 的硬排除把宇宙砍掉
                                # 一大块(实测 gap_excluded 22.8 万次 = 最大闸门),
                                # 而成交活跃的币多数是 gap_prone ⇒ 开仓腿数塌到 1 单/h。
                                # 回退为**软档**:gap_prone 币按探索单名义(equity×5%)做,
                                # 崩盘损失被名义上限压住(LYN 崩盘在 $15~500 名义下可控),
                                # 用户要的开仓量由它们恢复。
                                _gate = {"allow": True, "side": None, "mu": None,
                                         "max_hold_sec": None, "reason": "explore_gap_soft",
                                         "probe": True, "mean_y": 0.0, "n_eff": 0.0}
                                _probe = max(0.0, float(equity or 0.0) * PROBE_EQUITY_FRAC)
                                _skip_why = "gap_soft_probe"
                            elif _regime in ("R4", "R5"):
                                _gate = {"allow": False, "side": None, "mu": None,
                                         "max_hold_sec": None, "reason": f"regime_{_regime}"}
                                _skip_why = f"regime_{_regime}"
                            else:
                                # 短期方向用现成的因子分数，不用我们自己的历史成交。
                                # 微价、资金流、5 分钟趋势（顺着）、15 分钟趋势（反过来）。
                                # 分数不够偏，这一拍没有方向，不开。下一拍再算。
                                from backend.services.market_maker.dirscore import direction_score
                                _tr9 = 0.0
                                try:
                                    _tr9 = float(trend_move_bp(
                                        list(state.mid_hist or []), 60) or 0.0)
                                except Exception:
                                    _tr9 = 0.0
                                _d = direction_score(
                                    float(mp_skew_bp or 0.0), float(ofi or 0.0),
                                    float(_trend_af or 0.0), _tr9)
                                _min_d = float(_os.getenv("MM_FLOW_DIR_MIN", "0.25") or 0.25)
                                # ── [h894 趋势概率方向] 用户定方向:高频 = 趋势概率驱动的
                                # 快进快出(非做市)。模型新鲜 ⇒ 方向/门槛由 P(up)/P(dn)
                                # 接管;模型缺失/过期/异常 ⇒ 回退 direction_score(行为不变)。
                                # 开关 MM_TREND_PROB_ENABLED=0 全关。_d 照算(影子记录不污染)。
                                _tp_side = None
                                _tp_edge = 0.0
                                _tp_mag_small = False
                                try:
                                    if _os.getenv("MM_TREND_PROB_ENABLED", "1") != "0":
                                        from backend.services.market_maker.trend_prob import (  # noqa: E501
                                            predict_side_edge)
                                        # [2026-10-08 用户规则1] 放宽判定阈值 0.03→0.01。
                                        # 实测 margin=0.03 时市场稍乱就判不出方向 ⇒ 不进场 ⇒ 没交易。
                                        # 0.01 = 概率略偏就出手,配合止损/8分钟强平兜底。
                                        _tp_margin = float(_os.getenv(
                                            "MM_TREND_PROB_MARGIN", "0.01") or 0.01)
                                        _t120 = float(trend_move_bp(
                                            list(state.mid_hist or []), 120) or 0.0)
                                        _tp_feats = {
                                            "mp_skew_bp": float(mp_skew_bp or 0.0),
                                            "ofi": float(ofi or 0.0),
                                            "obi_top": float(obi_top or 0.0),
                                            "trend_20s": float(_trend_af or 0.0),
                                            "trend_60s": float(_tr9),
                                            "trend_120s": _t120,
                                            "accel": float(_trend_af or 0.0)
                                                     - float(_tr9),
                                            "vol_20s": float(_vol_af or 0.0)}
                                        _tp_r = predict_side_edge(_root, _tp_feats,
                                                                  margin=_tp_margin)
                                        if _tp_r is not None:
                                            _tp_side, _tp_edge = _tp_r
                                            # [h902 幅度门] 分析层加强:方向对了还要看
                                            # "预计动多大"。|E[fwd]| < 最小腿幅 ⇒
                                            # 小动静不值得打(赢单才 +1bp 的病根) ⇒ 跳过。
                                            from backend.services.market_maker.trend_prob import (  # noqa: E501
                                                load_model as _tp_lm2,
                                                predict_magnitude as _tp_mag)
                                            _mag = _tp_mag(_tp_lm2(_root), _tp_feats)
                                            _min_leg = float(_os.getenv(
                                                "MM_MIN_LEG_BP", "2.0") or 2.0)
                                            if _tp_side and _mag is not None \
                                                    and abs(_mag) < _min_leg:
                                                _tp_side = ""
                                                _tp_mag_small = True
                                except Exception as _tp_exc:
                                    _tp_side = None
                                    # [h899] 概率门异常必须可见(本项目反复踩静默 except 的坑)
                                    logger.warning("[h894] 趋势概率判定异常(回退因子分): %s",
                                                   _tp_exc)
                                # [h899c 双确认加成] A/B 实测(OOS 前向 30s):
                                #   概率与因子分**同向** ⇒ +0.93bp/信号(最强);
                                #   仅概率 ⇒ +0.44bp;仅因子分 ⇒ +0.44bp。
                                # ⇒ 同向时把 edge ×1.5(信心放大 ⇒ 仓位更大);
                                #   不强制同向才交易(仅概率仍 +0.44bp,砍掉会少太多成交)。
                                if _tp_side:
                                    _ds_side = ("buy" if _d > _min_d
                                                else ("sell" if _d < -_min_d else None))
                                    if _ds_side is not None and _ds_side == _tp_side:
                                        _tp_edge = float(_tp_edge) * 1.5
                                # [h895] 概率信心存给仓位放大段(同 tick 同币;
                                # 带时间戳,过期(>5s)自动失效 ⇒ 不会用陈旧的信心)
                                try:
                                    # [2026-10-09 修 bug] plan_tick 是模块级函数,没有 self。
                                    # _prob_edge 用模块级缓存(本来 getattr(self,...) 永远
                                    # 走 except ⇒ 概率信心从没存上 ⇒ 仓位放大从没生效)。
                                    global _PROB_EDGE_CACHE
                                    try:
                                        _PROB_EDGE_CACHE
                                    except NameError:
                                        _PROB_EDGE_CACHE = {}
                                    _PROB_EDGE_CACHE[state.symbol] = (time.time(),
                                                                      float(_tp_edge))
                                except Exception:
                                    pass
                                # [2026-10-08 用户规则] **顺市场趋势做单**:跌就做空,涨就做多。
                                # 不再让概率/因子在跌市里判 buy 去抄底(实测:跌市挂买单没人吃,
                                # 卡死不成交)。方向 = 价格趋势符号(趋势够强才做,弱市不做)。
                                _trend_dir = 0
                                if abs(_tr9) >= 2.0:   # 60s 趋势 ≥2bp 才算有方向
                                    _trend_dir = 1 if _tr9 > 0 else -1
                                if _tp_side is not None:
                                    _dir_side = _tp_side or None
                                    if _tp_side:
                                        _dir_why = "prob"
                                    else:
                                        _dir_why = ("mag_small" if _tp_mag_small
                                                    else "prob_weak")
                                else:
                                    _dir_side = (("buy" if _d > 0 else "sell")
                                                 if abs(_d) >= _min_d else None)
                                    _dir_why = "no_direction"
                                # 顺趋势覆盖:有明确价格趋势时,方向强制跟趋势(不被概率/因子反着来)
                                if _trend_dir != 0:
                                    _trend_side = "buy" if _trend_dir > 0 else "sell"
                                    if _dir_side != _trend_side:
                                        _dir_side = _trend_side
                                        _dir_why = "trend_follow"
                                # [h894 桥 05:58] **活性闸**:凌晨死市里流信号没有预测力
                                # (实测 00:00~06:00 每小时 −20~−55U,白天/晚间则 +15~+105U)。
                                # 用 300s 波动做代理:极低波动 = 没有可赚的行程,进场只交逆选费。
                                _dead = float(_vol_af or 0.0) < 0.5
                                if _dead:
                                    _gate = {"allow": False, "side": None, "mu": None,
                                             "max_hold_sec": None, "reason": "dead_market"}
                                    _skip_why = "dead_market"
                                elif _dir_side is None:
                                    _gate = {"allow": False, "side": None, "mu": None,
                                             "max_hold_sec": None, "reason": _dir_why}
                                    _skip_why = _dir_why
                                else:
                                    _side = _dir_side
                                    # [h899 A] 影子策略反哺:每拍记录影子侧;晋升后接管方向
                                    try:
                                        from backend.services.evolution import (
                                            shadow_feedback as _sf)
                                        _obs_s = [float(mp_skew_bp or 0.0),
                                                  float(ofi or 0.0), 0.0,
                                                  float(_tr9 or 0.0),
                                                  float(_trend_af or 0.0),
                                                  0.0, 0.0, 0.0, 0.0,
                                                  float(_vol_af or 0.0),
                                                  0.0, 0.0, 0.0, 0.0, 0.0]
                                        _sf.record_shadow(str(state.symbol),
                                                          float(now_ts), _obs_s,
                                                          _side, float(mid))
                                        if _sf.is_promoted() and \
                                                str(_os.getenv("MM_SHADOW_POLICY", "1")
                                                    or "1").strip().lower() \
                                                not in ("0", "false", "no", "off"):
                                            _sd = _sf.shadow_side(_obs_s)
                                            if _sd:
                                                _side = _sd
                                    except Exception:
                                        pass
                                    # [h877 底层诊断] 时限 90/120/180 → **45/45/60**
                                    if _regime == "R1":
                                        _hold, _why = 45.0, "explore_s1"
                                    elif _regime == "R3":
                                        _hold, _why = 45.0, "explore_s4"
                                    else:
                                        _hold, _why = 60.0, "explore_s3"
                                    # [h888 桥 05:58] **慢漂移否决**:近 2h BTC +28bp、
                                    # ETH +34bp 慢涨,引擎做空 37 腿 −19bp/腿(净 −34.9U);
                                    # 300s 趋势(±1~2bp)够不到 8bp 否决线 ⇒ 慢漂移的
                                    # grind 全靠逆侧买单。10 分钟趋势 ±4bp 否决逆侧,
                                    # S3(均值反转)豁免 —— 它本来就是逆偏离博回归。
                                    _tr10 = 0.0
                                    try:
                                        _tr10 = float(trend_move_bp(
                                            list(state.mid_hist or []), 120) or 0.0)
                                    except Exception:
                                        _tr10 = 0.0
                                    # [h893c 三层先验·模拟仓直连] 否决阈值由统一进化内核的
                                    # **regime 读数**给(layers_state.json 的 layers.m5 节):
                                    # 长线有方向 ⇒ 逆侧更严;中线无方向 ⇒ 放宽。
                                    # 旧桥(h892)错把 long/mid 层的 train_ep_mean(训练 reward
                                    # 均值 = 策略赚不赚钱)当方向读数 ⇒ 语义全错,已修。
                                    # fail-closed:文件缺失/过期(>3h)/异常 ⇒ 基线 4.0bp。
                                    _veto_bp = 4.0
                                    try:
                                        _ls = load_json_cached(_os.path.join(
                                            _root, "data",
                                            "evolution_layers_state.json")) or {}
                                        _m5 = (_ls.get("layers") or {}).get("m5") or {}
                                        _age = time.time() - float(_ls.get("ts") or 0.0)
                                        if _m5 and 0.0 <= _age < 3 * 3600:
                                            # 必须走 m5_bridge(纯标准库):
                                            # 本进程是 .runtime 解释器,没有 numpy,
                                            # import unified_learner 会静默失败。
                                            from backend.services.evolution.m5_bridge import (  # noqa: E501
                                                m5_veto_bp)
                                            _veto_bp = m5_veto_bp(
                                                _m5.get("long_regime_bp"),
                                                _m5.get("mid_prior_bp"),
                                                base_bp=4.0)
                                    except Exception:
                                        _veto_bp = 4.0
                                    _against10 = (abs(_tr10) >= _veto_bp
                                                  and ((_side == "buy" and _tr10 < 0)
                                                       or (_side == "sell" and _tr10 > 0))
                                                  and _why != "explore_s3")
                                    # [2026-10-08 修 bug] 方向用订单流(领先)判,不用价格动量(滞后)。
                                    # 实锤:OFI 强买(+0.15~+0.26)但价格小跌时,旧逻辑把买单误判
                                    # 成「逆势」拦掉 ⇒ 资金先进场、价格后跟的最佳进场点被错过。
                                    # 修法:OFI 与价格动量**同向**才算逆势;OFI 反向(资金已转向)
                                    # 时不拦——资金是领先信号,价格会跟上。
                                    try:
                                        _ofi_now = float(getattr(state, "ofi", 0.0) or 0.0)
                                    except Exception:
                                        _ofi_now = 0.0
                                    if _against10 and abs(_ofi_now) >= 0.10:
                                        # 价格跌但资金在大买 ⇒ 不拦买单(资金领先)
                                        if _side == "buy" and _ofi_now > 0:
                                            _against10 = False
                                        # 价格涨但资金在大卖 ⇒ 不拦卖单
                                        elif _side == "sell" and _ofi_now < 0:
                                            _against10 = False
                                    # [h893 桥 04:58] **震荡否决**:近 10 分钟区间/净位移 > 3
                                    # = 原地大幅来回 ⇒ 流信号在震荡里没有预测力
                                    # (实测 1h 多空两侧都亏:买 −16.6bp、卖 −10.5bp,
                                    #  合计 −46.6U —— 纯震荡税)。区间太小(<3bp)不算。
                                    _chop = False
                                    try:
                                        _hist = list(state.mid_hist or [])
                                        if len(_hist) >= 10 and float(mid) > 0:
                                            _rng = float(max(_hist) - min(_hist))
                                            _net = abs(float(_hist[-1]) - float(_hist[0]))
                                            if _rng > float(mid) * 6e-4 \
                                                    and _rng > 3.0 * max(_net, 1e-12):
                                                _chop = True
                                    except Exception:
                                        _chop = False
                                    # [h905] 原独立 chop 闸门已并入下方方向否决合并闸
                                    # ── [整顿轮·T45/T46 2026-10-06] 本闸门改为可配 ──
                                    #
                                    # **T45 的检验已撤回（方法论缺陷）**：
                                    # 我曾用"实际成交的 192 条进场腿"按逆势/顺势分组，
                                    # 得出 t=−0.41"不显著 ⇒ 闸门无依据"。**这是错的**：
                                    # 闸门的作用是**阻止逆势进场**，而账本里只有**成交**的腿
                                    # ⇒ **我手里没有"被拦下所以没成交"的反事实数据**，
                                    # 因此那次检验**无法否证本闸门**。
                                    #
                                    # ⇒ 本闸门的原始依据（h888"做空 37 腿 −19bp/腿"）
                                    #   **至今未被检验**（既未证实也未否证）。
                                    #
                                    # 开关 `MM_DRIFT_VETO`（默认 1 = 原行为）仅为 A/B 预留；
                                    # **在拿到真实反事实数据之前，默认不动**。
                                    #
                                    # 回滚：`MM_DRIFT_VETO=1`（默认）⇒ 逐字恢复原行为。
                                    try:
                                        _dv_on = str(
                                            _os.getenv("MM_DRIFT_VETO", "1") or "1"
                                        ).strip().lower() not in ("0", "false", "no", "off")
                                    except Exception:  # noqa: BLE001
                                        _dv_on = True
                                    _against10 = bool(_against10) and _dv_on
                                    # [h905 门禁整合] 震荡否决并入方向否决合并闸:
                                    # 一类风险一个口子 —— 逆漂移或震荡,都从这里拦
                                    # (不再有两个独立闸门做概率连乘)。
                                    _gate_why = "drift_veto"
                                    if _chop and not _against10:
                                        _against10 = True
                                        _gate_why = "chop_veto"
                                    if _against10:
                                        # ── [T46] **否决要留反事实记录** ──────────────
                                        # 为什么必须记：上一轮我用"成交腿"去检验一个
                                        # "决定谁不成交"的机制 ⇒ 逻辑错误（见上）。
                                        # 只有把**被拦下那一刻的现场**记下来，日后才能
                                        # 用当时中价 + 之后走势回答"拦对了还是拦错了"。
                                        # 这是纯遥测（只追加一行 jsonl），不改任何行为。
                                        #
                                        # ⚠️ 去重（T46 第三版 · 最终）：**不在写入端去重**。
                                        #   第一版每拍都写（75 行同币/同趋势/同中价，ts 差 1.2s）
                                        #   ⇒ 1 个决定被记 75 次。
                                        #   第二版想在写入端按 (symbol,side) 去重，**实测失败**：
                                        #   否决条件会在其后的分支里被重新赋值
                                        #   （`if not _gate.get("allow"):` 之后有冷启动探索配额，
                                        #    会把 `_gate` 改成 allow=True 并重设 `_skip_why`），
                                        #   我在 else 里清键的做法被后续流程反复打乱。
                                        #   ⇒ 改为**记录全部原始事件，在分析端按
                                        #   (symbol, side, 连续时间窗) 折叠成"事件"**。
                                        #   既保留审计完整性，又不让统计被 75 倍放大。
                                        try:
                                            import json as _json
                                            import pathlib as _plv
                                            _vp = (_plv.Path(__file__).resolve().parents[3]
                                                   / "logs" / "drift_veto_log.jsonl")
                                            with open(_vp, "a", encoding="utf-8") as _fv:
                                                _fv.write(_json.dumps({
                                                    "ts": float(now_ts),
                                                    "symbol": str(state.symbol),
                                                    "side": str(_side),
                                                    "trend10_bp": round(float(_tr10), 3),
                                                    "mid": float(mid or 0.0),
                                                    "why": str(_why),
                                                    "regime": str(_regime),
                                                }, ensure_ascii=False) + "\n")
                                        except Exception:  # noqa: BLE001
                                            pass
                                        _gate = {"allow": False, "side": None, "mu": None,
                                                 "max_hold_sec": None,
                                                 "reason": _gate_why}
                                        _skip_why = _gate_why
                                    else:
                                        _gate = {"allow": True, "side": _side, "mu": None,
                                                 "max_hold_sec": _hold, "reason": _why,
                                                 "probe": False, "mean_y": _d, "n_eff": 0.0}
                    if not _gate.get("allow"):
                        # 冷启动探索配额(严格门开着时仍然可用):
                        # 每币每小时最多 N 笔**极小名义**探索单,只用来填桶。
                        _pk = str(state.symbol).upper()
                        _nowp = float(now_ts)
                        _hits = [t for t in (_PROBE_LOG.get(_pk) or [])
                                 if _nowp - float(t) < 3600.0]
                        _quota = int(_os.getenv("MM_FLOW_PROBE_PER_HOUR", "0") or 0)
                        if _quota > 0 and len(_hits) < _quota:
                            _g2 = situation_decision(
                                _sit, str(state.symbol), _nowp,
                                float(bid_qty or 0.0) * _bb_af,
                                float(ask_qty or 0.0) * _ba_af,
                                _spread_bp,
                                margin_bp=float(_learn.get("entry_margin_bp") or 1.0),
                                probe_ok=True,
                            )
                            if _g2.get("allow"):
                                _gate = _g2
                                _PROBE_LOG[_pk] = _hits + [_nowp]
                    if not _gate.get("allow"):
                        _skip_why = str(_gate.get("reason") or "situation_not_this_tick")
                else:
                    _gp = _os.path.join(_root, "data", "flow_gate_last.json")
                    if _os.path.exists(_gp):
                        _g = load_json_cached(_gp) or {}
                        _gate = gate_decision(_g, str(state.symbol), float(now_ts))
                        _raw = ((_g.get("gates") or {}).get(str(state.symbol).upper()) or {})
                        if _raw.get("mu") is not None:
                            _gate["mu"] = float(_raw.get("mu") or 0.0)
                        _recent = _raw.get("recent_mean_y")
                        if _recent is not None and float(_recent) <= 0.0:
                            _gate["allow"] = False
                            _gate["reason"] = "recent_mean_nonpositive"
                    if not _gate.get("allow"):
                        _skip_why = str(_gate.get("reason") or "model_gate_no_edge")
            except Exception:
                _gate = {"allow": False, "side": None, "mu": None,
                         "max_hold_sec": None, "reason": "gate_read_error"}
                _skip_why = "gate_read_error"
                # [h905 调试] 把真实异常写进日志(临时,查明后删)
                try:
                    import traceback as _tb
                    with open(_os.path.join(_root, "logs", "gate_read_error.log"),
                              "a", encoding="utf-8") as _fe:
                        _fe.write(f"--- {time.time():.0f} {state.symbol} ---\n")
                        _fe.write(_tb.format_exc() + "\n")
                except Exception:
                    pass
            _hold_af = float(_gate.get("max_hold_sec") or _learn.get("hold_sec") or 90.0)
            _same = 1
            try:
                for _ps in (local_book.positions or {}).values():
                    _q = float(getattr(_ps, "qty", 0.0) or 0.0)
                    if _gate.get("side") == "buy" and _q > 0:
                        _same += 1
                    elif _gate.get("side") == "sell" and _q < 0:
                        _same += 1
            except Exception:
                _same = 1
            # [h870] 本币 24h 振幅(供按币分档止损)—— 自己读文件,不依赖上方作用域
            _amp_af = 0.0
            try:
                _vt2 = load_json_cached(
                    _os.path.join(_root, "data", "vol_top20.json")) or {}
                for _it in (_vt2.get("detail") or []):
                    if str(_it.get("symbol") or "").upper() == str(state.symbol).upper():
                        _amp_af = float(_it.get("range_pct_24h") or 0.0)
                        break
            except Exception:
                _amp_af = 0.0
            # [h879/h880 + 整顿轮·T2] 吃单进场的**成本门槛**（在调用之前算好）：
            # 预期有利移动（bp）必须盖过「吃单进 + 挂单出」的往返成本。
            # 来源：唯一成本真相源 `fee_schedule_service.break_even_move_bp`。
            # 回滚：`MM_TAKER_ENTRY_COST_MULT=0` ⇒ 门槛为 0 ⇒ 逐字恢复旧行为。
            _need_taker_bp = 0.0
            try:
                # [2026-10-09 文档对齐] 吃单进场默认关闭(实测 -17bp/笔 大亏)。
                # 文档:高频只走「挂单进场+挂单离场」唯一正 edge 路径。
                # 吃单只留灾难止损/真深跳(别处),进场一律挂单 0 费。
                # 回滚:MM_TAKER_ENTRY_COST_MULT=0.1 恢复。
                _tcm = float(_os.getenv("MM_TAKER_ENTRY_COST_MULT", "0") or 0.0)
                if _tcm > 0:
                    from backend.services.fee_schedule_service import (
                        break_even_move_bp as _be_bp,
                    )
                    _need_taker_bp = _tcm * float(_be_bp(
                        exchange=DEFAULT_VENUE,
                        entry_is_maker=False,
                        exit_is_maker=True,
                        hold_seconds=max(0.0, min(float(_hold_af or 90.0), 300.0)),
                    ))
            except Exception:
                _need_taker_bp = 0.0
            # [整顿轮·T20] 计算当前总敞口 Σ|仓位×标记价| 与上限比例，交给 active_flow。
            # 与 `core.check_side_allowed` 的总敞口口径一致（用同一 marks / positions）。
            _af_gross_ratio_af = 0.0
            _af_gross_af = 0.0
            try:
                if str(_os.getenv("MM_AF_GROSS_CAP", "1") or "1").strip() not in (
                        "0", "false", "False", "no", "off"):
                    _af_gross_ratio_af = float(
                        getattr(limits, "max_gross_notional_ratio", 0.0) or 0.0)
                    if _af_gross_ratio_af > 0:
                        _mk = locals().get("marks") or {}
                        for _s in set(list(local_book.positions) + [state.symbol]):
                            try:
                                _af_gross_af += abs(float(
                                    local_book.notional(_s, _mk.get(_s, 0.0) or 0.0)))
                            except Exception:
                                continue
            except Exception:
                _af_gross_ratio_af = 0.0
                _af_gross_af = 0.0
            active_flow_decision(
                state=state, mid=float(mid), ofi=float(ofi or 0.0),
                trend_bp=_trend_af, bb=_bb_af, ba=_ba_af,
                now_ts=float(now_ts), fill_notional=0.0,
                taker_fee_bp=TAKER_FEE_BP,
                sl_bp=float(getattr(limits, "stop_loss_bp", 40.0) or 40.0),
                tp_bp=float(getattr(limits, "take_profit_bp", 60.0) or 60.0),
                max_hold_sec=_hold_af,
                flow_thresh=float(getattr(limits, "active_flow_thresh", 0.15) or 0.15),
                local_book=local_book, dec=dec, PlannedFill=PlannedFill,
                maker_fee_bp=0.0,
                allow_entry=bool(_gate.get("allow")) and not flow_exit_only,
                model_side=_gate.get("side"),
                model_mu=_gate.get("mu"),
                regime=_regime,
                book_stale=bool(book_stale),
                equity=float(equity or 0.0),
                vol_300s_bp=_vol_af,
                seg_low=float(seg_low or 0.0),
                seg_high=float(seg_high or 0.0),
                seg_sell=float(seg_taker_sell or 0.0),
                seg_buy=float(seg_taker_buy or 0.0),
                same_side_n=_same,
                # [整顿轮·T20 2026-10-05] 把账户级总敞口交给 active_flow ——
                # 本路径（runner.py:1417）在 `check_side_allowed`（runner.py:2479）**之前**
                # 执行，此前完全绕过 gross/symbol/net 三重上限。
                # 实测：$10,252 权益上出现单腿 $68,247（6.7×），
                # 声明的 max_gross_notional_ratio=3.0 从未生效。
                # 回滚：`MM_AF_GROSS_CAP=0` ⇒ 传 0 ⇒ 行为与修复前一致。
                gross_notional_usd=_af_gross_af,
                max_gross_notional_ratio=_af_gross_ratio_af,
                stop_floor_bp=float(_learn.get("disaster_stop_floor_bp") or 15.0),
                # [h870] **按币振幅分档止损**:高振幅币的 2×vol 天然远超 40bp,
                # cap=40 对它们是噪音级(止损率虚高)。振幅 >12% ⇒ 上限放宽到 60。
                stop_cap_bp=(
                    max(float(_learn.get("disaster_stop_cap_bp") or 40.0), 60.0)
                    if (_amp_af > 0.12) else
                    float(_learn.get("disaster_stop_cap_bp") or 40.0)),
                entry_margin_bp=float(_learn.get("entry_margin_bp") or 1.0),
                loss_frac=float(_learn.get("notional_loss_frac") or 0.005),
                roundtrip_root=__import__("pathlib").Path(_root),
                # [h834] 只有探索单才压到极小名义;已证实的桶走正常名义上限
                probe_notional_usd=(_probe if _gate.get("probe") else 0.0),
                # [h852] 探索模式:放行进场但**不污染离场 mu**
                explore_entry=str(_gate.get("reason") or "").startswith("explore_s"),
                bid_qty=float(bid_qty or 0.0),
                ask_qty=float(ask_qty or 0.0),
                vol_at_bid=float(vol_le_bid or 0.0),
                vol_at_ask=float(vol_ge_ask or 0.0),
                skip_reason=_skip_why,
                # ── [整顿轮·T2 2026-10-05] 吃单进场的门槛改为「成本门槛」 ──
                #
                # 旧逻辑（h879→h880）：`|mean_y| >= 0.9` 且价差 ≤8bp 且与 300s 同向。
                # 病根：`mean_y` 的单位是 **bp**（= 预期的有利移动，来自
                # `gate_decision` 的 `mean_y` / `flow_rules.situation_decision`），
                # 而 **吃单进场 + 挂单出场的往返成本是 9bp**
                # （`break_even_move_bp(entry_is_maker=False, exit_is_maker=True)`）。
                # ⇒ 旧门槛允许 **0.9bp 的预期**去付 **9bp 的成本**：期望直接为负，
                #   门槛形同虚设。实测后果（近 24h）：
                #     `flow_entry_maker` 554 腿 均 spread **+6.15bp**  净 **+$63.17**
                #     `flow_entry_taker` 220 腿 均 spread **−4.24bp**  净 **−$16.31**
                #   两者唯一的差别就是"过价 vs 挂单"，而模型捕获只有约 +0.56bp。
                #
                # 修法：把门槛换成**成本门槛** —— 预期有利移动必须**盖过往返成本**。
                # 这是经济常识，不是新增门禁（规矩第 4.1 条：关掉它期望会变差 ⇒ 保留）。
                # 成本从唯一真相源取（规矩第 1 条），不再内联数字。
                # 注意：门槛不通过 ⇒ **退回挂单进场**（`active_flow` 的默认路径），
                # 不是"拒绝开仓" ⇒ 机会不减少，只是不再付费买"马上有仓"。
                #
                # 回滚：`MM_TAKER_ENTRY_COST_MULT=0` ⇒ 门槛归零 ⇒ 逐字恢复旧行为。
                taker_entry=(_need_taker_bp > 0
                             and float(_gate.get("mean_y") or 0.0) >= _need_taker_bp
                             and (abs(float(_ba_af or 0.0) - float(_bb_af or 0.0))
                                  / max(float(mid), 1e-9) * 1e4) <= 8.0
                             and float(_gate.get("mean_y") or 0.0)
                             * float(_trend_af or 0.0) >= 0.0
                             and not bool(_gate.get("probe"))),
                # [2026-10-09 重复来回做市] 车道「只减不加」开关透传:
                # ping-pong 用它当进场总闸(空仓且非只减不加才挂进场对),
                # 情况表的 side 不再决定挂哪一边。
                flow_exit_only=bool(flow_exit_only),
                # [2026-10-09 进化重挂] 学习参数与桶级情形表透传
                # （env 覆盖 > 这里的学习值 > 代码默认）。
                pp_rest_sec=float(_learn.get("pp_rest_sec") or 15.0),
                pp_thin_frac=float(_learn.get("pp_thin_frac") or 0.5),
                pp_exit_ticks=float(_learn.get("pp_exit_ticks") or 1.0),
                pp_bucket_min_n=float(_learn.get("pp_bucket_min_n") or 20.0),
                pp_sit_doc=_ppsit,
            )
            # [h899d 概率驱动出场] **持仓中每拍用概率模型重估剩余优势**(替代旧的
            # 一次性 direction_score 翻面检查):进场是"概率说涨就买",出场就是
            # "概率不再说涨就卖"。信号翻面(持仓方向的概率优势 < −1bp)⇒ flow_mu=−1
            # ⇒ 下一拍 maker_edge 在对手价挂平仓单(0 费),不等止损、不等超时。
            # 滞后:−1~0bp 之间不误平(h886 教训:小抖动就平 ⇒ 捕获归 0)。
            try:
                if abs(float(state.qty or 0.0)) > 1e-12:
                    _held = "buy" if float(state.qty or 0.0) > 0 else "sell"
                    _tr9b = 0.0
                    _t120b = 0.0
                    try:
                        _tr9b = float(trend_move_bp(
                            list(state.mid_hist or []), 60) or 0.0)
                        _t120b = float(trend_move_bp(
                            list(state.mid_hist or []), 120) or 0.0)
                    except Exception:
                        pass
                    from backend.services.market_maker.trend_prob import (
                        held_edge_bp)
                    _edge = held_edge_bp(_root, {
                        "mp_skew_bp": float(mp_skew_bp or 0.0),
                        "ofi": float(ofi or 0.0),
                        "obi_top": float(obi_top or 0.0),
                        "trend_20s": float(_trend_af or 0.0),
                        "trend_60s": _tr9b, "trend_120s": _t120b,
                        "accel": float(_trend_af or 0.0) - _tr9b,
                        "vol_20s": float(_vol_af or 0.0)},
                        held_side=_held)
                    if _edge is not None:
                        # 清晰翻面(edge<−1bp)⇒ −1 触发离场;否则保持正(持有)
                        state.flow_mu = -1.0 if _edge < -1.0 else max(0.5, _edge)
                    elif not bool(getattr(state, "flow_rechecked", False)):
                        # 模型不可用 ⇒ 回退旧的 direction_score 强翻面检查(一次性)
                        state.flow_rechecked = True
                        from backend.services.market_maker.dirscore import (
                            direction_score)
                        _d2 = direction_score(
                            float(mp_skew_bp or 0.0), float(ofi or 0.0),
                            float(_trend_af or 0.0), _tr9b)
                        if float(state.qty or 0.0) * float(_d2) < 0.0 \
                                and abs(float(_d2)) >= 0.3:
                            state.flow_mu = -1.0
            except Exception:
                pass
            return dec, {}
        except Exception as _afe:
            try:
                import logging as _lg
                _lg.getLogger("mm").warning("[active_flow] 本秒跳过，不退回做市: %s", _afe)
            except Exception:
                pass
            dec.skip = "active_flow_error"
            dec.bid = dec.ask = 0.0
            return dec, {}

    # [h527 2026-09-29] 单币目标名义：逐币比例优先（缺失 ⇒ 旧的全局值，逐字一致）。
    # 同时是该币 `inv_ratio` 的分母（下方 1491 行）⇒ 缩小该币规模会让库存偏斜
    # 更早介入、更快减仓，二者语义一致 ✓。
    limit_notional = equity * symbol_lookup(
        getattr(limits, "per_symbol_max_notional_ratio", None),
        state.symbol, limits.max_net_directional_ratio)

    # [F89a 2026-09-14] 陈旧挂单保护：挂单年龄超限 ⇒ 丢弃挂单、不判成交。
    # 现场事故：币种重新加入宇宙时运行态残留数天前的挂单，首个 tick 把当前区间
    # 成交判成这些旧价位的成交（4 笔幻影成交、净敞口 -$739 > 上限 $300，且账本
    # 用旧 ref_mid 记成假盈利）。默认阈值 90s（6 个 15s tick）。
    _max_q_age = float(getattr(limits, "max_quote_age_sec", 0.0) or 0.0)
    if (_max_q_age > 0 and state.quote_ts > 0
            and (now_ts - float(state.quote_ts)) > _max_q_age):
        state.quote_bid = state.quote_ask = state.quote_ts = 0.0
        state.quote_mid = 0.0
        dec.skip = dec.skip or "stale_quote_cleared"

    # ① 旧挂单成交判定（区间成交明细）
    # **两侧独立判定**：一侧被敞口/趋势闸门挡住时挂单价为 0，但另一侧的挂单
    # 依然真实存在、必须照常检查成交。此前用 `bid>0 and ask>0` 作为总开关，
    # 结果库存到顶后减仓腿永远不被检查 → 只能等超时砸单（实测平仓占比
    # 从回放的 14% 涨到 32%，净期望因此转负）。
    # [F176] 被判定挂单：优先用调用方显式传入的"延迟一档"挂单 ✓（见参数说明），
    # 否则沿用状态里的挂单（回放与旧行为完全一致 ✓）。
    if judged_quote is not None:
        _jq_bid, _jq_ask, _jq_mid = (float(judged_quote[0] or 0.0),
                                    float(judged_quote[1] or 0.0),
                                    float(judged_quote[2] or 0.0))
    else:
        _jq_bid, _jq_ask, _jq_mid = (float(state.quote_bid or 0.0),
                                     float(state.quote_ask or 0.0),
                                     float(state.quote_mid or 0.0))
    if _jq_bid > 0 or _jq_ask > 0:
        pen = max(0.0, float(PENETRATION_BP)) / 1e4
        # [h621] tick 级：价位上有真实成交量即成交，含恰好打在挂单价上的那一笔。
        # 桶路径仍要求价格穿过挂单。恰好相等时，严格小于会把贴价成交全部丢掉。
        if tick_fill:
            hit_buy = _jq_bid > 0 and float(vol_le_bid or 0.0) > 0
            hit_sell = _jq_ask > 0 and float(vol_ge_ask or 0.0) > 0
        else:
            hit_buy = (_jq_bid > 0 and seg_taker_sell > 0
                       and seg_low < _jq_bid * (1.0 - pen))
            hit_sell = (_jq_ask > 0 and seg_taker_buy > 0
                        and seg_high > _jq_ask * (1.0 + pen))
        legs: List[Tuple[str, float]] = []
        if hit_buy:
            legs.append(("buy", _jq_bid))
        if hit_sell:
            legs.append(("sell", _jq_ask))
        # [h621 临时诊断] 命中判定输入/输出（定位零成交用）
        if tick_fill and (seg_taker_sell or seg_taker_buy):
            try:
                import pathlib as _pl2
                _dp2 = _pl2.Path(__file__).resolve().parents[3] / "logs" / "h621_diag.log"
                with open(_dp2, "a", encoding="utf-8") as _f:
                    _f.write(f"{time.strftime('%H:%M:%S')} HIT {state.symbol} "
                             f"jq=({_jq_bid:.6f},{_jq_ask:.6f}) "
                             f"seg=({seg_low:.6f},{seg_high:.6f}) sv={seg_taker_sell:.3f} bv={seg_taker_buy:.3f} "
                             f"vlb={float(vol_le_bid or 0):.4f} vga={float(vol_ge_ask or 0):.4f} "
                             f"hit_b={hit_buy} hit_s={hit_sell} legs={legs}\n")
            except Exception:   # noqa: BLE001
                pass
        # [h395] 加仓腿名义在下方按 `_eff_notional`（止损后衰减）逐腿计算；
        # 减仓腿仍精确平仓（F91）。固定基础币口径不变。
        # [F75 2026-09-12 回放/实盘同口径] 队列份额约束：影子成交模拟必须与回放
        # 一致——排在既有做市商之后，只能吃到区间主动量的一部分（F59_QUEUE_SHARE，
        # 默认 0.30）。此前实盘每段全量吃 $100、回放只吃 30%：库存摆动 ~3× 大、
        # 漂移亏损 ~3× 大——回放正收益的配置在实盘变负的根因。
        try:
            from backend.services.market_maker.replay import (
                MIN_FILL_NOTIONAL as _MIN_FILL_NOTIONAL,
                QUEUE_SHARE as _QUEUE_SHARE,
            )
        except Exception:
            _QUEUE_SHARE, _MIN_FILL_NOTIONAL = 0.30, 10.0
        # 归因口径：价差用**挂单时的中价**做基准（挂单意图的边际），
        # 行情从挂单到成交的移动归入 price 维度。
        # 若用成交判定时的中价，行情下跌会把负值塞进 spread，
        # 看起来像「挂宽 8bp 却负价差」，实际是逆选择（总量不变，但归因不可读）。
        ref_mid = _jq_mid if _jq_mid > 0 else mid
        # [h527 2026-09-29] **逐币单笔规模倍数**（缺失 ⇒ 1.0 ⇒ 旧行为逐字一致）。
        # 依据 h524：NEAR/ARB/ENA 占 32% 的腿、却占 90% 的止损腿，而直接删币会把
        # 64.5 腿/h 打到 43.9（破 ≥60 硬约束）。**腿数是"事件数"不是名义额** ⇒
        # 缩小这几个币的单笔目标量可在腿数不变的前提下把尾部 USD 亏损等比压下去。
        _psize = symbol_lookup(getattr(limits, "per_symbol_size_mult", None),
                              state.symbol, 1.0)
        for side, px in legs:
            # [F75] 队列份额：与回放同口径（只能吃到区间主动量的一部分）
            # [h621] tick 级口径：可吃量 = **价位上的真实量**（vol_at_price），
            #        不再用"整桶总量"外推（实测名义只覆盖 82%、p90 腿量 2.32× 真实量）
            if tick_fill:
                _avail = float(vol_le_bid if side == "buy" else vol_ge_ask)
            else:
                _avail = float(seg_taker_sell if side == "buy" else seg_taker_buy)
            # [F91 2026-09-14] 减仓腿按「**精确平掉现有仓位**」定量。
            # 缺陷现场：两腿都用 `leg_qty = fill_notional/mid`，而进场腿与出场腿
            # 的 mid 不同 ⇒ 每次往返都留下 |Δqty| 残差（账本实测每趟 +6.4e-7 BTC
            # ≈ $0.05，且因均值回归两方向**同号**）→ 静默单向库存漂移，账本能重建
            # 出运行态根本没有的持仓（BTC $5.10 幽灵仓）。平仓必须精确归零：
            # 减仓方向取 min(|现仓|, 队列份额)，绝不用 dollar 腿量「大致平掉」。
            _pos = float(state.qty or 0.0)
            _reducing = ((side == "sell" and _pos > 0)
                         or (side == "buy" and _pos < 0))
            # [h395] 止损后降腿量只作用于**加仓腿**（减仓腿仍精确平掉现有仓位，
            # F91 口径不变；否则残仓永远平不掉，F338 同理由）。
            # [h527] 逐币规模倍数同口径：**只乘加仓腿**（`_psize` 见上）。
            # [h657] Q 速控乘子再叠一层(同样只乘加仓腿)。
            # ⚠️ [h662 修复] 不能写 `q_size_mult or 1.0`:0.0(停加仓)是 falsy
            # 会被吞成 1.0 ⇒ Q 速控的"停"静默失效(当晚实测踩坑,falsy-0 第三次)。
            _qsm = (float(q_size_mult) if q_size_mult is not None else 1.0)
            # [h692] 方向分数分侧倾斜(仅加仓腿):与 Q 乘子相乘;None=不倾斜
            _sk = (float((add_size_mult or {}).get(side, 1.0))
                   if add_size_mult is not None else 1.0)
            _qsm = max(0.0, _qsm) * max(0.0, _sk)
            # [h736/h740 2026-10-03] 软模式缩量:必须在**记账前**生效(book 与
            # 账本同源)。方向来自 state.soft_side(本 tick 闸门段会写;fill 段
            # 先跑、读到的是上一 tick 闸门留下的标记,语义=挂出这张单时该侧
            # 被软闸标记)。h735 的教训:在闸门段缩放 dec.fills ⇒ book/账本 qty
            # 分叉 ⇒ reload_states 清成本价(ARB 开仓价 0 的根因)。
            _soft_qm = 1.0
            if _soft_mode and not _reducing:
                _ss = str(state.soft_side or "")
                if _ss and (side in _ss or _ss == "both"):
                    _soft_qm = _tsm
            # [h895 激进模式] **概率信心仓位放大**:方向由趋势概率给时,
            # 信心(胜方概率 − 门槛)越大仓位越大:刚压线 ⇒ 1.0x(不惩罚),
            # edge 0.08 ⇒ 2.0x,上限 2.5x。只乘加仓腿;非概率模式/无记录 ⇒ 1.0。
            # [h898 修复] 旧公式 0.7+edge/0.10 把"刚压线"的腿也打成 0.7x,
            # 与逐币闸门(0.25x)叠加 ⇒ 腿量缩到 $8~16(实测),与用户要的激进相反。
            # 时间戳 >5s 视为陈旧 ⇒ 1.0。 Kelly 思想:有信心才加码,没信心不加码。
            # [2026-10-09 改机制·不加门禁] 仓位随「同向持仓笔数」收敛,不随信心放大。
            # 旧机制:_prob_qm 信心越大仓位越大(最高 2.5x)⇒ 趋势强一直加 ⇒ 4 笔叠加
            # 仓位滚 4 倍 ⇒ 波动来了亏穿(GTC 凌晨5点 $367 就是这么来的)。
            # 新机制:第 1 笔足额,已有 N 笔同向持仓 ⇒ 第 N+1 笔 × 1/(N+1)。
            # 信心只决定「加不加」(方向),不决定「加多大」(大小由持仓数收敛)。
            _prob_qm = 1.0
            if not _reducing:
                # 同向持仓笔数(本币):当前持仓方向与本次同向才算
                _same_dir_n = 0
                try:
                    _cur_pos = float(state.qty or 0.0)
                    if (side == "buy" and _cur_pos > 1e-12) or \
                            (side == "sell" and _cur_pos < -1e-12):
                        _same_dir_n = 1   # 已有同向持仓
                except Exception:
                    _same_dir_n = 0
                # 概率信心记录仍读(供观测),但不再放大仓位
                try:
                    _pe_rec = (globals().get("_PROB_EDGE_CACHE") or {}).get(state.symbol)
                    if _pe_rec and (time.time() - float(_pe_rec[0])) < 5.0:
                        _prob_qm = 1.0   # 只记录,不放大
                except Exception:
                    _prob_qm = 1.0
                # 持仓收敛:第 1 笔 1.0,第 2 笔 0.5,第 3 笔 0.33...
                _converge = 1.0 / (1.0 + _same_dir_n)
                _prob_qm = _converge
            _add_notional = ((_eff_notional * _psize * _qsm * _soft_qm * _prob_qm)
                             if not _reducing
                             else float(fill_notional or 0.0))
            # [h621] 规模波动衰减：剧烈期（σ 大）压低加仓腿目标量——
            # 证据见 `LaneRiskLimits.size_vol_decay`（满额腿 −1.34 vs 被削腿 +0.79 bp/腿，
            # 5/5 币小半更优）。0=关=旧行为；只作用加仓腿（减仓腿精确平仓，F91）。
            _vd = float(getattr(limits, "size_vol_decay", 0.0) or 0.0)
            if _vd > 0 and not _reducing:
                _sig_d = max(0.0, float(sigma_norm or 0.0))
                _cap_d = float(getattr(params, "k_vol_sigma_cap", 0.0) or 0.0)
                if _cap_d > 0:
                    _sig_d = min(_sig_d, _cap_d)
                _add_notional = _add_notional / (1.0 + _vd * _sig_d)
            _target_qty = abs(_pos) if _reducing else (_add_notional / mid if mid > 0 else 0.0)
            qty = min(_target_qty, _avail * _QUEUE_SHARE)
            # [h621 临时诊断] 腿量与地板判定（定位零成交用）
            try:
                import pathlib as _pl3
                _dp3 = _pl3.Path(__file__).resolve().parents[3] / "logs" / "h621_diag.log"
                with open(_dp3, "a", encoding="utf-8") as _f:
                    _f.write(f"{time.strftime('%H:%M:%S')} LEG {state.symbol} {side} "
                             f"avail={_avail:.4f} share={_QUEUE_SHARE} target={_target_qty:.4f} "
                             f"qty={qty:.4f} px={px} notional={qty*px:.2f} "
                             f"floor={_MIN_FILL_NOTIONAL} 过地板={qty*px >= _MIN_FILL_NOTIONAL}\n")
            except Exception:   # noqa: BLE001
                pass
            # [F338 2026-09-22] 单腿名义硬上限（**只约束加仓腿**）。
            # 缺陷现场：`_avail` = 该 15s 桶的主动成交量 ⇒ 成交量洪泛的桶
            # 会把腿量放大到中位数的 71.5×（实测 $11,661 单腿），而洪泛桶正是
            # `price_bp` 最差处。实测前 10 腿 = 全部亏损的 41%。
            # 减仓腿**不受限**：否则残仓永远平不掉，亏损会从价差搬到 taker 强平。
            # 0/负 = 关闭（与旧行为逐字一致）。
            _capm = float(getattr(limits, "max_leg_notional_mult", 0.0) or 0.0)
            if _capm > 0 and not _reducing and px > 0:
                _cap_notional = _capm * float(fill_notional or 0.0)
                _cap_qty = _cap_notional / px
                if qty > _cap_qty:
                    dec.leg_capped = dec.leg_capped + 1
                    qty = _cap_qty
            # [h781 2026-10-03 用户指令"继续"] **单币持仓名义硬上限(焊死)**:
            # 只约束加仓腿(减仓腿精确平仓 F91,不受限;否则仓位永远出不去)。
            # 仓位名义 = |当前持仓| × px;超过 _HARD_POS_MAX_MULT × 基础腿名义
            # ⇒ 把本腿加仓量裁到"顶到上限"为止(能顶到哪算哪,不 skip 整腿)。
            if not _reducing and _eff_notional > 0 and px > 0:
                _pos_cap = _HARD_POS_MAX_MULT * float(_eff_notional)
                _cur_abs = abs(float(state.qty or 0.0))
                _deepen = (_cur_abs <= 1e-12) or (
                    (side == "buy" and float(state.qty or 0.0) >= -1e-12)
                    or (side == "sell" and float(state.qty or 0.0) <= 1e-12))
                if _deepen:
                    _room = max(0.0, _pos_cap / px - _cur_abs)
                    if qty > _room:
                        dec.leg_capped = dec.leg_capped + 1
                        qty = _room
            if qty * px < _MIN_FILL_NOTIONAL:
                continue
            # ── [整顿轮·T27 2026-10-06] **腿量溯源遥测**（只写日志，不改任何行为）──
            #
            # 待查异常：$10,083 权益、`cap ≤ equity×5%` = $504 的约束下，
            # 实测出现单腿 **$66,655**（133×）。同一倍数在 $10,252 权益时重现
            # （$512 → $68,247），⇒ 系统性偏差。
            #
            # 已排除：`notional` 口径、`round_qty` 放大、分笔累积、`equity=self.equity`
            # 漏传（`runner.py:6322` 确已传）。
            #
            # 本遥测要一次问清：**哪个守卫没生效**。
            #   · `_capm`（`max_leg_notional_mult`）> 0 时，加仓腿应被裁到
            #     `_capm × fill_notional`（3 × $1,500 = $4,500）
            #   · `_reducing=True` 会**跳过**该守卫（减仓腿不受限，F91/F338 设计）
            #   · `_HARD_POS_MAX_MULT` 守卫同理只约束加仓腿
            try:
                import pathlib as _pl4
                _dp4 = _pl4.Path(__file__).resolve().parents[3] / "logs" / "leg_size_trace.log"
                with open(_dp4, "a", encoding="utf-8") as _f4:
                    _f4.write(
                        f"{time.strftime('%H:%M:%S')} {state.symbol} {side} "
                        f"qty={qty:.6f} px={px} notional={qty*px:.2f} "
                        f"avail={_avail:.4f} share={_QUEUE_SHARE} "
                        f"target_qty={_target_qty:.6f} reducing={_reducing} "
                        f"capm={_capm} fill_notional={float(fill_notional or 0.0):.2f} "
                        f"cap_notional={(float(_capm or 0.0)*float(fill_notional or 0.0)):.2f} "
                        f"eff_notional={float(_eff_notional or 0.0):.2f} "
                        f"leg_capped={getattr(dec, 'leg_capped', 0)} "
                        f"equity_passed={float(getattr(limits, '_dbg_equity', 0.0) or 0.0):.2f}\n")
            except Exception:   # noqa: BLE001
                pass
            # [h669 修复] 真实交易所条件必须在 **book 应用之前** 生效:
            # 数量/价格按 venue 过滤器取整+校验,book 与账本同源一致。
            # (此前在 _record_fills 才取整 ⇒ 账本数量≠运行态数量 ⇒ 启动对账
            # F301 误判"分叉"⇒ 清掉成本价 ⇒ 面板"开仓 0.000000"事故。)
            from backend.services.market_maker.venue_filters import (
                passes as _vp, round_px as _vpx, round_qty as _vqty,
            )
            _qty_v = _vqty(state.symbol, qty)
            _px_v = _vpx(state.symbol, px)
            if _qty_v <= 0 or not _vp(state.symbol, _px_v, _qty_v,
                                      float(ref_mid or 0.0))[0]:
                # [h741 2026-10-03 修崩溃] plan_tick 是自由函数,此前写
                # `self._venue_filter_skips += 1` ⇒ NameError ⇒ 整个 tick 失败
                # (03:16 起半分钟一次,用户报"崩了")。改用模块级计数。
                _VENUE_FILTER_SKIPS[0] += 1
                continue
            qty, px = _qty_v, _px_v
            edge = ((ref_mid - px) if side == "buy" else (px - ref_mid)) / ref_mid * 1e4
            _pre_qty = float(state.qty or 0.0)   # [h403] 开仓判定：加仓前持仓
            d = local_book.apply_fill(symbol=state.symbol, side=side, qty=qty, fill_px=px,
                                      mid_px=ref_mid, fee_rate=maker_fee_bp / 1e4,
                                      now_ts=now_ts)
            state.qty = local_book.qty(state.symbol)
            # [h403] 空仓→持仓 = 新仓开立：按当前 mid_hist 标记形态（分形态持有期）
            if abs(_pre_qty) <= 1e-12 and abs(state.qty) > 1e-12:
                state.pattern_tag = _detect_pattern(
                    list(state.mid_hist or []),
                    float(getattr(limits, "p1_trigger_bp", 15.0) or 15.0))
            pos = local_book.positions.get(state.symbol)
            state.avg_px = pos.avg_px if pos else 0.0
            state.avg_mid = pos.avg_mid if pos else 0.0
            # [h442] 最近加仓腿中价：同向加仓（仓位变深）⇒ 更新；平仓 ⇒ 清零。
            if abs(state.qty) > abs(_pre_qty) and _pre_qty * state.qty >= 0:
                state.last_entry_mid = ref_mid
            elif abs(state.qty) <= 1e-12:
                state.last_entry_mid = 0.0
            state.opened_ts = pos.opened_ts if pos else 0.0
            state.last_ts = now_ts
            dec.fills.append(PlannedFill(
                symbol=state.symbol, side=side, qty=qty, px=px, mid=ref_mid,
                ts=now_ts, edge_bp=edge,
                spread_usd=float(d.get("spread_usd") or 0.0),
                price_usd=float(d.get("price_usd") or 0.0),
                fee_usd=float(d.get("fee_usd") or 0.0),
                position_id=str(d.get("position_id") or ""),
                # [h402] #13 挂单时刻（成交时刻归因被机械污染，h369；挂单时刻才能
                # 做正确的流条件归因）。judge_lag_buckets=0 时 state.quote_ts 即
                # 被判定挂单的真实挂出时刻。
                quote_ts=float(state.quote_ts or 0.0),
                # [F257] 落盘判定依据：桶的极值与"桶内是否真有成交落在我们价位上"。
                # 这是事后唯一能把"桶内极值顺带穿过"与"真有成交在我们价位"
                # 分开的信息（H28 发现 47.7% 的成交价在真实市场里找不到对应）。
                seg_low=float(seg_low or 0.0),
                seg_high=float(seg_high or 0.0),
                # ⚠️ `px_exact_hit` 保持 None —— 我们手上**只有桶级**数据
                #    （low/high/taker_buy/taker_sell），拿不到桶内逐笔
                #    ⇒ **无法**在本函数里判断"桶内是否真有成交落在我们的价位上"。
                #    留 None 是诚实的：写成 True/False 都是编造。
                #    真正的判定必须由能读 `asterdex_trades` 的离线分析器做
                #    （见 `fill_basis_analyzer`）。
                px_exact_hit=None,
                # [h754 A1] 严格审计输入:判定用的挂单时刻与逐笔窗口
                judge_ts=float(judge_ts or 0.0),
                win_lo_ms=int(win_lo_ms or 0),
                win_hi_ms=int(win_hi_ms or 0),
            ))
            # [h624] maker 成交入 markout 待解析队列（减仓/加仓都记；闸门用滚动均值）
            _enqueue_markout_sample(
                state, side=side, fill_px=float(px),
                capture_bp=float(edge), notional=float(qty) * float(px),
                now_ts=float(now_ts), horizon_sec=_mk_hz,
            )
            # [h624/A5] 止损宽限期内被动减仓 ⇒ 出口记 stop_loss_maker（可与 taker 分账）
            if (float(state.stop_since or 0.0) > 0 and _reducing
                    and not str(getattr(dec, "exit_path", "") or "")):
                dec.exit_path = "stop_loss_maker"
                dec.skip = dec.skip or "stop_loss_maker"
            # ── [整顿轮·T10 2026-10-05] 被动成交必须**始终**有出口标签 ──
            # 实测：24h 有 340 条腿 `exit_path` 为空（均 net_bp −9.77，净 −$58.85）。
            # 病根：上面那条只覆盖「止损宽限期内减仓」，普通被动成交没有任何分支
            # 给它命名 ⇒ 归因表里出现一大块"未标注"，无法判断钱花在哪。
            # 修法：按**是否减仓**给出中性但明确的名字。不改任何交易行为，只补标签。
            if not str(getattr(dec, "exit_path", "") or ""):
                dec.exit_path = "flow_exit_maker" if _reducing else "flow_entry_maker"
                dec.skip = dec.skip or dec.exit_path
            # 毒性流判定：成交后中价相对**挂单时中价**的反向移动。
            # 买在挂单价、随后中价继续跌（或卖完继续涨）→ 逆选择。
            move_bp = ((mid - ref_mid) if side == "buy" else (ref_mid - mid)) / ref_mid * 1e4
            if move_bp < -limits.toxic_bp:
                state.toxic_streak += 1
            else:
                state.toxic_streak = 0

    # ── [F258 2026-09-20] 交易时长窗口：**下限**闸门 ────────────────────────
    #
    # 用户给定：「交易时间锁定在 30秒到5分钟之内，这个是有验证过的」。
    #
    # 上界 `max_one_side_seconds` 已在 ② 中生效；这里补**下限**：
    # 持仓未满 `min_hold_seconds` 时，**禁止引擎主动发起**的四条强制出口
    # （①′ 止损 / ② 超时 / ②′ OFI 择时 / ③ 反手）。
    #
    # 实测依据（H26，8h，66 个往返）：出场腿 −$0.032/往返 = 入场腿盈利的 3 倍，
    # 而**出场成本与持有时长几乎无关**（穿越点差 + 对手方逆向选择）
    # ⇒ 刚建仓就强制平掉是白付一次出场成本。
    #
    # `_force_exit_allowed()` 返回 (是否允许, 原因)。`hold_too_young` 表示
    # 未到下限 —— 此时**不 taker 平仓**，但减仓侧的 **maker 报价照常挂**
    # （下方报价流程），所以真正的行情到来时仍能自然出库。
    _exit_ok, _exit_why = _force_exit_allowed(state, now_ts, limits)

    # [h389 2026-09-27] 尾随锁利：MFE 追踪（锚定 avg_mid，换仓/平仓自动重置）。
    _upl_bp = 0.0
    if abs(state.qty) > 1e-12 and state.avg_mid > 0:
        _upl_bp = (state.qty * (mid - state.avg_mid)
                   / (abs(state.qty) * state.avg_mid) * 1e4)
        if abs(state.avg_mid - state.mfe_avg_mid) > 1e-12:
            state.mfe_bp = _upl_bp
            state.mfe_avg_mid = state.avg_mid
        elif _upl_bp > state.mfe_bp:
            state.mfe_bp = _upl_bp
    else:
        state.mfe_bp = 0.0
        state.mfe_avg_mid = 0.0
        state.pattern_tag = ""            # [h403] 空仓 ⇒ 形态标记清除

    # ①′ [F71] 止损平仓：浮亏超阈值立即平（早于超时，削掉尾部亏损）
    # [F231] 波动条件：`stop_loss_vol_min > 0` 时只有 σ_norm ≥ 该值才启用止损；
    # 0 = 恒启用（旧行为逐字一致）。依据 F230 四场景：常数止损在正常日多亏 ✗
    # （被震荡反复打止损、白付 taker 腿），只在波动高的 regime 才划算 ✓。
    _sl_bp = float(limits.stop_loss_bp or 0.0)
    _sl_vmin = float(getattr(limits, "stop_loss_vol_min", 0.0) or 0.0)
    # [F264 2026-09-16] 快武装：本步中价移动 ≥ mult × 波动基准 ⇒ 视为「快跌/快涨」，
    # 跳过慢速 σ_norm 波动闸（20 样本 ≈ 10 分钟窗口武装太晚，F250#4 实测快跌中
    # 止损成交在 −75bp 而非触发线 −10bp ✗）。mult ≤ 0 = 关闭（旧行为一致）。
    _fast_mult = float(getattr(limits, "stop_loss_fast_mult", 0.0) or 0.0)
    _fast_arm = False
    if _fast_mult > 0 and float(state.vol_baseline_bp or 0.0) > 0 and len(state.mid_hist) >= 2:
        # 注意：live tick 与回放都在调用 plan_tick **之前**把本 tick 的 mid 追加进
        # mid_hist ⇒ mid_hist[-1] == 当前 mid（步长恒 0 ✗）。上一快照是 [-2]。
        _prev_mid = float(state.mid_hist[-2])
        if _prev_mid > 0 and mid > 0:
            _step_bp = (mid - _prev_mid) / _prev_mid * 1e4
            _fast_arm = abs(_step_bp) >= _fast_mult * float(state.vol_baseline_bp)
    if _sl_bp > 0 and _sl_vmin > 0 and float(sigma_norm or 0.0) < _sl_vmin and not _fast_arm:
        _sl_bp = 0.0
    # [h442 2026-09-28] 止损参考价：`stop_ref_last_leg>0` 时取"最差入场"口径
    # （多头 max(avg, last_entry)、空头 min(avg, last_entry)）⇒ 更快触发，
    # 减少多腿摊低后 avg 拖后造成的深亏与"盈利仓被止损"两个偏差；0 = 旧行为逐字一致。
    _sl_ref = float(state.avg_mid or 0.0)
    if float(getattr(limits, "stop_ref_last_leg", 0.0) or 0.0) > 0 \
            and float(state.last_entry_mid or 0.0) > 0 and abs(state.qty) > 1e-12:
        _sl_ref = (max(_sl_ref, float(state.last_entry_mid)) if state.qty > 0
                   else min(_sl_ref, float(state.last_entry_mid)))
    _sl_hit = bool(abs(state.qty) > 1e-12
                   and should_stop_loss(state.qty, _sl_ref, mid, _sl_bp))
    # [h389 2026-09-27] 尾随锁利：MFE ≥ trail_lock_bp 后止损线抬至保本并逐档上移；
    # 浮盈回落至尾随线即触发（复用下方 maker 宽限→taker 流程）。
    _trail_bp = float(getattr(limits, "trail_lock_bp", 0.0) or 0.0)
    _trail_hit = False
    if (_trail_bp > 0 and abs(state.qty) > 1e-12):
        # [h389] 浮点护栏：bp 由 *1e4 除法而来，档位边界会出现
        # 29.999999999999716 这类误差，floor 除法会把档位吞成 0。
        # 先圆整到 1e-6 bp（远小于任何阈值语义）再判定与取档。
        _mfe_r = round(float(state.mfe_bp), 6)
        if _mfe_r >= _trail_bp:
            _line = -5.0 + 10.0 * ((_mfe_r - _trail_bp) // 10.0)
            if round(_upl_bp, 6) <= _line:
                _trail_hit = True
    _sl_hit = bool(_sl_hit or _trail_hit)
    # [h668] 变盘先减仓:300s 趋势反向越过阈值 ⇒ 与止损同一套 maker 宽限流程
    # (先 maker 地板单宽限 stop_maker_grace_sec,再 taker)。动机:今日实测变盘时
    # 库存被趋势整体标记,止损/超时出口每条 −16~−128bp 吃掉一半以上亏损;
    # 提前在趋势翻转点 maker 减仓 = 少穿越点差、少跳空。
    _tff = float(getattr(limits, "trend_flip_flatten_bp", 0.0) or 0.0)
    _tff_hit = False
    if (_tff > 0 and not _sl_hit and abs(state.qty) > 1e-12
            and state.opened_ts > 0 and len(state.mid_hist) >= 20):
        _tbp = trend_move_bp(state.mid_hist, 20)   # 与趋势闸同口径(20期×15s=300s)
        if (state.qty > 1e-12 and _tbp <= -_tff) or \
                (state.qty < -1e-12 and _tbp >= _tff):
            _sl_hit = True
            _tff_hit = True
    _grace = float(getattr(limits, "stop_maker_grace_sec", 0.0) or 0.0)
    # [h700 2026-10-02] **硬距离 fail-fast**:maker 宽限期内,浮亏越过
    # `stop_taker_bp` ⇒ 跳过宽限立即 taker。病根(23:47 WLD 实测 −127bp):
    # 崩盘时 maker 止损地板单**追着中价下移**,只在反弹时成交 ⇒ 成交落在
    # 崩盘最低点,止损距离无上限(−40bp 阈值实亏 −127bp;15:26 ENA 同族 −310bp)。
    _staker = float(getattr(limits, "stop_taker_bp", 0.0) or 0.0)
    # ── [h900 2026-10-07 根因·出场环] 穿透 fail-fast 收紧到「止损线 × 1.3」──
    # 实锤的灾难链:止损 −40bp 触发 → maker 宽限 300s 死等(想省 4bp 费) →
    # 崩盘里没人接单 → 价格崩到 −350bp 才成交(SI/LYN/牛来/USELESS 实测)。
    # 止损线被穿透 30% 就说明 maker 单没人接(价格在崩) ⇒ 立即 taker 保命,
    # 不等宽限。省 4bp 费 = 亏 350bp,这笔账必须算过来。
    _pen_mult = float(os.getenv("MM_STOP_PEN_MULT", "1.3") or 1.3)
    _pen_cap = (_sl_bp * _pen_mult) if _sl_bp > 0 else 0.0
    _hard_cands = [x for x in (_staker, _pen_cap) if x > 0]
    _hard_dist = min(_hard_cands) if _hard_cands else 0.0   # 取更紧的(更早触发)
    if (_sl_hit and _hard_dist > 0 and abs(state.qty) > 1e-12
            and abs(float(_upl_bp)) >= _hard_dist):
        state.stop_since = float(now_ts) - 1e9   # 视同宽限已耗尽 ⇒ 下方立即 taker
    if not _sl_hit:
        # [F235] 止损条件解除（价格回摆/仓位被 maker 吃掉）⇒ 宽限计时归零 ✓
        state.stop_since = 0.0
    elif _grace > 0 and float(state.stop_since or 0.0) <= 0:
        # [F235] 首次触发：先给减仓侧 maker 单宽限（下方报价流程每 tick 都在挂
        # 减仓侧地板单），本 tick 不 taker。计时从本 tick 起算 ✓。
        state.stop_since = now_ts
    if _sl_hit and (_grace <= 0
                    or (now_ts - float(state.stop_since or 0.0)) >= _grace):
        # [h700] 硬距离 fail-fast 不受 F258 时长下限约束(浮亏已越过上限,再等是加码)
        _hard_cap = bool(_staker > 0 and abs(float(_upl_bp)) >= _staker)
        if not _exit_ok and not _hard_cap:
            # [F258] 未到时长下限 ⇒ 不主动平仓。但**不能什么都不做**：
            # 止损条件仍成立，说明行情对我们不利；此时把减仓侧做成
            # "宽限地板单"（下方报价流程每 tick 都挂）等对手方来接，
            # 而不是自己穿越点差付两次成本。
            dec.skip = dec.skip or _exit_why
            state.stop_since = 0.0
        else:
            hs = max(0.0, float(half_spread or 0.0))
            side = "sell" if state.qty > 0 else "buy"
            px = (mid - hs) if side == "sell" else (mid + hs)
            qty = abs(state.qty)
            fd = local_book.apply_fill(symbol=state.symbol, side=side, qty=qty, fill_px=px,
                                       mid_px=mid, fee_rate=abs(taker_fee_bp) / 1e4,
                                       now_ts=now_ts)
            state.qty = local_book.qty(state.symbol)
            state.avg_px = state.avg_mid = state.opened_ts = 0.0
            state.mfe_bp = state.mfe_avg_mid = 0.0     # [h389] 平仓重置尾随状态
            state.last_ts = now_ts
            state.stop_since = 0.0      # [F235] 已 taker 平仓 ⇒ 宽限计时归零 ✓
            state.last_stop_ts = now_ts # [h395] 强制止损离场时刻（#16③ 衰减窗起点）
            dec.fills.append(PlannedFill(
                symbol=state.symbol, side=side, qty=qty, px=px, mid=mid, ts=now_ts,
                is_flatten=True,
                spread_usd=float(fd.get("spread_usd") or 0.0),
                price_usd=float(fd.get("price_usd") or 0.0),
                fee_usd=float(fd.get("fee_usd") or 0.0),
                position_id=str(fd.get("position_id") or ""),
            ))
            dec.action = "flatten"
            if _tff_hit:
                dec.skip = "trend_flip_flatten"
                dec.exit_path = "trend_flip_taker"
            else:
                dec.skip = "trail_lock" if _trail_hit else "stop_loss"
                # [f342/f328] 出口标记（尾随/止损合并为同一强平流程后的形态）
                dec.exit_path = "trail_lock_taker" if _trail_hit else "stop_loss_taker"

    # ①′‴ [F348 2026-09-23] **跳变速退**：单 tick 跳变直接 taker 离场。
    # 实盘解剖：跳变在一个 15s 快照内完成，衰减离场（需 30s 持续延伸）看不见它，
    # 等 40bp 兜底接住时已 −33bp（阈值+滑点+费）。速退 12bp 封顶跳变损失。
    _jump_bp = float(getattr(limits, "jump_exit_bp", 0.0) or 0.0)
    if (_jump_bp > 0 and abs(state.qty) > 1e-12 and state.opened_ts > 0
            and (now_ts - state.opened_ts)
            >= float(getattr(limits, "reversal_decay_min_age_sec", 15.0) or 15.0)):
        _upl_bp = (state.qty * (mid - state.avg_mid)
                   / (abs(state.qty) * state.avg_mid) * 1e4) if state.avg_mid > 0 else 0.0
        _tick_bp = trend_move_bp(state.mid_hist, 1)
        _tick_adv = (-_tick_bp) if state.qty > 0 else _tick_bp  # 不利方向记正
        if (_upl_bp <= -_jump_bp
                and _tick_adv >= float(getattr(limits, "jump_exit_tick_bp", 8.0) or 8.0)):
            if not _exit_ok:
                dec.skip = dec.skip or _exit_why
            else:
                hs = max(0.0, float(half_spread or 0.0))
                side = "sell" if state.qty > 0 else "buy"
                px = (mid - hs) if side == "sell" else (mid + hs)
                qty = abs(state.qty)
                fd = local_book.apply_fill(symbol=state.symbol, side=side, qty=qty,
                                           fill_px=px, mid_px=mid,
                                           fee_rate=abs(taker_fee_bp) / 1e4,
                                           now_ts=now_ts)
                state.qty = local_book.qty(state.symbol)
                state.avg_px = state.avg_mid = state.opened_ts = 0.0
                state.last_ts = now_ts
                state.decay_since = 0.0
                dec.fills.append(PlannedFill(
                    symbol=state.symbol, side=side, qty=qty, px=px, mid=mid, ts=now_ts,
                    is_flatten=True,
                    spread_usd=float(fd.get("spread_usd") or 0.0),
                    price_usd=float(fd.get("price_usd") or 0.0),
                    fee_usd=float(fd.get("fee_usd") or 0.0),
                    position_id=str(fd.get("position_id") or ""),
                ))
                dec.action = "flatten"
                dec.skip = "jump_exit"
                dec.exit_path = "jump_exit_taker"

    # ①′″ [F346 2026-09-23] **反转衰减离场**（H284 P3 证据：替代固定硬止损的主离场）。
    #
    # H284（48h、8.2k 腿/政策模拟）：固定 6bp 止损净 −3.34bp/腿、MAE −3.9bp；
    # 反转衰减离场净 −0.80bp/腿、MAE −1.7bp —— 止损 3126 次每次卖在坑底 + 4bp 费。
    # 触发条件：持仓满 `reversal_decay_min_age_sec` 后，30s 趋势（2 期）朝不利方向
    # 延伸 ≥ `reversal_decay_bp`（多头=跌、空头=涨）⇒ 离场。
    # 执行：先减仓侧 maker 挂 `grace` 秒（0 费，报价流程每 tick 都在挂减仓侧），
    # 超时 taker 兜底。条件解除（价格回摆）⇒ 宽限计时归零。
    _decay_bp = float(getattr(limits, "reversal_decay_bp", 0.0) or 0.0)
    if (_decay_bp > 0 and abs(state.qty) > 1e-12 and state.opened_ts > 0
            and (now_ts - state.opened_ts)
            >= float(getattr(limits, "reversal_decay_min_age_sec", 15.0) or 15.0)):
        # [h325] 判定窗口可调（默认 2 期 = 30s，线上改为 4 期 = 60s；
        # h284 双窗复现：60s 净收益更好且 MAE 从 −1.80 收到 −1.34）
        _r30 = trend_move_bp(state.mid_hist,
                             int(getattr(limits, "reversal_decay_window_periods", 2) or 2))
        _adverse = ((_r30 <= -_decay_bp) if state.qty > 0 else (_r30 >= _decay_bp))
        if _adverse:
            _dgrace = float(getattr(limits, "reversal_decay_grace_sec", 30.0) or 0.0)
            if float(state.decay_since or 0.0) <= 0:
                state.decay_since = now_ts
            if (_dgrace <= 0 or (now_ts - float(state.decay_since)) >= _dgrace):
                if not _exit_ok:
                    dec.skip = dec.skip or _exit_why
                    state.decay_since = 0.0
                else:
                    hs = max(0.0, float(half_spread or 0.0))
                    side = "sell" if state.qty > 0 else "buy"
                    px = (mid - hs) if side == "sell" else (mid + hs)
                    qty = abs(state.qty)
                    fd = local_book.apply_fill(symbol=state.symbol, side=side, qty=qty,
                                               fill_px=px, mid_px=mid,
                                               fee_rate=abs(taker_fee_bp) / 1e4,
                                               now_ts=now_ts)
                    state.qty = local_book.qty(state.symbol)
                    state.avg_px = state.avg_mid = state.opened_ts = 0.0
                    state.last_ts = now_ts
                    state.decay_since = 0.0
                    dec.fills.append(PlannedFill(
                        symbol=state.symbol, side=side, qty=qty, px=px, mid=mid, ts=now_ts,
                        is_flatten=True,
                        spread_usd=float(fd.get("spread_usd") or 0.0),
                        price_usd=float(fd.get("price_usd") or 0.0),
                        fee_usd=float(fd.get("fee_usd") or 0.0),
                        position_id=str(fd.get("position_id") or ""),
                    ))
                    dec.action = "flatten"
                    dec.skip = "reversal_decay"
                    dec.exit_path = "reversal_decay_taker"
        else:
            state.decay_since = 0.0

    # ①″ [F301 2026-09-21] **止盈主动平仓**：浮盈 ≥ `take_profit_bp` 时主动（taker）落袋。
    #
    # 用户提出「检测盈利过多少之后是不是可以进行主动平仓？」。用真实盘口做了两轮验证：
    #   · 入场后的**最大有利偏移**（MFE，180s，n=300）：
    #       p25 +2.71  中位 **+6.77**  p75 +14.84  p90 +22.30  p95 +33.90 bp
    #     ⇒ 空间远大于被动出库赚到的 ~0.2bp 价差
    #   · **严格口径**规则期望（价格必须被对手方真打到才算成交，与引擎同语义）：
    #       T=5  触发 55.6% 净 +0.556bp      T=20 触发 12.0% 净 +1.920bp
    #       T=8  触发 44.0% 净 +1.760bp      T=30 触发  5.6% 净 +1.456bp
    #       **T=12 触发 32.0% 净 +2.560bp**  ← 最优，是现实基准（+0.1374bp）的 **18.6×**
    #   · 关键交叉验证：严格口径触发率 32.0% ≈ 乐观口径 31.3%
    #     ⇒ "价格到过就能成交"，不是"擦到但没被吃" ⇒ 可落地
    #
    # ⚠️ 为什么 T **必须 > taker 费(4bp)**：触发时是主动吃对手价，要付费。
    #   实测 T=3 时净 −1.00bp ⇒ 整体每笔 −0.733bp（比不做还差）。
    #   所以默认 `take_profit_bp=0.0`（关闭），启用时**不要低于 8bp**。
    #
    # 与 ①′ 止损对称：一个是亏损侧硬上界（40bp），一个是盈利侧落袋线（12bp）。
    # 放在止损之后、超时之前 —— 止盈优先于"等"，因为它的期望是正的。
    _tp_bp = float(getattr(limits, "take_profit_bp", 0.0) or 0.0)
    if (_tp_bp > 0 and abs(state.qty) > 1e-12 and state.avg_mid > 0
            and mid > 0 and _exit_ok):
        _upl_bp = (state.qty * (mid - state.avg_mid)
                   / (abs(state.qty) * state.avg_mid) * 1e4)
        if _upl_bp >= _tp_bp:
            _tp_grace = float(getattr(limits, "take_profit_maker_grace_sec", 0.0) or 0.0)
            _tp_ready = True
            if _tp_grace > 0:
                # 先给减仓侧 maker 单 N 秒机会（正常报价流程每 tick 都在挂减仓侧）
                if float(getattr(state, "tp_since", 0.0) or 0.0) <= 0:
                    state.tp_since = now_ts
                    _tp_ready = False
                elif (now_ts - float(state.tp_since)) < _tp_grace:
                    _tp_ready = False
                else:
                    state.tp_since = 0.0
            if _tp_ready:
                hs = max(0.0, float(half_spread or 0.0))
                side = "sell" if state.qty > 0 else "buy"
                px = (mid - hs) if side == "sell" else (mid + hs)
                qty = abs(state.qty)
                fd = local_book.apply_fill(symbol=state.symbol, side=side, qty=qty,
                                           fill_px=px, mid_px=mid,
                                           fee_rate=abs(taker_fee_bp) / 1e4,
                                           now_ts=now_ts)
                state.qty = local_book.qty(state.symbol)
                state.avg_px = state.avg_mid = state.opened_ts = 0.0
                state.last_ts = now_ts
                state.tp_since = 0.0
                state.take_profit_hits = int(getattr(state, "take_profit_hits", 0) or 0) + 1
                dec.fills.append(PlannedFill(
                    symbol=state.symbol, side=side, qty=qty, px=px, mid=mid, ts=now_ts,
                    is_flatten=True,
                    spread_usd=float(fd.get("spread_usd") or 0.0),
                    price_usd=float(fd.get("price_usd") or 0.0),
                    fee_usd=float(fd.get("fee_usd") or 0.0),
                    position_id=str(fd.get("position_id") or ""),
                ))
                dec.action = "flatten"
                dec.skip = "take_profit"
                dec.exit_path = "take_profit_taker"    # [F340]
        elif _exit_ok:
            state.tp_since = 0.0

    # ② 单边持仓超时 → 打对手价平仓（taker）
    #
    # [F296 2026-09-21] **加开关 `timeout_exit_maker_only`**：
    #   = True ⇒ 超时**不 taker**，改为「只撤加仓侧挂单、保留减仓侧挂单」，
    #             把出库完全交给被动成交（maker 免费），仅保留 ①′ 价格止损的 taker 权。
    #   = False（默认）⇒ 旧行为逐字一致（可一键回退 ✓）。
    #
    # 依据（本轮实测，全部来自我们自己的账本）：
    #   · 入场腿（maker）12,269 笔 平均 fee = **0.0000 bp** —— 完全免费
    #   · 强平腿（taker）   917 笔 平均 fee = **−3.9956 bp**
    #   · 强平腿累计 taker 费 = **−$50.87**，而整夜亏损 −$52.20 ⇒ **97% 的亏损是 taker 费**
    #   · 917 笔强平里 `price_bp ≤ −40bp` 的只占 **10.9%**，中位只漂移 **−7.39bp**
    #     ⇒ 近 90% 是在行情几乎没动时就 taker 出场 —— 白付 4bp
    #   · 被动出库的成功样本中 96.1% 在 120s 内、100% 在 300s 内完成
    #     ⇒ **300s 窗口不是瓶颈**，"价格回不来"不是因为等得不够
    #   ⇒ 结论：超时 taker 是在为「时间」付费，而成本来自「价格」。
    #     正确的出库条件应该只有价格（①′），时间到了只需**停止加仓、继续等被动成交**。
    #
    # ⚠️ 代价（必须盯的）：去掉超时会**拉长持仓时长** ⇒ 敞口占用更久、可能顶到
    #   净/总敞口上限而阻塞新仓；且若某个币**长期不回摆**，仓位会一直挂着。
    #   所以①′ 的 40bp 价格止损是这条改动的**必要配套**（它现在已恒启用，见 F295/H127）。
    #   监控口径：`timeout_exit_blocked` 计数 + 心跳里的 `states.*.qty`
    #   与 `skip_counts.symbol_exposure/net_exposure` 是否随之上行。
    _tmo_add_block = False   # [h413] maker-only 超时 ⇒ 下游封锁加仓侧
    # [h472 2026-09-29] 封锁加仓侧的**原因标签**必须跟随真正的触发源：
    # 该变量原先被硬编码成 "timeout_maker_only"（见下方 1605/1608），
    # 而 h472 让 ofi_flatten 的被动执行也走同一机制 ⇒ 若不改，`ofi_flatten_maker`
    # 会被记成 `timeout_maker_only`，又一次"出口归因错位"（F335/F340 同源事故）。
    _tmo_block_why = "timeout_maker_only"
    if abs(state.qty) > 1e-12 and state.opened_ts > 0 and \
            (now_ts - state.opened_ts) > _effective_hold_sec(state, limits):
        _age = now_ts - state.opened_ts
        # [h411 2026-09-27] 持仓硬上限：仅在 maker_only 分支内生效（maker_only=false
        # 时 90s 即 taker，硬上限无意义且必须保持旧行为逐字一致）。
        # timeout_hard_taker_sec>0 且年龄超过它 ⇒ 无条件 taker（用户约定=300s）。
        _hard = float(getattr(limits, "timeout_hard_taker_sec", 0.0) or 0.0)
        if bool(getattr(limits, "timeout_exit_maker_only", False)):
            if _hard > 0 and _age > _hard:
                # [h490] **必须在清零之前**落盘：本分支紧接着会把
                # `avg_px/avg_mid/opened_ts` 归零，之后再探针就只剩 0 了
                persist_exit_probe(symbol=state.symbol, state=state, mid=mid,
                                   kind="hardcap", ofi=float(ofi or 0.0),
                                   now_ts=now_ts)
                state.timeout_exit_blocked = int(getattr(state, "timeout_exit_blocked", 0) or 0) + 1
                hs = max(0.0, float(half_spread or 0.0))
                side = "sell" if state.qty > 0 else "buy"
                px = (mid - hs) if side == "sell" else (mid + hs)
                qty = abs(state.qty)
                fd = local_book.apply_fill(symbol=state.symbol, side=side, qty=qty, fill_px=px,
                                           mid_px=mid, fee_rate=abs(taker_fee_bp) / 1e4,
                                           now_ts=now_ts)
                state.qty = local_book.qty(state.symbol)
                state.avg_px = state.avg_mid = state.opened_ts = 0.0
                state.mfe_bp = state.mfe_avg_mid = 0.0
                state.pattern_tag = ""
                state.last_ts = now_ts
                state.stop_since = 0.0
                state.last_stop_ts = now_ts   # 强平离场 ⇒ #16③ 衰减窗同样生效
                dec.fills.append(PlannedFill(
                    symbol=state.symbol, side=side, qty=qty, px=px, mid=mid, ts=now_ts,
                    is_flatten=True,
                    spread_usd=float(fd.get("spread_usd") or 0.0),
                    price_usd=float(fd.get("price_usd") or 0.0),
                    fee_usd=float(fd.get("fee_usd") or 0.0),
                    position_id=str(fd.get("position_id") or ""),
                ))
                dec.action = "flatten"
                dec.skip = "timeout_hard_taker"
                dec.exit_path = "timeout_hard_taker"
            else:
                # 只登记、不 taker：下方报价流程仍会挂**减仓侧**地板单等对手方来接
                state.timeout_exit_blocked = int(getattr(state, "timeout_exit_blocked", 0) or 0) + 1
                dec.skip = dec.skip or "timeout_maker_only"
                # [h413 2026-09-27 修复] F296 设计"超时后只撤加仓侧挂单"此前**从未接线**
                # （timeout_exit_blocked 只有写入与遥测，无任何读取点）⇒ 超时仓一边等
                # 被动出场一边继续加仓（实测 ADA 78min 加到 −1108）。
                # allow_* 在下游 check_side_allowed 处才绑定 ⇒ 这里只立标记，
                # 稍后在 allow 旗标就绪后统一封锁加仓侧（减仓侧豁免 F76）。
                _tmo_add_block = True
        else:
            hs = max(0.0, float(half_spread or 0.0))
            side = "sell" if state.qty > 0 else "buy"
            px = (mid - hs) if side == "sell" else (mid + hs)
            qty = abs(state.qty)
            fd = local_book.apply_fill(symbol=state.symbol, side=side, qty=qty, fill_px=px,
                                       mid_px=mid, fee_rate=abs(taker_fee_bp) / 1e4,
                                       now_ts=now_ts)
            state.qty = local_book.qty(state.symbol)
            state.avg_px = 0.0
            state.avg_mid = 0.0
            state.opened_ts = 0.0
            state.last_ts = now_ts
            dec.fills.append(PlannedFill(
                symbol=state.symbol, side=side, qty=qty, px=px, mid=mid, ts=now_ts,
                is_flatten=True,
                spread_usd=float(fd.get("spread_usd") or 0.0),
                price_usd=float(fd.get("price_usd") or 0.0),
                fee_usd=float(fd.get("fee_usd") or 0.0),
                position_id=str(fd.get("position_id") or ""),
            ))
            dec.action = "flatten"
            dec.exit_path = "timeout_taker"            # [F340]

    # ②′ [F86 2026-09-14] 流向择时平仓：持仓已过半程且 **OFI 顺离场方向**
    # （多头遇买压=高位卖出、空头遇卖压=低位回补）⇒ 提前 taker 平仓。
    # 依据：平仓腿是最大成本项（实测均 -9.4bp、占 16-25%）；最优执行文献指出
    # 应「顺着订单流离场」而非固定时点。实证 OFI>+0.5 后下一期 +0.195bp、
    # OFI<-0.5 后 -0.257bp（86.5% 延续）——顺风离场可吃到这段漂移。
    _fl_th = float(getattr(limits, "ofi_flatten_threshold", 0.0) or 0.0)
    if (_fl_th > 0 and abs(state.qty) > 1e-12 and state.opened_ts > 0
            and (now_ts - state.opened_ts)
            > limits.max_one_side_seconds * float(
                getattr(limits, "ofi_flatten_min_age_ratio", 0.5) or 0.5)):
        _fl_side = "sell" if state.qty > 0 else "buy"
        _favorable = ((float(ofi) > _fl_th) if _fl_side == "sell"
                      else (float(ofi) < -_fl_th))
        if _favorable and float(getattr(limits, "ofi_flatten_maker_only", 0.0) or 0.0) > 0:
            # [h472 2026-09-29] **被动执行同一信号**：不穿价、不付 taker 费。
            #
            # 证据（h471）：本场馆 maker 费率 0，而近 12h 出场**全是 taker**、
            # 手续费占净亏 74%；ofi_flatten 单路径付 −3.53bp/腿费 + −1.19bp/腿穿价差，
            # 其价格项却是 +4.89bp/腿（顺风判断正确），且出场后 300s 离场方向
            # 有利漂移 +9.91bp（t=2.15，h470）⇒ 该时点挂被动减仓单本就有对手方流量。
            #
            # 机制复用（不新增执行路径）：置 `_tmo_add_block` ⇒ 下方
            # `check_side_allowed` 之后封**加仓侧**、**减仓侧存活**（F76 豁免），
            # 减仓侧由 ③ 的常规报价流程以地板单价挂出等对手方来接；
            # 若一直不成交，`timeout_hard_taker_sec`(300s) 合规硬顶兜底。
            _tmo_add_block = True
            _tmo_block_why = "ofi_flatten_maker"
            dec.skip = dec.skip or "ofi_flatten_maker"
            dec.exit_path = "ofi_flatten_maker"     # 未成交则不落腿，仅遥测
            # [h490] 落盘"放弃点"状态：宽限期反事实的唯一干净数据源
            persist_exit_probe(symbol=state.symbol, state=state, mid=mid,
                               kind="giveup", ofi=float(ofi or 0.0), now_ts=now_ts)
        elif _favorable:
            hs = max(0.0, float(half_spread or 0.0))
            px = (mid - hs) if _fl_side == "sell" else (mid + hs)
            qty = abs(state.qty)
            fd = local_book.apply_fill(symbol=state.symbol, side=_fl_side, qty=qty,
                                       fill_px=px, mid_px=mid,
                                       fee_rate=abs(taker_fee_bp) / 1e4, now_ts=now_ts)
            state.qty = local_book.qty(state.symbol)
            state.avg_px = state.avg_mid = state.opened_ts = 0.0
            state.last_ts = now_ts
            dec.fills.append(PlannedFill(
                symbol=state.symbol, side=_fl_side, qty=qty, px=px, mid=mid,
                ts=now_ts, is_flatten=True,
                spread_usd=float(fd.get("spread_usd") or 0.0),
                price_usd=float(fd.get("price_usd") or 0.0),
                fee_usd=float(fd.get("fee_usd") or 0.0),
                position_id=str(fd.get("position_id") or ""),
            ))
            dec.action = "flatten"
            dec.skip = "ofi_flatten"
            dec.exit_path = "ofi_flatten_taker"        # [F340]

    # ③ 重挂新单（含单侧许可）
    inv_ratio = (inv_ratio_hint if inv_ratio_hint is not None
                 else local_book.inv_ratio(state.symbol, mid, limit_notional))
    # [F80 2026-09-13] 冻结行情信号：近 frozen_lookback 期单步最大移动（bp）。
    # 单步指标对「微幅高频往返」敏感、对缓慢漂移不敏感——与「挂单能否被
    # 穿越」的真实成交条件对应（冻结日 2h 全幅 16bp 但单步 ≤2bp 即为例证）。
    _hist = state.mid_hist or []
    slow_move_bp = 0.0
    _lb = max(2, int(getattr(params, "frozen_lookback", 60) or 60))
    if len(_hist) >= _lb + 1:
        _h = _hist[-_lb - 1:]
        _maxmv = 0.0
        for _i in range(len(_h) - 1):
            if _h[_i] > 0 and _h[_i + 1] > 0:
                _mv = abs(_h[_i + 1] - _h[_i]) / _h[_i] * 1e4
                if _mv > _maxmv:
                    _maxmv = _mv
        slow_move_bp = _maxmv
    # [F204] 趋势反向偏斜用的"近期净移动"：与实证口径一致（回看 trend_skew_lookback 期，
    # 快照≈15s ⇒ 默认 60 期 ≈ 15 分钟）。k_trend=0 时 compute_quote 内部直接跳过 ✓。
    _trend_bp = trend_move_bp(
        state.mid_hist, int(getattr(params, "trend_skew_lookback", 60) or 60))
    # [F347 2026-09-23] side_mode="model"：方向判定改用 h300 学习工件。
    # 工件（lane_registry.meta.ai_model）= {features, weights, mu, sd, ic_val}。
    # v1 为单特征 r60（weight<0 = 逆 60s 趋势）：等价于 counter_trend 用 4 期回看 +
    # 工件阈值；工件缺失/特征不符/权重符号异常 → 静默回退 counter_trend 规则。
    _model_thr_bp = None
    if str(getattr(params, "side_mode", "") or "").lower() == "model":
        _am = ai_model or {}
        if _am and list(_am.get("features") or []) == ["r60"]:
            try:
                _w = float((_am.get("weights") or {}).get("r60", 0.0) or 0.0)
                _sd = float((_am.get("sd") or {}).get("r60", 1.0) or 1.0)
                _mu = float((_am.get("mu") or {}).get("r60", 0.0) or 0.0)
                if abs(_w) > 1e-9 and _sd > 0:
                    _r60 = trend_move_bp(state.mid_hist, 4)
                    # 模型方向 = 权重符号 × r60（bp 单位，兼容 compute_quote 的
                    # counter_trend 分支）：weight<0 = 逆 60s 趋势。拟合的线性标度
                    # 对方向无影响（只缩放），故用 ±r60 直接作趋势信号。
                    _trend_bp = -_r60 if _w < 0 else _r60
                    # [F349] θ 门槛 = model_thr_frac × sd（默认 0.25 ≈ 3.1bp；调小提频）
                    _model_thr_bp = float(getattr(limits, "model_thr_frac", 0.25) or 0.25) * _sd
            except Exception:
                _model_thr_bp = None
    _model_paused = False
    if _model_thr_bp is not None:
        # 工件有效：用工件阈值替代 side_trend_min_bp 的 0
        _m_bp = abs(float(_trend_bp or 0.0))
        if _m_bp < _model_thr_bp:
            # [h334 2026-09-26] 不再提前 return：改设暂停标记，稍后在 allow
            # 旗标上**库存感知**应用（有持仓只封加仓侧、减仓侧照常挂单）。
            # 病根：此前的提前 return 把减仓侧挂单一并停掉，慢行情里 r60 永远
            # < θ ⇒ 持仓永远等不到被动成交，实测悬挂 46min~1.5h（违反 30s-5min
            # 时域约定，用户实测发现）。
            _model_paused = True
    # ── [F280 2026-09-21] 把**真实价差**喂给报价器（价差相对挂宽）────────────────
    # 为什么必须传：`half_spread` 是 `plan_tick` 的现成入参（来自实盘 tick 的
    # best_bid/best_ask，见 `_collect_market_rows` 的 SQL），但历史上**只用于平仓腿**
    # （`(mid − hs)` / `(mid + hs)`），**从未进入报价公式** ✗。
    # 后果（H55 实测，Aster 32 币）：价差 p50 从 0.0124bp（BTC）到 20.19bp（VIRTUAL），
    # **跨度 1629 倍**，而挂宽是绝对 bp ⇒ 同一个宽度在 BTC 上是 **230 倍半价差**（打不到）、
    # 在 SEI 上是 **0.2 倍**（报价已在价差内，被逆选择）⇒ 32 币里 16 个"太贴"、3 个"太远"。
    # 传入后，`spread_mult > 0` 时挂宽 = spread_mult × 半价差，**按币自适应** ✓。
    # 同时把真实盘口传给不穿越钳制 —— H54 实测 δ 用绝对 bp 时 BTC 上穿越率 86.89% ✗✗。
    _hs = max(0.0, float(half_spread or 0.0))
    _spread_bp = (_hs * 2.0 / mid * 1e4) if mid > 0 else 0.0
    _bb = (mid - _hs) if (_hs > 0 and mid > _hs) else 0.0
    _ba = (mid + _hs) if _hs > 0 else 0.0
    # [h432] 持仓逆向漂移（bp，正=水下）——减仓侧偏斜消融（exit_skew_k）的输入。
    # 多头：mid < avg_mid 即水下；空头相反。无仓/avg 无效 ⇒ 0（compute_quote 内部跳过）。
    _adv_bp = 0.0
    if abs(state.qty) > 1e-12 and float(state.avg_mid or 0.0) > 0 and mid > 0:
        _drift = (mid - float(state.avg_mid)) / float(state.avg_mid) * 1e4
        _adv_bp = max(0.0, -_drift if state.qty > 0 else _drift)
    q = compute_quote(symbol=state.symbol, mid=mid, sigma_norm=sigma_norm,
                      inv_ratio=inv_ratio, slow_range_bp=slow_move_bp,
                      trend_bp=_trend_bp, spread_bp=_spread_bp,
                      adverse_bp=_adv_bp,
                      best_bid=_bb, best_ask=_ba, params=params)
    if q is None:
        dec.skip = "no_quote"
        if dec.action != "flatten":
            dec.action = "pause"
        return dec, {"book": local_book}

    marks = dict(marks) if marks else {state.symbol: mid}
    marks.setdefault(state.symbol, mid)
    if book is None:
        marks = {s: (mid if s == state.symbol else p.avg_mid or p.avg_px or mid)
                 for s, p in local_book.positions.items()}
        marks.setdefault(state.symbol, mid)

    # [§82 执行 2026-09-11 / 决策 P5-A —— 清单第 19 条] **车道级暂停闸**。
    # 此前 `check_lane_limits()` 生产调用点 = 0（AST 复核，`_audit_ml/Z222`）：
    # `toxic_streak`（连续逆选择暂停）在运行中的影子里**从未生效**，
    # `daily_loss_stop_pct`（日亏上限）更是**从未实现**（判定用消费方 0 个）。
    # 现在：由 `MM_LANE_LIMITS_ENFORCE` 控制（默认 false ⇒ 与旧行为逐字一致），
    # 打开后只执行**车道级**判据（权益/波动/毒性流/日亏）——
    # **绝不**把手伸到敞口判据：那会让"减仓腿"一起停掉，库存只能等超时砸单。
    # 位置刻意放在 ①′止损 / ②超时平仓 **之后** ⇒ 暂停永远不阻断已有库存的离场。
    # [F98] 开关：显式参数优先（回放/实验可强制开启），否则读环境变量（生产默认）
    if (lane_limits_enforce if lane_limits_enforce is not None
            else lane_limits_enforce_enabled()):
        # [h749 2026-10-03] 冷却式熔断状态机由 runner 持有:日亏闸改由 runner
        # 传入 `lane_pause_override`(含冷却截止时间,到点自动复开)。plan_tick
        # 内部只保留 equity/vol/toxic 判据,日亏判据**在 override 非空时让位**。
        if lane_pause_override:
            _lane_pause, _lane_why = True, lane_pause_override
        else:
            _lane_pause, _lane_why = lane_pause_reason(
                equity=equity, limits=limits, sigma_norm=sigma_norm,
                toxic_streak=state.toxic_streak, day_pnl_usd=day_pnl_usd,
            )
        if _lane_pause:
            dec.lane_pause = _lane_why
            dec.skip = _lane_why.split("(")[0]
            dec.skip_side = "both"
            if dec.action != "flatten":
                dec.action = "pause"
            state.quote_bid = state.quote_ask = state.quote_ts = 0.0
            # [F98 2026-09-14] **暂停必须自解除**：`toxic_streak` 只在成交判定里
            # 更新（成交才 +1 / 否则归零），而暂停会清掉挂单 ⇒ 不再有成交 ⇒ 计数
            # 永远卡在阈值上 ⇒ **整车道永久停摆**（谁打开 MM_LANE_LIMITS_ENFORCE
            # 谁中招，且表现为「策略突然不成交」）。语义上「暂停一拍」本身就意味着
            # 这一段毒性行情已经避开，重新武装计数即可（15s 粒度下暂停一拍=一个桶）。
            # 日亏闸不需要这样处理：它按 UTC 日读账本，跨日自然解除（这是它的设计）。
            if _lane_why.startswith("toxic_streak"):
                state.toxic_streak = 0
            return dec, {"book": local_book, "lane_pause": _lane_why}

    # ══ [F342 2026-09-22] 突发事件闸（暴涨/暴跌）═══════════════════════════
    # 用户要求建立「突发事件机制」。**刻意不预测方向**（理由见 core.sudden_move_hit
    # 的 docstring：实测事件后 fwd 从 −47 到 +43bp 全谱，方向不可预测；本会话
    # 已有 6 次"找方向信号"全部跨窗口翻转）。
    #
    # 位置：**在所有 exits 之后**（止损/止盈/超时/OFI 都已执行）、
    #       **在 lane gate 与报价流程之前** ⇒ 只拦"新挂单"，不阻断任何离场。
    #
    # 与既有闸门的区别（这是它存在的理由）：
    #   `trend_pause_bp` / `vol_pause_sigma` 建在 20 期窗口上 = 实测 **6.7 分钟**
    #   （20 × 18~20s tick 周期），而暴涨暴跌发生在**几秒到几十秒**内 ⇒ 来不及。
    #   本闸用 `sudden_move_k` 期（默认 1 期 ≈ 一个 tick）⇒ **快两个数量级**。
    _sm_bp = float(getattr(limits, "sudden_move_bp", 0.0) or 0.0)
    if _sm_bp > 0:
        _sm_k = int(getattr(limits, "sudden_move_k", 1) or 1)
        _hit, _mv = sudden_move_hit(state.mid_hist, _sm_bp, _sm_k)
        _cd = float(getattr(limits, "sudden_move_cooldown_sec", 0.0) or 0.0)
        _until = float(getattr(state, "sudden_move_until", 0.0) or 0.0)
        if _hit:
            state.sudden_move_until = max(_until, float(now_ts) + max(0.0, _cd))
            state.sudden_move_hits = int(getattr(state, "sudden_move_hits", 0) or 0) + 1
            _until = float(state.sudden_move_until)
        # ⚠️ 用 `>=` 而不是 `>`：`sudden_move_cooldown_sec=0` 时
        # `_until == now_ts`，用 `>` 会**恰好漏掉命中那一 tick**（实测踩到：
        # `state.sudden_move_hits` 已经是 1、`dec.skip` 却仍是空串）。
        # 语义上 `cooldown=0` = 「只暂停命中那一 tick」⇒ 必须含等号。
        if _until >= float(now_ts) and _until > 0:
            dec.skip = "sudden_move"
            dec.skip_side = "both"
            dec.skip_detail = f"mv={_mv:+.1f}bp thr={_sm_bp:.0f} k={_sm_k} cd_left={_until-now_ts:.0f}s"
            # ⚠️ **只停新挂单，不清空 `state.quote_*`**。
            # 原因：清空会让 `_lagged_quote` 记零单标记 ⇒ **已有的减仓侧挂单
            # 立刻失去被动成交机会** ⇒ 库存只能等超时/止损 taker 砸单，
            # 而 taker 正好付在"急动后最宽的点差"上（4bp fee + 过价）。
            # 保留挂单的代价是老价被吃到（有 `max_quote_age_sec=90` 兜底 → 变
            # `stale_quote_cleared`）⇒ **90 秒后自然失效**，比主动砸单便宜一个量级。
            if dec.action != "flatten":
                dec.action = "pause"
            return dec, {"book": local_book, "sudden_move": dec.skip_detail}

    # [h624/A4] 跳价暂停：单步 |Δmid| ≥ jump_pause_bp ⇒ 双边撤新挂 N 秒
    _jpb = float(getattr(limits, "jump_pause_bp", 0.0) or 0.0)
    if _jpb > 0:
        from backend.services.market_maker.markout import jump_pause_hit
        _jps = float(getattr(limits, "jump_pause_sec", 0.0) or 0.0)
        _jp_until = float(getattr(state, "jump_pause_until", 0.0) or 0.0)
        if jump_pause_hit(state.mid_hist, thresh_bp=_jpb, lookback=1):
            state.jump_pause_until = max(_jp_until, float(now_ts) + max(0.0, _jps))
            _jp_until = float(state.jump_pause_until)
            GATE_PROBES["jump_pause_hit"] = GATE_PROBES.get("jump_pause_hit", 0) + 1
        if _jp_until >= float(now_ts) and _jp_until > 0:
            dec.skip = "jump_pause"
            dec.skip_side = "both"
            dec.skip_detail = (
                f"thr={_jpb:.0f}bp cd_left={_jp_until - float(now_ts):.0f}s")
            GATE_PROBES["jump_pause_blocked"] = (
                GATE_PROBES.get("jump_pause_blocked", 0) + 1)
            if dec.action != "flatten":
                dec.action = "pause"
            return dec, {"book": local_book, "jump_pause": dec.skip_detail}

    # [F71b] 波动状态闸门：高波动时平仓成本吞掉价差 → 暂停该币
    vol_paused, vol_cur = vol_regime_blocked(
        state.mid_hist, state.vol_baseline_bp, limits.vol_pause_mult,
        limits.vol_window)
    dec.vol_bp = round(vol_cur, 3)
    if vol_paused:
        dec.action, dec.skip, dec.skip_side = "pause", "vol_regime", "both"
        state.quote_bid = state.quote_ask = state.quote_ts = 0.0
        return dec, {"book": local_book}

    # [F278 2026-09-16] 步长/最小名义合规：腿量按交易所步长向下取整后必须可下单。
    # 否则纸面会记录"实盘必被拒"的成交（BTC $30 腿 = 0.000396 BTC 非 0.001 整数倍 ✗）。
    _leg_ok, _leg_qty, _leg_why = leg_qty_compliant(
        state.symbol, _eff_notional, mid)
    if not _leg_ok:
        dec.action, dec.skip, dec.skip_side = "pause", "below_step", "both"
        dec.skip_detail = _leg_why
        state.quote_bid = state.quote_ask = state.quote_ts = 0.0
        return dec, {"book": local_book}

    # [F274 2026-09-16 · E2'] 持续性单边流闸：连续 N 桶同向主动流 ⇒ 该币站开。
    # 依据：单桶 OFI 闸（F86）只看一拍，而真正的趋势推进是**连续同向**的
    # （09-15 夜"慢漂移连续吃挂单"的形态）；站开比只封一侧更彻底。
    _fp_n = int(getattr(limits, "flow_persist_pause", 0) or 0)
    if _fp_n > 0 and abs(float(flow_streak or 0.0)) >= _fp_n:
        dec.action, dec.skip, dec.skip_side = "pause", "flow_persist", "both"
        state.quote_bid = state.quote_ask = state.quote_ts = 0.0
        return dec, {"book": local_book}

    allow_buy, why_buy = check_side_allowed(
        symbol=state.symbol, side="buy", book=local_book, marks=marks, equity=equity,
        add_notional=_eff_notional, limits=limits, now_ts=now_ts, sigma_norm=sigma_norm,
        # [F94] 在挂同向腿的最坏情形预留（见 core.check_side_allowed docstring）
        pending_up_usd=float(_pend.get("up") or 0.0),
        pending_down_usd=float(_pend.get("down") or 0.0),
        pending_gross_usd=float(_pend.get("gross") or 0.0))
    allow_sell, why_sell = check_side_allowed(
        symbol=state.symbol, side="sell", book=local_book, marks=marks, equity=equity,
        add_notional=_eff_notional, limits=limits, now_ts=now_ts, sigma_norm=sigma_norm,
        pending_up_usd=float(_pend.get("up") or 0.0),
        pending_down_usd=float(_pend.get("down") or 0.0),
        pending_gross_usd=float(_pend.get("gross") or 0.0))
    # [h413] maker-only 超时的"停止加仓"接线：超时仓封锁加仓侧
    # （多头加仓=买、空头加仓=卖），减仓侧豁免（F76）。
    if _tmo_add_block:
        _pos_tmo = local_book.qty(state.symbol)
        if _pos_tmo > 1e-12 and allow_buy:
            allow_buy = False
            why_buy = why_buy or _tmo_block_why
        elif _pos_tmo < -1e-12 and allow_sell:
            allow_sell = False
            why_sell = why_sell or _tmo_block_why

    # [h334 2026-09-26] 模型门库存感知应用：有持仓只封加仓侧，减仓侧必须存活。
    # 空仓时两侧全封 ⇒ 走下方 1535 行的统一 pause（与原提前 return 行为一致）。
    if _model_paused:
        _pos_m = local_book.qty(state.symbol)
        if _pos_m > 1e-12:
            if allow_buy:
                allow_buy, why_buy = False, "model_below_thr"
        elif _pos_m < -1e-12:
            if allow_sell:
                allow_sell, why_sell = False, "model_below_thr"
        else:
            if allow_buy:
                allow_buy, why_buy = False, "model_below_thr"
            if allow_sell:
                allow_sell, why_sell = False, "model_below_thr"

    # [h324 2026-09-26] 趋势闸门：单边行情里禁止**逆势侧**加仓
    # （下跌禁买 = 别逆势接刀 ✗、上涨禁卖 = 别逆势做空 ✗）。
    # F204 曾翻成"禁止顺势侧"，依据是 2915 笔账本分组（顺势 −4.05 / 逆势 +2.11）；
    # h324 用三重复测翻回：逐笔 markout 4/4 日窗一致（加仓腿顺势侧 +4.3~+9.8bp
    # vs 逆势侧 −0.5~−3.1bp）+ h284 带闸门实现盈亏（168h/48h：封逆势 −0.842/−0.868
    # vs 封顺势 −0.885/−1.073 vs 无闸 −0.897/−1.004）。详见 core.trend_blocked_side 文档。
    blocked = trend_blocked_side(state.mid_hist, limits.trend_pause_bp,
                                 limits.trend_lookback,
                                 mode=float(getattr(limits, "trend_block_mode", 0.0)
                                            or 0.0))
    # [h711 2026-10-02] **趋势闸滞后**:慢牛/慢熊里 5 分钟口径会闪烁——实测
    # 14-16h 全体币 +108~311bp 的慢牛中,trend_h697_both 拦了 94.8% tick,
    # 但剩余 ~5% 的"闪烁窗口"里逆势卖腿照样进场,而这些漏网腿每腿 −2.89bp
    # (买腿同期 +0.61bp)。检测到趋势后,在 None 的闪烁期仍保持封锁
    # `trend_block_min_hold_sec`(0=关)。只延长**已出现过的**封锁,不会凭空新增。
    _tbh_hold = float(getattr(limits, "trend_block_min_hold_sec", 0.0) or 0.0)
    if _tbh_hold > 0:
        if blocked:
            _TREND_BLOCK_HOLD[state.symbol] = (now_ts, blocked)
        else:
            _prev_hold = _TREND_BLOCK_HOLD.get(state.symbol)
            if _prev_hold and (now_ts - _prev_hold[0]) < _tbh_hold:
                blocked = _prev_hold[1]
            else:
                _TREND_BLOCK_HOLD.pop(state.symbol, None)
    # [F76 2026-09-12] 库存感知：趋势闸只封锁**加仓侧**。
    # 减仓侧在趋势里成交对持仓是**有利**的（多头在上涨中高价卖出、空头在
    # 下跌中低价回补）——此前减仓侧一并被封死，库存只能等超时 taker 平仓
    # （实盘平仓均价 -12.98bp，是亏损主因）。空仓时两侧都是加仓，语义不变。
    # 注：这条豁免与 F204 的符号修正**正交** ✓ —— 反向之后它依旧正确：
    #   多头 + 上涨 ⇒ blocked="buy"（加仓侧，不豁免）⇒ 仍封 ✗ ✓
    #   空头 + 上涨 ⇒ blocked="buy"（回补=减仓侧）⇒ 豁免 ✓
    # [h697] "both" = 涨势双边全封,但减仓侧仍豁免(否则库存卡死,F76 语义)。
    # [h734 R2b 2026-10-03] **概率调制模式(trend_block_mode>=3 且
    # trend_soft_size_mult>0)**:不再封侧,只把被拦侧加仓腿量 × soft 乘子
    # (永不归零)。病根(用户现场"交易几分钟就停止"):硬开关在慢牛/单边流里
    # 把 83% tick 判成无单,市场 660 笔/时我方 0 腿;而影子数据显示方向信号
    # 只有 3~5pt(≈50%)⇒ 正确响应是减量不是封死。
    _tsm = _tsm  # [h736] 早段已定义(见函数顶部),此处保持引用
    _tbm = _tbm
    # [h740] 闸门段起点:清空本 tick 软标记,下面各闸门在软模式下逐步累加。
    state.soft_side = ""

    def _soft_mark(_side: str) -> None:
        _cur = str(state.soft_side or "")
        state.soft_side = ("both" if (_cur and _cur != _side) else _side)
    # [h735/h736 2026-10-03] 软模式:不封侧、不在此缩放 fills(缩放已在 fill 段
    # 记账前完成,book 与账本同源,不再分叉);这里只需把 blocked 置空绕过硬封锁。
    if blocked and _soft_mode:
        state.soft_side = str(blocked)   # [h740] 软标记(buy/sell/both)
        blocked = ""        # 软模式不进下方硬封锁
    # [h752 C1a] 资金偏斜软闸(加密特化):极端 funding = 拥挤头寸信号。
    # 正 funding(多头拥挤)⇒ 买腿缩量;负 funding ⇒ 卖腿缩量。
    # 软模式下用软标记(概率调制);非软模式维持观察(不改变旧行为)。
    _fst = float(getattr(limits, "funding_skew_thresh", 0.0) or 0.0)
    if _fst > 0 and abs(float(funding_rate or 0.0)) >= _fst and _soft_mode:
        _crowd = "buy" if float(funding_rate) > 0 else "sell"
        _soft_mark(_crowd)
    if blocked:
        _pos = local_book.qty(state.symbol)
        if blocked == "both":
            if _pos > 1e-12:
                blocked = "buy"      # 多头:卖是减仓侧,放行;封加仓买
            elif _pos < -1e-12:
                blocked = "sell"     # 空头:买是减仓侧,放行;封加仓卖
            # 空仓:保持 "both"(双边都是加仓,全封)
        elif _pos > 1e-12 and blocked == "sell":
            blocked = ""          # 多头减仓侧（卖），放行
        elif _pos < -1e-12 and blocked == "buy":
            blocked = ""          # 空头减仓侧（买），放行
    if blocked == "buy" and allow_buy:
        allow_buy, why_buy = False, ("trend_h697_buy" if
                                     float(getattr(limits, "trend_block_mode", 0.0)
                                           or 0.0) >= 2.0 else "trend_down")
    elif blocked == "sell" and allow_sell:
        allow_sell, why_sell = False, ("trend_h697_sell" if
                                       float(getattr(limits, "trend_block_mode", 0.0)
                                             or 0.0) >= 2.0 else "trend_up")
    elif blocked == "both":
        if allow_buy:
            allow_buy, why_buy = False, "trend_h697_both"
        if allow_sell:
            allow_sell, why_sell = False, "trend_h697_both"

    # [h622/h623] 大波动停加仓：绝对下限 + 该币自己的 |r300| 分位数。
    # 全局固定 30bp 会把正常山寨一起停光；分位数只在该币自己的尾部触发。
    _tab = float(getattr(limits, "trend_add_block_bp", 0.0) or 0.0)
    _tab_q = float(getattr(limits, "trend_add_block_q", 0.0) or 0.0)
    if _tab > 0 or _tab_q > 0:
        _tab_thr = _tab
        if _tab_q > 0:
            _ah_tab = list(getattr(state, "ar300_hist", None) or [])
            # [h709 2026-10-02] **样本门槛 60→120**:样本不足时节点的分位数没有
            # 统计意义(换币后新币只有几条样本 ⇒ q90 极小)。
            if len(_ah_tab) >= 120:
                _srt_tab = sorted(_ah_tab)
                _q_tab = min(max(_tab_q, 0.0), 0.99)
                _tab_thr = max(
                    _tab_thr,
                    _srt_tab[min(len(_srt_tab) - 1, int(len(_srt_tab) * _q_tab))],
                )
        # [h709] **阈值下限**:自适应门槛在退化分布下会把加仓全部封死。
        # 实测(10-02 13:3x)`trend_add_block` 90 秒触发 214 次、90 秒 0 新报价,
        # 腿速从 ~40/h 掉到 ~8/h —— 正是"换币后币龄小、历史样本少"的时刻。
        # 下限保证"正常波动不会被当成尾部"(山寨 300s 常规波动 ≈ 5-15bp)。
        _tab_min = float(getattr(limits, "trend_add_block_min_bp", 8.0) or 0.0)
        _tab_thr = max(_tab_thr, _tab_min)
        if _tab_thr > 0 and adds_blocked_by_trend(
                state.mid_hist, _tab_thr, limits.trend_lookback):
            _pos_tab = local_book.qty(state.symbol)
            if _pos_tab > 1e-12:
                if allow_buy:
                    allow_buy, why_buy = False, "trend_add_block"
            elif _pos_tab < -1e-12:
                if allow_sell:
                    allow_sell, why_sell = False, "trend_add_block"
            else:
                if allow_buy:
                    allow_buy, why_buy = False, "trend_add_block"
                if allow_sell:
                    allow_sell, why_sell = False, "trend_add_block"

    # [h622 废止 2026-09-29] 不再用 entry_block_symbols 永久禁开仓。
    #
    # 事故：选币把 BNB 放进当前宇宙后，名单仍只减仓 ⇒ 选出的币开不了仓，车道停摆。
    # 淘汰只允许发生在选币结果：不在本轮 `symbols` 里 = 不进交易序列。
    # 已经在序列里的币必须能开仓。字段保留兼容，此处不再读取。

    # [h625] 20 档价差选档：够宽才新开。没有深度不干预。减仓侧照旧。
    _bs_min = float(getattr(limits, "book_slot_min_bp", 0.0) or 0.0)
    if _bs_min > 0:
        _bs_ok, _bs_until, _bs_open = book_slot_decision(
            spread_bp=depth_spread_bp,
            min_bp=_bs_min,
            now_ts=float(now_ts),
            until=float(getattr(state, "book_slot_until", 0.0) or 0.0),
            is_open=bool(getattr(state, "book_slot_open", True)),
            dwell_sec=float(getattr(limits, "book_slot_dwell_sec", 180.0) or 180.0),
        )
        state.book_slot_until = _bs_until
        state.book_slot_open = _bs_open
        if not _bs_ok:
            _pos_bs = local_book.qty(state.symbol)
            _bs_fired = False
            GATE_PROBES["book_slot_hit"] = GATE_PROBES.get("book_slot_hit", 0) + 1
            if _pos_bs > 1e-12:
                if allow_buy:
                    allow_buy, why_buy = False, "book_tight"
                    _bs_fired = True
            elif _pos_bs < -1e-12:
                if allow_sell:
                    allow_sell, why_sell = False, "book_tight"
                    _bs_fired = True
            else:
                if allow_buy:
                    allow_buy, why_buy = False, "book_tight"
                    _bs_fired = True
                if allow_sell:
                    allow_sell, why_sell = False, "book_tight"
                    _bs_fired = True
            if _bs_fired:
                GATE_PROBES["book_slot_blocked"] = GATE_PROBES.get("book_slot_blocked", 0) + 1
                if not any(f.is_flatten for f in dec.fills):
                    dec.skip = dec.skip or "book_tight"

    # [h626] 方向卡：每 5 分钟重判，只停点名的那一边加仓。减仓照旧。
    # [h759 2026-10-03 **实时淘汰快路**] 用户指出"淘汰还要等周期":雷达换币是
    # 15 分钟级(宇宙变更+驻留保护的固有节奏),但**止损式的风险不该等**。
    # 新增:命中亏损淘汰判据的币,在**下一个 tick**(≤60s 刷新)立刻只减不加:
    #   · 空仓 ⇒ 双边停挂(不再开新仓)
    #   · 多头 ⇒ 只留卖(减仓侧)
    #   · 空头 ⇒ 只留买(回补侧)
    # 雷达随后在 ≤15 分钟内完成正式换币(宇宙层面的替换)。
    if decay_blocked and dec.action != "flatten":
        _pos_db = local_book.qty(state.symbol)
        if abs(_pos_db) <= 1e-12:
            if allow_buy or allow_sell:
                allow_buy, allow_sell = False, False
                dec.skip = dec.skip or "decay_blocked(flat)"
                dec.skip_side = "both"
        elif _pos_db > 1e-12:
            if allow_buy:
                allow_buy, why_buy = False, "decay_blocked(long)"
                dec.skip = dec.skip or "decay_blocked"
        else:
            if allow_sell:
                allow_sell, why_sell = False, "decay_blocked(short)"
                dec.skip = dec.skip or "decay_blocked"
    _blk = str(block_add_side or "").lower()
    if _blk in ("buy", "sell"):
        _pos_d = local_book.qty(state.symbol)
        _is_add = (
            abs(_pos_d) <= 1e-12
            or (_pos_d > 1e-12 and _blk == "buy")
            or (_pos_d < -1e-12 and _blk == "sell")
        )
        if _is_add:
            GATE_PROBES["direction_hit"] = GATE_PROBES.get("direction_hit", 0) + 1
            if _blk == "buy" and allow_buy:
                allow_buy, why_buy = False, "direction_buy"
                GATE_PROBES["direction_blocked"] = GATE_PROBES.get("direction_blocked", 0) + 1
                if not any(f.is_flatten for f in dec.fills):
                    dec.skip = dec.skip or "direction_buy"
            elif _blk == "sell" and allow_sell:
                allow_sell, why_sell = False, "direction_sell"
                GATE_PROBES["direction_blocked"] = GATE_PROBES.get("direction_blocked", 0) + 1
                if not any(f.is_flatten for f in dec.fills):
                    dec.skip = dec.skip or "direction_sell"

    # [h623] 库存占比到阈值 ⇒ 停加仓侧。盘口钳制会把绝对偏斜的加仓侧拉回最优价，
    # 不显式撤单就会继续贴盘口接单。
    _iabr = float(getattr(limits, "inv_add_block_ratio", 0.0) or 0.0)
    if _iabr > 0 and abs(float(inv_ratio or 0.0)) >= _iabr:
        _pos_ia = local_book.qty(state.symbol)
        if _pos_ia > 1e-12:
            if allow_buy:
                allow_buy, why_buy = False, "inv_add_block"
        elif _pos_ia < -1e-12:
            if allow_sell:
                allow_sell, why_sell = False, "inv_add_block"

    # [h897 激进加仓·只加赢家] 用户:"发现买入点就买入"。加仓上限已放开
    # (inv_add_block_ratio 0.05 ⇒ 0.5,约 3 腿/币),但**摊平亏损单除外**:
    # 持仓浮亏超过 MM_ADD_MAX_UNDERWATER_BP(默认 8bp)⇒ 不加。
    # 亏损加仓(摊平)是 h623 库存堆积大亏的根因;激进要加在赢家/平价单上,
    # 输家交给离场阶梯(maker_risk/taker_stop)处理。
    try:
        _uw_max = float(os.getenv("MM_ADD_MAX_UNDERWATER_BP", "8") or 8.0)
    except Exception:
        _uw_max = 8.0
    try:
        from backend.services.market_maker.core import (
            add_blocked_underwater as _abu)
        _pos_uw = local_book.qty(state.symbol)
        if _abu(_pos_uw, float(getattr(state, "avg_mid", 0.0) or 0.0),
                float(mid or 0.0), _uw_max):
            if _pos_uw > 1e-12 and allow_buy:
                allow_buy, why_buy = False, "add_underwater"
            elif _pos_uw < -1e-12 and allow_sell:
                allow_sell, why_sell = False, "add_underwater"
    except Exception:
        pass

    # [h624/A2] markout 毒性停加仓：样本够且 m+cap < halt_bp（window_n>0 启用）
    _mk_wn = int(getattr(limits, "markout_window_n", 0) or 0)
    _mk_snap = symbol_markout_snapshot(state, min_n=max(1, _mk_wn or 10))
    if _mk_wn > 0:
        from backend.services.market_maker.markout import markout_halts_adds
        if markout_halts_adds(
                markout_bp=float(_mk_snap["m30"]),
                capture_bp=float(_mk_snap["cap"]),
                n=float(_mk_snap["n"]),
                min_n=_mk_wn,
                halt_thresh_bp=float(getattr(limits, "markout_halt_bp", 0.0) or 0.0)):
            _pos_mk = local_book.qty(state.symbol)
            _mk_fired = False
            GATE_PROBES["markout_halt_hit"] = GATE_PROBES.get("markout_halt_hit", 0) + 1
            if _pos_mk > 1e-12:
                if allow_buy:
                    allow_buy, why_buy = False, "markout_halt"
                    _mk_fired = True
            elif _pos_mk < -1e-12:
                if allow_sell:
                    allow_sell, why_sell = False, "markout_halt"
                    _mk_fired = True
            else:
                if allow_buy:
                    allow_buy, why_buy = False, "markout_halt"
                    _mk_fired = True
                if allow_sell:
                    allow_sell, why_sell = False, "markout_halt"
                    _mk_fired = True
            if _mk_fired:
                GATE_PROBES["markout_halt_blocked"] = (
                    GATE_PROBES.get("markout_halt_blocked", 0) + 1)
                if not any(f.is_flatten for f in dec.fills):
                    dec.skip = dec.skip or "markout_halt"

    # [h624/A3] 盈亏平衡开仓闸：be_mult>0 启用；捕获 < be×mult ⇒ 停加仓
    _bem = float(getattr(limits, "be_mult", 0.0) or 0.0)
    if _bem > 0:
        from backend.services.market_maker.markout import (
            break_even_bp as _be_bp, break_even_blocks_add as _be_blocks,
        )
        _be = _be_bp(
            rolling_markout_bp=float(_mk_snap["m30"]),
            stop_share=float(getattr(state, "stop_share_hat", 0.0) or 0.0),
            taker_fee_bp=float(getattr(limits, "be_taker_fee_bp", 4.0) or 4.0),
        )
        _exp_cap = abs(float(half_spread or 0.0)) / float(mid) * 1e4 if mid > 0 else 0.0
        if _exp_cap <= 0 and float(_mk_snap["cap"] or 0.0) > 0:
            _exp_cap = float(_mk_snap["cap"])
        if _be_blocks(expected_capture_bp=_exp_cap, be_bp=_be, be_mult=_bem):
            _pos_be = local_book.qty(state.symbol)
            _be_fired = False
            GATE_PROBES["break_even_hit"] = GATE_PROBES.get("break_even_hit", 0) + 1
            if _pos_be > 1e-12:
                if allow_buy:
                    allow_buy, why_buy = False, "break_even_block"
                    _be_fired = True
            elif _pos_be < -1e-12:
                if allow_sell:
                    allow_sell, why_sell = False, "break_even_block"
                    _be_fired = True
            else:
                if allow_buy:
                    allow_buy, why_buy = False, "break_even_block"
                    _be_fired = True
                if allow_sell:
                    allow_sell, why_sell = False, "break_even_block"
                    _be_fired = True
            if _be_fired:
                GATE_PROBES["break_even_blocked"] = (
                    GATE_PROBES.get("break_even_blocked", 0) + 1)
                if not any(f.is_flatten for f in dec.fills):
                    dec.skip = dec.skip or "break_even_block"

    # [R201 2026-09-29] **饱和要求闸**（用户裁决 A：忠实实现 h483 的"要求"口径）。
    #
    # 为什么必须新加：现有两个 OFI 闸都是**禁令**（`|ofi| > θ ⇒ 封逆势侧`，调高=放松），
    # 而 h483 的样本外结论是**要求**（只保留 `fo ≥ θ` 的腿：+1.91bp、腿量 ×0.92、两半复现）
    # ⇒ 方向相反、现成参数一个都实现不了它 ✗（详见 `核心库 core.LaneRiskLimits` 的字段注释）。
    #
    # 语义：`|ofi| < θ` ⇒ **不建仓**（空仓时买卖两侧都是加仓 ⇒ 两侧都封）；
    #       **减仓侧永不受限**（F76：封减仓会让库存只能等超时 taker 平，是历史亏损主因 ✗）。
    # 位置：放在**趋势闸之后**，这样趋势闸的计数（`trend_up/trend_down`）不被我抢先污染 ✓
    #       —— 代价是我的标签可能被它抢先，故**另记一个不被遮蔽的探针计数** ✓（R201 的教训：
    #       "闸门在动"必须能独立观测，不能只看 `dec.skip` ⇒ 见 `self.gate_probe_counts`）。
    # 默认 0 ⇒ 关闭 ⇒ 与改动前逐字一致 ✓。
    _req_th = float(getattr(limits, "ofi_require_threshold", 0.0) or 0.0)
    if _req_th > 0 and abs(float(ofi or 0.0)) < _req_th:
        _pos_req = local_book.qty(state.symbol)
        _blocked_req = []
        # 加仓侧：多头=买、空头=卖、空仓=两侧
        if _pos_req > 1e-12:
            _blocked_req = ["buy"]
        elif _pos_req < -1e-12:
            _blocked_req = ["sell"]
        else:
            _blocked_req = ["buy", "sell"]
        _fired = False
        for _sd in _blocked_req:
            if _sd == "buy" and allow_buy:
                allow_buy, why_buy = False, f"ofi_require({float(ofi):+.2f})"
                _fired = True
            elif _sd == "sell" and allow_sell:
                allow_sell, why_sell = False, f"ofi_require({float(ofi):+.2f})"
                _fired = True
        # 探针：**无论是否被更早的闸抢先**都记一次（这才是"该闸在动"的可靠证据 ✓）
        # ⚠️ 走**模块级** `GATE_PROBES`（`plan_tick` 是模块级函数，没有 `self` ✗）
        GATE_PROBES["ofi_require_hit"] = GATE_PROBES.get("ofi_require_hit", 0) + 1
        if _fired:
            GATE_PROBES["ofi_require_blocked"] = GATE_PROBES.get("ofi_require_blocked", 0) + 1
            if not any(f.is_flatten for f in dec.fills):
                dec.skip = dec.skip or "ofi_require"

    # [h354 2026-09-27] P2 形态（VWAP 回归）：|现价 − 60s VWAP| ≥ vwap_revert_bp
    # ⇒ 只挂向 VWAP 回归的一侧（偏离上方→只挂卖、下方→只挂买），减仓侧豁免（F76）。
    # 这是"实盘形态试跑框架"的第 1 个受试形态（h350 事件研究 +0.57bp@120s, t=2.2）。
    _vw_bp = float(getattr(limits, "vwap_revert_bp", 0.0) or 0.0)
    if _vw_bp > 0 and float(vwap60 or 0.0) > 0 and mid > 0:
        _dev_vw = (mid - float(vwap60)) / float(vwap60) * 1e4
        if abs(_dev_vw) >= _vw_bp:
            _pos_vw = local_book.qty(state.symbol)
            _ofi_vw = float(ofi or 0.0)
            # [h359] P2 v2：流驱动偏离（OFI 继续推离 VWAP）⇒ 回归侧也封，不 fade 流。
            # h358：OFI 推离时回归侧 f30 −0.79(t=−23.9)；OFI 回归时 +0.79(t=16.1)。
            _vfb = float(getattr(limits, "vwap_flow_block", 0.0) or 0.0)
            if _dev_vw > 0:      # 价在 VWAP 上方 → 封买（回归=向下）
                if allow_buy and not (_pos_vw < -1e-12):
                    allow_buy, why_buy = False, "vwap_revert_up"
                # 买流在推离 ⇒ 卖（回归侧）也被 fade 流 ⇒ 封（多头减仓豁免）
                if (_vfb > 0 and _ofi_vw > _vfb and allow_sell
                        and not (_pos_vw > 1e-12)):
                    allow_sell, why_sell = False, f"vwap_flow_away_buy({_ofi_vw:+.2f})"
                    if not any(f.is_flatten for f in dec.fills):
                        dec.skip = dec.skip or "vwap_flow_away_buy"
            else:                # 价在 VWAP 下方 → 封卖（回归=向上）
                if allow_sell and not (_pos_vw > 1e-12):
                    allow_sell, why_sell = False, "vwap_revert_down"
                # 卖流在推离 ⇒ 买（回归侧）也被 fade 流 ⇒ 封（空头回补豁免）
                if (_vfb > 0 and _ofi_vw < -_vfb and allow_buy
                        and not (_pos_vw < -1e-12)):
                    allow_buy, why_buy = False, f"vwap_flow_away_sell({_ofi_vw:+.2f})"
                    if not any(f.is_flatten for f in dec.fills):
                        dec.skip = dec.skip or "vwap_flow_away_sell"

    # [h333 2026-09-26] 平缓时段全停：|近 trend_lookback 期趋势| < trend_only_bp
    # ⇒ 暂停全部加仓侧（减仓侧豁免，与上方 F76 语义一致）。依据：h323 真实成交
    # markout（96h/24h，中位数口径）——强趋势顺势腿 mk30 +4.4~+5.3bp，其余 90%
    # 成交 −1.4~−2.9bp。空仓时两侧全封 = 只在强趋势开新腿。
    #
    # [h454 2026-09-28] **自适应形式**：h450/h451 实测 |r300| 越大边际越强
    # （≥30bp 段 +2.52bp, t=14.7, OOS 双窗同号；≥40bp 段 +3.11bp），但**固定绝对
    # 阈值在安静时段会把频率打到 0**（h452 实测近 10min 0 腿 ⇒ 破 ≥60/h 硬约束 ✗）。
    # ⇒ 门槛改为"近 1h |r300| 分布的分位数"：`trend_only_q`（如 0.75 = 只做最强的
    # 25% 时段），绝对下限仍由 `trend_only_bp` 兜底（floor）。这样通过率与市场活跃度
    # 无关（始终约 1−q），频率不会因低波动而崩塌 ✓。
    _tonly = float(getattr(limits, "trend_only_bp", 0.0) or 0.0)
    _tonly_q = float(getattr(limits, "trend_only_q", 0.0) or 0.0)
    if _tonly > 0 or _tonly_q > 0:
        _tmv = trend_move_bp(state.mid_hist, int(getattr(limits, "trend_lookback", 20) or 20))
        _thr = _tonly
        if _tonly_q > 0:
            _ah = list(getattr(state, "ar300_hist", None) or [])
            if len(_ah) >= 60:
                _srt = sorted(_ah)
                _q = min(max(_tonly_q, 0.0), 0.99)
                _thr = max(_thr, _srt[min(len(_srt) - 1, int(len(_srt) * _q))])
        if abs(_tmv) < _thr:
            _pos = local_book.qty(state.symbol)
            # [h334b 修正] 多头：买=加仓侧→封、卖=减仓侧→保留；空头反之；
            # 空仓：两侧都是加仓→全封。（上一版条件写反，把减仓侧封了）
            if _pos > 1e-12:
                if allow_buy:
                    allow_buy, why_buy = False, "trend_only_flat"
            elif _pos < -1e-12:
                if allow_sell:
                    allow_sell, why_sell = False, "trend_only_flat"
            else:
                if allow_buy:
                    allow_buy, why_buy = False, "trend_only_flat"
                if allow_sell:
                    allow_sell, why_sell = False, "trend_only_flat"

    # [h338 2026-09-26] VPIN 毒性暂停：滚动 20 桶 |OFI| 均值 > 阈值 ⇒ 暂停全部
    # 加仓侧（减仓侧豁免）。依据：h333 实测 4,835 笔——低 VPIN 环境 mk30 −0.014bp
    # （逆选择≈0）、高 VPIN −0.482bp、斜率 −1.27bp/单位VPIN。文献：ELO(2012) VPIN。
    _vpin_thr = float(getattr(limits, "vpin_pause_threshold", 0.0) or 0.0)
    if _vpin_thr > 0:
        _oh = state.ofi_abs_hist or []
        if len(_oh) >= 20 and (sum(_oh) / len(_oh)) > _vpin_thr:
            _pos_v = local_book.qty(state.symbol)
            if _pos_v > 1e-12:
                if allow_buy:
                    allow_buy, why_buy = False, "vpin_high"
            elif _pos_v < -1e-12:
                if allow_sell:
                    allow_sell, why_sell = False, "vpin_high"
            else:
                if allow_buy:
                    allow_buy, why_buy = False, "vpin_high"
                if allow_sell:
                    allow_sell, why_sell = False, "vpin_high"

    # [F217 2026-09-15] **侧选择**（结构性旋钮）：`side_mode="counter_trend"` 时，
    # **只**在逆势侧挂单（涨了只挂卖 = 卖 rip ✓、跌了只挂买 = 买 dip ✓）。
    # 证据链（全部修复后模型/账本实测）：
    #   · 被动入场腿的逆向选择 ≈ −0.84bp 是全部剩余亏损（F216，平仓腿已归零 ✓）；
    #   · 逐笔 markout：买腿 30 分钟转正 +1.90bp ✓、卖腿 −4.51 ✗（F198b）；
    #   · 顺势/逆势分组：逆势 +2.11bp、顺势 −4.05bp（F199，事后口径 ⇒ 只能当方向
    #     参考 ✗）；宽度不对称版（k_trend）已在修复后模型上否掉（F215① 劣 ✓）
    #     ⇒ 硬性侧选择是剩下的唯一"改入场符号"的杠杆 ✓。
    # 与趋势闸（trend_pause_bp）的区别：它是**不对称禁令**（只禁顺势侧、逆势侧仍与
    # 对侧共存）；本旋钮是**只留逆势侧**（更彻底 ✓）。`side_trend_min_bp` 是触发下限：
    # |趋势| 低于它时仍双边挂（避免噪声把车道切成单边 ✗）。
    _side_mode = str(getattr(params, "side_mode", "both") or "both")
    if _side_mode == "counter_trend":
        _min_bp = float(getattr(params, "side_trend_min_bp", 0.0) or 0.0)
        # [F228 2026-09-15 **设计缺陷根治**] 必须补上 F76 的「减仓侧永不受限」豁免。
        # 现场（22:30~22:47 账本逐笔）：单边下跌里多头 SOL/BNB 被**连买 57 笔、零卖出** ✗
        # —— 本分支在跌势里无条件砍掉卖侧，而"卖"正是多头的**减仓侧** ⇒ 唯一的 maker
        # 退出通道被关死；只剩 2 小时超时后的 taker 平仓（实测平仓腿 -108.78bp/-39.62bp，
        # 把浮亏全部落袋）。趋势闸（上方 blocked 分支）有库存感知豁免，side_mode 没有 ✗。
        # 语义（与 F76 正交且一致）：
        #   · 顺势侧（涨=买、跌=卖）在"加仓或空仓"时仍封（保持 counter_trend 原意 ✓）；
        #   · 顺势侧若是**减仓侧**（多头在跌势里卖、空头在涨势里买）⇒ 放行 ✓；
        #   · 逆势侧永远放行（与现状一致 ✓）。
        _pos_ct = local_book.qty(state.symbol)
        if _trend_bp > _min_bp:
            if allow_buy and not (_pos_ct < -1e-12):
                allow_buy, why_buy = False, "ct_trend_up"      # 涨：买=加仓/接刀 → 封
        elif _trend_bp < -_min_bp:
            if allow_sell and not (_pos_ct > 1e-12):
                allow_sell, why_sell = False, "ct_trend_down"  # 跌：卖=加仓/杀跌 → 封

    # [h341 2026-09-26] `side_mode="slow_rev"`：15 分钟反转备选规则（未上线）。
    # |r900| ≥ slow_rev_min_bp ⇒ 只挂反向（fade）侧；顺势加仓侧封、减仓侧豁免
    # （F76 语义）。出口档由车道 meta 控制（reversal_decay_bp=0、tp=30、
    # timeout=300、stop=40），本分支只负责入场侧。
    if _side_mode == "slow_rev":
        _min_sr = float(getattr(limits, "slow_rev_min_bp", 40.0) or 40.0)
        _r900 = trend_move_bp(state.mid_hist, 60)   # 60 期 × 15s = 900s
        _pos_sr = local_book.qty(state.symbol)
        if _r900 > _min_sr:      # 涨过头 → 只挂卖（fade），买=加仓/接刀 → 封
            if not (_pos_sr < -1e-12):
                if _soft_mode:                    # [h740] 软模式:不封侧,只标记缩量
                    _soft_mark("buy")
                elif allow_buy:
                    allow_buy, why_buy = False, "slow_rev_up"
        elif _r900 < -_min_sr:   # 跌过头 → 只挂买（fade），卖=加仓/杀跌 → 封
            if not (_pos_sr > 1e-12):
                if _soft_mode:
                    _soft_mark("sell")
                elif allow_sell:
                    allow_sell, why_sell = False, "slow_rev_down"

    # [F326 2026-09-17 路径 D] `side_mode="reduce_only_when_inv"`：
    # **有库存时只允许减仓侧报价**（加仓侧一律封），让库存只能**被动流出**。
    #
    # 为什么需要（本轮的证据链）：
    #   · 实盘 21.8h：亏损 100% 来自 taker 平仓（12 笔 / −52bp/腿）；
    #   · F324：把止损关掉（让减仓侧地板单接管）⇒ **maker 腿从 +7.78 掉到 −3.20**；
    #   · F325：拉长 `stop_maker_grace_sec` 30→3600 ⇒ flatten 19→15 次，
    #     但 **maker 腿 +6.58 → −3.18**，净额反而更差。
    #   ⇒ 共同机制：削弱 taker 平仓会让**库存留在账上**，而现有逻辑在持有库存时
    #     **仍然继续挂加仓侧** ⇒ 库存越滚越大 ⇒ 报价被 k_inv 偏斜与敞口闸持续抑制
    #     ⇒ maker 腿转负。
    #
    # 本模式切断这个循环：库存一旦存在，**加仓侧立即停止报价**，
    # 只留减仓侧（`compute_quote` 会给它降到 `min_width_reduce_bp` 地板，默认 6bp）
    # ⇒ 库存只能被动流出，且**不需要 taker**。
    #
    # 与 F76/F86/F228 的既有豁免一致：**减仓侧永不受限**（本模式只封加仓侧）。
    # 与 F228 的区别：F228 在跌势里放行"多头的卖"（减仓侧），但对**空头的买**
    # （加仓侧）仍可能放行；本模式无论趋势如何，只要有库存就封加仓侧。
    #
    # 回滚：把 `side_mode` 改回 `counter_trend`（本块完全不执行，行为逐字回到旧版）。
    if _side_mode == "reduce_only_when_inv":
        _pos_rq = local_book.qty(state.symbol)
        if _pos_rq > 1e-12 and allow_buy:
            allow_buy, why_buy = False, "reduce_only_inv"
        elif _pos_rq < -1e-12 and allow_sell:
            allow_sell, why_sell = False, "reduce_only_inv"

    # [F86 2026-09-14] 流向毒性闸（学术）：按上一桶主动流失衡封锁**逆势加仓侧**。
    # 依据 Lu-Abergel 2018（市价单驱动的移动延续率 84.3% vs 撤单 27%）与
    # Barzykin et al. 2025 逆向选择框架；本项目实证 OFI<-0.5 后 86.5% 继续下跌。
    # 语义：ofi 显著为负（主动卖压）⇒ 下一期大概率下跌 ⇒ 买单会被逆选择
    # （买了继续跌）⇒ 封买；ofi 显著为正 ⇒ 封卖。**减仓侧永不受限**（同 F76）。
    _ofi_th = float(getattr(limits, "ofi_block_threshold", 0.0) or 0.0)
    if _ofi_th > 0 and abs(float(ofi or 0.0)) > _ofi_th:
        _toxic_side = "buy" if float(ofi) < 0 else "sell"
        _pos_ofi = local_book.qty(state.symbol)
        # 库存感知：若被封侧实为减仓侧（空头遇卖压封买/多头遇买压封卖），放行
        _is_reduce = ((_toxic_side == "buy" and _pos_ofi < -1e-12)
                      or (_toxic_side == "sell" and _pos_ofi > 1e-12))
        if not _is_reduce:
            if _toxic_side == "buy":
                if _soft_mode:                    # [h740] 软模式:不封侧,只标记缩量
                    _soft_mark("buy")
                elif allow_buy:
                    allow_buy = False
                    why_buy = f"ofi_toxic_sell({float(ofi):+.2f})"
                    if not any(f.is_flatten for f in dec.fills):
                        dec.skip = dec.skip or "ofi_toxic_sell"
            elif _toxic_side == "sell":
                if _soft_mode:
                    _soft_mark("sell")
                elif allow_sell:
                    allow_sell = False
                    why_sell = f"ofi_toxic_buy({float(ofi):+.2f})"
                    if not any(f.is_flatten for f in dec.fills):
                        dec.skip = dec.skip or "ofi_toxic_buy"

    # [F350 2026-09-23] 流衰竭确认（H307 双窗验证：7 天 28.7k 腿 −0.74→−0.56bp、
    # 前窗 3.4k 腿 −0.76→−0.63bp）：逆势方向与驱动该趋势的主动流仍同向
    # （趋势涨 + 买流还在推 / 趋势跌 + 卖流还在压，|ofi| ≥ thr）→ 封加仓侧，
    # 等流衰竭再逆势；减仓侧永不受限（与 F86 同一套豁免）。
    _cfm_th = float(getattr(limits, "ofi_confirm_threshold", 0.0) or 0.0)
    if _cfm_th > 0 and abs(float(_trend_bp or 0.0)) > 1e-9:
        _trend_up = float(_trend_bp) > 0
        _pos_cfm = local_book.qty(state.symbol)
        if _trend_up and float(ofi or 0.0) > _cfm_th:
            # 趋势涨 + 买流在推 → 卖是逆势加仓侧（多头持仓时卖=减仓，豁免）
            if _pos_cfm <= 1e-12 and allow_sell:
                allow_sell = False
                why_sell = f"ofi_confirm_buy({float(ofi):+.2f})"
                if not any(f.is_flatten for f in dec.fills):
                    dec.skip = dec.skip or "ofi_confirm"
        elif (not _trend_up) and float(ofi or 0.0) < -_cfm_th:
            # 趋势跌 + 卖流在压 → 买是逆势加仓侧（空头持仓时买=减仓，豁免）
            if _pos_cfm >= -1e-12 and allow_buy:
                allow_buy = False
                why_buy = f"ofi_confirm_sell({float(ofi):+.2f})"
                if not any(f.is_flatten for f in dec.fills):
                    dec.skip = dec.skip or "ofi_confirm"

    # [h356 2026-09-27] P1 薄流确认：h355 条件模型——回调的期望收益由当前 OFI 流向
    # 决定（OFI 顺势 f30 +1.06(t=6.6) vs OFI 逆势 f30 −0.98(t=−7.3)）。
    # |300s 趋势|≥15bp（P1 触发区）且 OFI 逆势流动 ≥ thr ⇒ 封趋势同向加仓侧
    # （= 放弃流驱动回调），减仓侧豁免（F76）。300s 趋势独立计算，不受
    # side_mode=model 的 60s 模型方向影响。
    _pfb = float(getattr(limits, "pullback_flow_block", 0.0) or 0.0)
    if _pfb > 0:
        _p1_trend = trend_move_bp(state.mid_hist, 20)
        # [h400] P1 触发区下限参数化（默认 15 = 旧行为逐字一致；试跑 #10 用 10）
        _p1_th = float(getattr(limits, "p1_trigger_bp", 15.0) or 15.0)
        if abs(_p1_trend) >= _p1_th:
            _pos_pfb = local_book.qty(state.symbol)
            _ofi_pfb = float(ofi or 0.0)
            if _p1_trend > 0 and _ofi_pfb < -_pfb:
                # 涨势 + 卖流在压 = 流驱动回调：买（趋势同向）被压 → 封买
                # （多头加仓/空仓开仓都封；空头买=减仓豁免）
                if _pos_pfb > -1e-12 and allow_buy:
                    allow_buy, why_buy = False, f"pullback_flow_sell({_ofi_pfb:+.2f})"
                    if not any(f.is_flatten for f in dec.fills):
                        dec.skip = dec.skip or "pullback_flow_sell"
            elif _p1_trend < 0 and _ofi_pfb > _pfb:
                # 跌势 + 买流在推 = 流驱动反弹：卖（趋势同向）被推 → 封卖
                if _pos_pfb < 1e-12 and allow_sell:
                    allow_sell, why_sell = False, f"pullback_flow_buy({_ofi_pfb:+.2f})"
                    if not any(f.is_flatten for f in dec.fills):
                        dec.skip = dec.skip or "pullback_flow_buy"

    # [h363 2026-09-27] P4 双触突破 with 闸：120s 窗口（8 期）两次触及极值且现价贴极值
    # + OFI 与突破方向同向 ⇒ 封逆突破侧（只挂突破方向），减仓侧豁免（F76）。
    # h361: with f30 +0.66(t=17.6)/f300 +1.11。live 用 15s mid_hist 近似 1s 口径。
    _p4g = float(getattr(limits, "p4_breakout_gate", 0.0) or 0.0)
    if _p4g > 0 and len(state.mid_hist) >= 9:
        _w4 = list(state.mid_hist[-9:])
        _hi4, _lo4 = max(_w4), min(_w4)
        if _hi4 > 0:
            # [f328] 不用推导式（推导式作用域会把外层 `_w4/_hi4` 读入误报为未绑定）
            _near_hi = 0
            _near_lo = 0
            for _m4 in _w4:
                if _m4 >= _hi4 * (1 - 5e-6):
                    _near_hi += 1
                if _m4 <= _lo4 * (1 + 5e-6):
                    _near_lo += 1
            _dir4 = 0.0
            if _near_hi >= 2 and _w4[-1] >= _hi4 * (1 - 5e-6):
                _dir4 = 1.0
            elif _near_lo >= 2 and _w4[-1] <= _lo4 * (1 + 5e-6):
                _dir4 = -1.0
            if _dir4 and (float(ofi or 0.0) * _dir4) >= _p4g:
                _pos4 = local_book.qty(state.symbol)
                if _dir4 > 0:      # 向上突破：封卖（多头减仓豁免）
                    if allow_sell and not (_pos4 > 1e-12):
                        allow_sell, why_sell = False, \
                            f"p4_breakout_buy({float(ofi) * _dir4:+.2f})"
                        if not any(f.is_flatten for f in dec.fills):
                            dec.skip = dec.skip or "p4_breakout_buy"
                else:              # 向下突破：封买（空头回补豁免）
                    if allow_buy and not (_pos4 < -1e-12):
                        allow_buy, why_buy = False, \
                            f"p4_breakout_sell({float(ofi) * _dir4:+.2f})"
                        if not any(f.is_flatten for f in dec.fills):
                            dec.skip = dec.skip or "p4_breakout_sell"

    # [h363 2026-09-27] P5 挤压突破 with 闸：60s 波动 < 滚动 30 分位 且 |r15|≥2bp
    # + OFI 与动量方向同向 ⇒ 封逆动量侧，减仓侧豁免（F76）。
    # h361: with f30 +0.77(t=22.5)。q30 用 5 期窗在 240 期历史上滚动估算。
    _p5g = float(getattr(limits, "p5_squeeze_gate", 0.0) or 0.0)
    if _p5g > 0 and len(state.mid_hist) >= 40:
        _h5 = list(state.mid_hist)

        def _seg_vol(w):
            return sum(abs((w[t + 1] - w[t]) / w[t]) * 1e4
                       for t in range(len(w) - 1) if w[t] > 0)

        _v_now = _seg_vol(_h5[-5:])
        if _v_now > 0:
            # [f328] 不用推导式（同上：外层 `_seg_vol/_h5` 读入推导式作用域会被误报）
            _vs = []
            for _k5 in range(len(_h5) - 4):
                _vs.append(_seg_vol(_h5[_k5:_k5 + 5]))
            _vs2 = []
            for _v5 in _vs:
                if _v5 > 0:
                    _vs2.append(_v5)
            _vs = _vs2
            if _vs:
                _q30 = sorted(_vs)[int(len(_vs) * 0.3)]
                _r15 = trend_move_bp(_h5, 1)
                if _v_now < _q30 and abs(_r15) >= 2.0:
                    _dir5 = 1.0 if _r15 > 0 else -1.0
                    if (float(ofi or 0.0) * _dir5) >= _p5g:
                        _pos5 = local_book.qty(state.symbol)
                        if _dir5 > 0:      # 动量向上：封卖
                            if allow_sell and not (_pos5 > 1e-12):
                                allow_sell, why_sell = False, \
                                    f"p5_squeeze_buy({float(ofi) * _dir5:+.2f})"
                                if not any(f.is_flatten for f in dec.fills):
                                    dec.skip = dec.skip or "p5_squeeze_buy"
                        else:              # 动量向下：封买
                            if allow_buy and not (_pos5 < -1e-12):
                                allow_buy, why_buy = False, \
                                    f"p5_squeeze_sell({float(ofi) * _dir5:+.2f})"
                                if not any(f.is_flatten for f in dec.fills):
                                    dec.skip = dec.skip or "p5_squeeze_sell"

    if not allow_buy and not allow_sell:
        # [h735/h736] 软模式:双边被封不再清空挂单,改双边报价(缩量已在 fill
        # 段记账前完成,此处不缩放 fills——避免 book/账本 qty 分叉)
        if _soft_mode:
            allow_buy = allow_sell = True
            dec.skip = ""
            dec.skip_side = ""
        else:
            dec.skip = (why_buy or why_sell or "blocked").split("(")[0]
            dec.skip_side = "both"
            if dec.action != "flatten":
                dec.action = "pause"
            state.quote_bid = state.quote_ask = state.quote_ts = 0.0
            return dec, {"book": local_book}

    # [h401 2026-09-27] P3 尖峰 fade with 闸：|75s 移动|≥3bp（尖峰）且 OFI 与
    # fade 方向同向（|ofi|≥thr）⇒ 封追尖峰侧（上尖峰+卖流=封买；下尖峰+买流=封卖），
    # 减仓侧豁免（F76）。h361: with f30 +0.857(t=10.2)；against f30 −0.972(t=−18.2)。
    _p3g = float(getattr(limits, "p3_spike_gate", 0.0) or 0.0)
    if _p3g > 0 and len(state.mid_hist) >= 6:
        _spike = trend_move_bp(state.mid_hist, 5)   # 5×15s ≈ 75s 移动
        if abs(_spike) >= 3.0:
            _dir3 = 1.0 if _spike > 0 else -1.0
            _ofi_fade = float(ofi or 0.0) * (-_dir3)
            if _ofi_fade >= _p3g:
                _pos3 = local_book.qty(state.symbol)
                if _dir3 > 0:      # 上尖峰 + 卖压在 fade：封买（追涨侧）
                    if allow_buy and not (_pos3 < -1e-12):
                        allow_buy, why_buy = False, \
                            f"p3_spike_fade_buy({_ofi_fade:+.2f})"
                        if not any(f.is_flatten for f in dec.fills):
                            dec.skip = dec.skip or "p3_spike_fade_buy"
                else:              # 下尖峰 + 买压在 fade：封卖（追跌侧）
                    if allow_sell and not (_pos3 > 1e-12):
                        allow_sell, why_sell = False, \
                            f"p3_spike_fade_sell({_ofi_fade:+.2f})"
                        if not any(f.is_flatten for f in dec.fills):
                            dec.skip = dec.skip or "p3_spike_fade_sell"
                if not allow_buy and not allow_sell:
                    if _soft_mode:
                        allow_buy = allow_sell = True
                        dec.skip = ""
                        dec.skip_side = ""
                    else:
                        dec.skip = (why_buy or why_sell or "blocked").split("(")[0]
                        dec.skip_side = "both"
                        if dec.action != "flatten":
                            dec.action = "pause"
                        state.quote_bid = state.quote_ask = state.quote_ts = 0.0
                        return dec, {"book": local_book}

    # [F272 2026-09-16 · E2 选择性成交] microprice 偏离闸（调研"用法 B"）：
    # microprice 高于 mid（买压）⇒ 卖单会被抬起后继续涨（负 markout）⇒ 封卖；
    # 低于 mid（卖压）⇒ 买单会被砸中后继续跌 ⇒ 封买。减仓侧豁免（同 F76/F228）。
    _mp_th = float(getattr(limits, "mp_block_bp", 0.0) or 0.0)
    # [h664] 微价闸排除清单(h365:BNB 微价反向,须排除;空串=不排除)
    _mp_excl = {x.strip().upper() for x in
                str(getattr(limits, "mp_block_exclude", "") or "").split(",") if x.strip()}
    _mp_skew = float(mp_skew_bp or 0.0)
    if (_mp_th > 0 and abs(_mp_skew) >= _mp_th
            and str(state.symbol).upper() not in _mp_excl):
        _mp_side = "sell" if _mp_skew > 0 else "buy"
        _pos_mp = local_book.qty(state.symbol)
        _mp_reduce = ((_mp_side == "buy" and _pos_mp < -1e-12)
                      or (_mp_side == "sell" and _pos_mp > 1e-12))
        if not _mp_reduce:
            if _mp_side == "sell":
                if _soft_mode:                    # [h740] 软模式:不封侧,只标记缩量
                    _soft_mark("sell")
                elif allow_sell:
                    allow_sell = False
                    why_sell = f"mp_skew_sell({_mp_skew:+.2f}bp)"
                    if not any(f.is_flatten for f in dec.fills):
                        dec.skip = dec.skip or "mp_skew_sell"
            elif _mp_side == "buy":
                if _soft_mode:
                    _soft_mark("buy")
                elif allow_buy:
                    allow_buy = False
                    why_buy = f"mp_skew_buy({_mp_skew:+.2f}bp)"
                    if not any(f.is_flatten for f in dec.fills):
                        dec.skip = dec.skip or "mp_skew_buy"
            if not allow_buy and not allow_sell:
                if _soft_mode:
                    allow_buy = allow_sell = True
                    dec.skip = ""
                    dec.skip_side = ""
                else:
                    dec.skip = (why_buy or why_sell or "blocked").split("(")[0]
                    dec.skip_side = "both"
                    if dec.action != "flatten":
                        dec.action = "pause"
                    state.quote_bid = state.quote_ask = state.quote_ts = 0.0
                    return dec, {"book": local_book}

    # [h405 2026-09-27] #12 分形态×分币种启停表：矩阵里的币只在允许形态上下文
    # 挂单（空仓时生效；有持仓豁免——减仓侧必须存活 F76）。矩阵来自
    # lane meta.pattern_matrix（tick 循环传入；None/空 = 旧行为逐字一致）。
    _pm = pattern_matrix or {}
    if _pm and abs(state.qty) <= 1e-12:
        _pm_allowed = list(_pm.get(state.symbol) or [])   # [f328] 不用推导式（检查器口径）
        if _pm_allowed:
            _pm_ctx = _detect_pattern(
                list(state.mid_hist or []),
                float(getattr(limits, "p1_trigger_bp", 15.0) or 15.0))
            if _pm_ctx not in _pm_allowed:
                dec.action, dec.skip = "pause", f"pattern_matrix({_pm_ctx or 'none'})"
                dec.skip_side = "both"
                state.quote_bid = state.quote_ask = state.quote_ts = 0.0
                return dec, {"book": local_book}

    # [F302 2026-09-21] **撤掉减仓侧出库挂单**（用户选定方案 ①）。
    #
    # 依据（真实账本，非模拟）：止盈腿 **+7.44bp/笔** vs 普通 maker 出库腿 **+0.3bp/笔**（24 倍）。
    # 而出库挂单与止盈**在同一条"有利偏移"轴上竞争，出库单总在更近处**
    # （出库赚 `r×半价差` ≈ 0.2bp，止盈要 +12bp）⇒ 挂着出库单时止盈几乎永不触发
    # （H172 实测止盈占比恒 0.0%）。
    #
    # 与 `core.compute_quote` 里 `_red_bid/_red_ask` 的判定**必须同口径**：
    #   空头（inv_ratio < 0）⇒ 买侧是减仓侧
    #   多头（inv_ratio > 0）⇒ 卖侧是减仓侧
    # ⚠️ 只在**持有仓位**时撤 —— 空仓时两侧都是加仓侧，撤掉就等于不做市了。
    #
    # ⚠️ 单独使用会把仓位拖很久（H176：38% 的仓位到 300s 仍在，
    #    且其中位浮亏 −15.89bp）⇒ **必须配套 `timeout_exit_maker_only=False`**。
    #
    # ⚠️⚠️ [F327 2026-09-21] **库存必须在本块内自己取，不许复用上游变量**。
    # 事故（生产停摆 ~15 分钟，231 tick 全部失败）：
    #   F302 初版写的是 `abs(_pos_d)`，而 `_pos_d` 只在上面
    #   `_side_mode == "reduce_only_when_inv"` 分支里赋值 ⇒ 生产 `side_mode="both"`
    #   时该名字**从未绑定** ⇒ 本行抛 `NameError`（报错文案是
    #   "cannot access local variable '_pos_d' where it is not associated with a value"）
    #   ⇒ `shadow_tick_task` 整体失败，车道**一个 tick 都跑不出去**。
    #   触发条件：`reduce_quote_disabled=True` 且 `side_mode != "reduce_only_when_inv"`
    #   —— 恰好是 H178 A/B 的 B 臂，且 A/B 脚本退出时**未能恢复**
    #   （`finally` 在 SIGTERM 下不执行）⇒ 崩溃配置被留在生产里。
    # 修法：本块自取一次 `_pos_now`。顺带把上面那个分支的局部名改成 `_pos_rq` ——
    # Python 不会因为"名字只在某分支绑定"而编译报错（所以这个 bug 是**静默**通过
    # 全部单测的：测试里 `side_mode` 恰好命中那个分支，`_pos_d` 就有值）。
    # 两个块各自持有自己的局部名后，"跨块复用局部名"这个错误模式在阅读时即暴露。
    _pos_now = local_book.qty(state.symbol)
    if bool(getattr(limits, "reduce_quote_disabled", False)) and abs(_pos_now) > 1e-12:
        if _pos_now > 1e-12 and allow_sell:
            allow_sell, why_sell = False, "reduce_quote_off"
        elif _pos_now < -1e-12 and allow_buy:
            allow_buy, why_buy = False, "reduce_quote_off"

    # [h740→h794 2026-10-04 用户诊断"底层方向错了"] 软模式覆盖的**修正版**:
    # 原始设计(h658,用户"30s-5min 超短交易/流对齐"):统一流定律 ——
    #   with 流 = 全正(t=10~22),against 流 = 全负(t=−7~−18),"不 fade 流 = 唯一硬规律";
    #   引擎应该"流对齐时接单、逆流时只挂同流侧",**不是**全时段双边做市。
    # h740 的"概率替代预测"把它改成"信号只调量、永不封单" ⇒ **逆流侧(全负 t)
    # 也被恢复报价** ⇒ 持续接毒性流量 —— 这就是"做市商化"的机械根源
    # (h792 块#2 铁证:inside 报价 84% 成交、每腿 −9.44bp,正是逆流侧)。
    # 修正:软模式只转化**概率类**闸门(趋势/微价/OFI/vpin 等),
    # **不恢复**以下硬封锁:
    #   · direction_*      = 流方向卡(h626/h658 造血定律,30s-5min 流对齐);
    #   · decay_blocked*   = 亏损淘汰/崩盘熔断隔离(h759/h784);
    #   · inv_add_block    = 库存硬上限;
    #   · book_tight       = 盘口太窄时拒报(防穿价);
    #   · markout_halt     = markout 风控停挂;
    #   · reduce_quote_off = 显式配置。
    if _soft_mode:
        _keep = ("direction_", "decay_blocked", "inv_add_block", "book_tight",
                 "markout_halt", "reduce_quote_off", "exposure")
        if not allow_buy and not str(why_buy or "").startswith(_keep):
            allow_buy = True
            _soft_mark("buy")
        if not allow_sell and not str(why_sell or "").startswith(_keep):
            allow_sell = True
            _soft_mark("sell")
    # 不允许的一侧不下单（挂 0），另一侧照常
    dec.bid = q.bid if allow_buy else 0.0
    dec.ask = q.ask if allow_sell else 0.0
    dec.w_bid_bp = q.w_bid_bp if allow_buy else 0.0
    dec.w_ask_bp = q.w_ask_bp if allow_sell else 0.0
    # ── [h775 2026-10-03 用户指令"加这一刀"] 老仓**减仓侧贴盘口** ──────────────
    # 实测证据(近 2h,逐出场路径拆解):
    #   · 171 条自然 maker 成交:捕获 **+3.6bp** ✓(引擎在赚)
    #   · 15 条 `timeout_hard_taker`:净 **−15.79U = 当时段亏损的 79%**,
    #     每腿 −34bp = **吃价差 −12.2bp + taker 费 −4.0bp** + 价格漂移 −17.8bp
    #   ⇒ "赚小亏大"的钱不是慢慢亏的,是**平仓那一刀**同时付了过路费与漂移。
    # 改法:持仓年龄 > `reduce_touch_after_sec`(默认 0=关)时,把**减仓侧**报价从
    #   我方常规宽度**贴到盘口最优价**(仍是 maker 挂单,绝不穿价)——
    #   用"更快被动成交"换"少付 12bp 过路费",并保留吃到半价差的可能。
    # 边界:只动减仓侧(加仓侧不受影响);贴价后仍严格不穿价(留 1e-6 余量);
    #   无仓/无盘口/年龄不够 ⇒ 逐字保持原行为。
    _rt_after = float(getattr(limits, "reduce_touch_after_sec", 0.0) or 0.0)
    if (_rt_after > 0 and abs(_pos_now) > 1e-12 and float(state.opened_ts or 0.0) > 0
            and (now_ts - float(state.opened_ts)) > _rt_after
            and _bb > 0 and _ba > _bb and mid > 0):
        if _pos_now > 1e-12 and allow_sell and dec.ask > 0:
            _touch = max(min(float(dec.ask), _ba), _bb * (1.0 + 1e-6))
            if _touch < float(dec.ask):
                dec.ask = _touch
                dec.w_ask_bp = (dec.ask - mid) / mid * 1e4
                dec.exit_path = dec.exit_path or "reduce_touch"
        elif _pos_now < -1e-12 and allow_buy and dec.bid > 0:
            _touch = min(max(float(dec.bid), _bb), _ba * (1.0 - 1e-6))
            if _touch > float(dec.bid):
                dec.bid = _touch
                dec.w_bid_bp = (mid - dec.bid) / mid * 1e4
                dec.exit_path = dec.exit_path or "reduce_touch"
    # ── [h793 2026-10-04 用户指令"从底层啃/继续"] 报价位置钳制:不再插价差 ────
    # 底层审计(h792)铁证:近 10h 带位置标签的成交,inside(我方报价比全场最优
    # 还好,平均 22.5bp)= 84% 的成交、每腿 **−9.44bp**;behind(排在盘口之后)
    # = 每腿 **+5.96bp**。根因:宽度上限 4.8bp(半宽)远小于这些币的真实半价差
    # (5~22bp)⇒ 我方永远"倒贴钱"成为全场最优 ⇒ 独自吃下全部毒性流量。
    # 钳制:报价**最多比盘口最优价好 1 个 tick**(排队靠前的最小代价),其余时间
    # 与全市场同队,赚真实半价差(5~22bp/腿,替代现在的 2.4bp)。
    # 边界:盘口缺失(_bb/_ba=0)⇒ 不动;只把报价往"更差"方向移(绝不可能穿价);
    # 老仓减仓侧贴盘口(h775)已落在 touch,本钳制放行(不优于 touch+1tick)。
    try:
        from backend.services.market_maker.venue_filters import (
            filters_for as _ff, round_px as _vpx,
        )
        _tick = float((_ff(str(state.symbol)) or {}).get("tick_size") or 0.0)
    except Exception:
        _tick = 0.0
    if _tick <= 0:
        _tick = float(mid) * 0.5 / 1e4 if mid > 0 else 0.0
    if _tick > 0 and _bb > 0 and _ba > _bb:
        # [h799 2026-10-04 修"零成交"] 滞回死区:只有报价比盘口好超过
        # (1 tick + 2bp) 时才重新钳制 —— 否则报价每个 tick 都追盘口,
        # quote_ts 每 1~2 秒刷新一次 ⇒ 成交判定窗口坍缩到几秒 ⇒ 判不到成交
        # (实测 15 分钟 0 腿,而市场每秒都有成交)。
        _db = float(mid) * 2.0 / 1e4 if mid > 0 else 0.0
        if dec.bid > 0 and dec.bid > _bb + _tick + _db:
            dec.bid = _vpx(str(state.symbol), _bb + _tick)
            dec.w_bid_bp = (mid - dec.bid) / mid * 1e4 if mid > 0 else 0.0
            dec.skip = dec.skip or "quote_at_touch"
        if dec.ask > 0 and dec.ask < _ba - _tick - _db:
            dec.ask = _vpx(str(state.symbol), _ba - _tick)
            dec.w_ask_bp = (dec.ask - mid) / mid * 1e4 if mid > 0 else 0.0
            dec.skip = dec.skip or "quote_at_touch"
    # [F205] 把报价分支带出来（即使某一侧被闸门挡掉，分支信息仍然有效 ✓）
    dec.quote_mode = q.mode
    dec.base_bp = q.base_bp
    if not allow_buy or not allow_sell:
        dec.skip = (why_buy if not allow_buy else why_sell).split("(")[0]
        dec.skip_side = "buy" if not allow_buy else "sell"

    # ── [F254 2026-09-20] 队列优先保持：价位没变就**不重挂** ──────────────
    #
    # ## 为什么（H19 实测 + 论文 Table 1）
    #
    # 此前这里是 `state.quote_bid, state.quote_ask = dec.bid, dec.ask`：
    # 每 15s tick 无条件把报价重设一遍。真实交易所里，"重挂"= 撤单 + 重新排队，
    # **队列位置直接清零**（排到当前挂量的最后）。
    #
    # H19 用 tick 级真实数据量化了这个代价（15s 报价节奏，12h，4 个有效样本币）：
    #     队尾（LA=全部挂量）成交率 24–37%   mk@1s −0.22 ~ −0.62bp
    #     队首（LA=0）        成交率 85–95%   mk@1s −0.12 ~ +0.27bp
    #     ⇒ **队首 − 队尾 = +0.42bp @1s**（跨币）
    # 而我们的净边际只有 **−0.60bp** ⇒ 队列位置的量级与整个策略的盈亏同级。
    # 论文 Table 1 独立给出同格内差 0.12–0.86bp，量级一致。
    #
    # H19 还给出"怎么做到队首"：队首成交的**等待中位只有 3.2–5.7 秒**，
    # 说明 touch 的队列周转极快 ⇒ 只要**别自己把位置推倒重来**，就能吃到队首那一段。
    # 论文第 79–81 / 390–392 行的撤单纪律正是这个意思：
    #     挂在 touch 上就保留（前面的量只减不增、优先级累积），
    #     **只有当价格变化使它不再位于 touch 时才撤单**。
    #
    # ## 实现
    #
    # 若新报价与当前挂单**在同一价位**（相对差 < `quote_hold_tol_bp`，默认 0.25bp），
    # 则**保留原价与原 `quote_ts`** —— 价位不变、时间戳不刷新，语义上就是
    # "这张单从 T 时刻起一直挂在那里"。只有价位真的移动了才换成新价并刷新时间戳。
    #
    # ## 对纸面回测**没有**影响（必须说清楚）
    #
    # 我们的纸面成交模型不建队列，所以"保持"与"重挂"在回测里产出完全相同的成交
    # ⇒ **本改动的收益在 paper 模式下恒等于 0，是纯粹的实盘准备**。
    # 之所以现在就做：一旦真实下单，这个语义差异就是直接的钱，
    # 而事后从账本里是看不出来"我们到底有没有保持队列位置"的。
    #
    # 关闭方式：把 `quote_hold_tol_bp` 设为 0（或负数）⇒ 与旧行为逐字一致。
    _hold_tol_bp = float(getattr(limits, "quote_hold_tol_bp", 0.25) or 0.0)
    _held_bid = _held_ask = False
    if _hold_tol_bp > 0:
        for _side, _new, _attr in (("bid", dec.bid, "quote_bid"),
                                   ("ask", dec.ask, "quote_ask")):
            _old = float(getattr(state, _attr) or 0.0)
            if _new and _old > 0 and _new > 0:
                _drift_bp = abs(_new - _old) / _old * 1e4
                if _drift_bp < _hold_tol_bp:
                    # 同价位 ⇒ 保留原单（含原时间戳），不回队列
                    if _side == "bid":
                        dec.bid = _old
                        _held_bid = True
                    else:
                        dec.ask = _old
                        _held_ask = True

    if _held_bid or _held_ask:
        dec.skip_detail = (dec.skip_detail or "") + \
            f" [保持队列位置: {'bid ' if _held_bid else ''}{'ask' if _held_ask else ''}]".rstrip()

    state.quote_bid = dec.bid if not _held_bid else state.quote_bid
    state.quote_ask = dec.ask if not _held_ask else state.quote_ask
    # `quote_ts` 只在**两侧都重挂**时刷新；只要有一侧被保持，时间戳就不能前进，
    # 否则 `max_quote_age_sec` 会误以为这是一张新单。
    if not (_held_bid or _held_ask):
        state.quote_mid, state.quote_ts = mid, now_ts
    if dec.action != "flatten":
        dec.action = "quote"
    return dec, {"book": local_book}


def _params_maker_fee_bp(venue: str) -> float:
    """真实费率表里的 maker 费率（bp）。"""
    from backend.services.market_maker.replay import params_maker_fee
    return float(params_maker_fee(venue)) * 1e4


# ═══════════════════════ 驱动层（DB + 调度） ═══════════════════════

_ensured = False
_ensure_lock = None


def ensure_table() -> None:
    """建运行态表（每进程一次）。"""
    global _ensured, _ensure_lock
    if _ensure_lock is None:
        import threading
        _ensure_lock = threading.Lock()
    if _ensured:
        return
    with _ensure_lock:
        if _ensured:
            return
        try:
            from sqlalchemy import text

            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    db.execute(text(
                        "CREATE TABLE IF NOT EXISTS lane_runtime_state ("
                        " lane_id VARCHAR(64) NOT NULL,"
                        " symbol VARCHAR(32) NOT NULL,"
                        " state_json JSONB NOT NULL,"
                        " updated_ts TIMESTAMPTZ NOT NULL DEFAULT now(),"
                        " PRIMARY KEY (lane_id, symbol))"
                    ))
                    db.execute(text(
                        "CREATE TABLE IF NOT EXISTS lane_shadow_report ("
                        " id BIGSERIAL PRIMARY KEY,"
                        " lane_id VARCHAR(64) NOT NULL,"
                        " as_of TIMESTAMPTZ NOT NULL DEFAULT now(),"
                        " window_days INTEGER NOT NULL DEFAULT 30,"
                        " fills INTEGER NOT NULL DEFAULT 0,"
                        " flattens INTEGER NOT NULL DEFAULT 0,"
                        " notional DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " spread_bp DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " price_bp DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " fee_bp DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " net_bp DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " net_usd DOUBLE PRECISION NOT NULL DEFAULT 0,"
                        " per_symbol JSONB,"
                        " promotion JSONB,"
                        " note TEXT)"
                    ))
                    db.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_lane_shadow_report_lane"
                        " ON lane_shadow_report (lane_id, as_of DESC)"
                    ))
                    # 指标列允许 NULL：无成交时六维是「无数据」而不是 0，
                    # 用 0 会把「没跑」误读成「跑了但收益为 0」。
                    for col in ("notional", "spread_bp", "price_bp", "fee_bp",
                                "net_bp", "net_usd"):
                        db.execute(text(
                            f"ALTER TABLE lane_shadow_report ALTER COLUMN {col} DROP NOT NULL"
                        ))
                    db.commit()
            _ensured = True
        except Exception as e:
            logger.warning("[F60] ensure_table 失败: %s", e)


class ShadowRunner:
    """L1 做市车道的影子期驱动器。

    每个 tick：读最新盘口 + 自上次 tick 以来的区间成交 → `plan_tick` →
    写 `lane_ledger` → 存运行态 → 刷新 `lane_registry` edge。
    """

    def __init__(
        self,
        *,
        lane_id: str = DEFAULT_LANE_ID,
        venue: str = DEFAULT_VENUE,
        symbols: Optional[List[str]] = None,
        equity: float = 5000.0,
        account_id: Optional[int] = None,
        params=None,
        limits=None,
        fill_notional: float = FILL_NOTIONAL,
        strategy_type: str = "MM",
    ) -> None:
        from backend.services.market_maker.core import LaneRiskLimits, QuoteParams

        self.lane_id = lane_id
        self.venue = venue
        # [h665] 车道模式:live 车道走实盘执行桥,paper 走模拟成交
        self.live_mode = False
        try:
            from backend.services import lane_registry as reg
            _lane0 = reg.get_lane(lane_id) or {}
            self.live_mode = str(_lane0.get("mode") or "") == "live"
        except Exception:
            pass
        self.symbols = list(symbols or DEFAULT_SYMBOLS)
        self.equity = float(equity)
        self.account_id = account_id
        self.strategy_type = str(strategy_type or "MM").upper()
        self.meta: Dict[str, Any] = {}
        # [F301 2026-09-16] 启动对账结果（load_states 填充），供观测/巡检读取。
        self.last_reconcile: Dict[str, Any] = {}
        self.params = params or QuoteParams()
        self.limits = limits or LaneRiskLimits()
        # ── [整顿轮·T35 2026-10-06] 给 `timeout_exit_maker_only` 一个受控覆盖 ──
        #
        # 为什么要能覆盖：实测 556 条真实往返显示——
        #   持仓 15-45s : μ=+15.92bp  t=+3.65   ← edge 活在这里
        #   持仓 60-120s: μ=−20.58bp  t=−4.76   ← 衰减区
        #   **>60s 占 56.8% 的往返**，μ=−7.51bp  t=−3.45
        #
        # 机制（`runner.py:2195-2230`）：
        #   `timeout_hard_taker_sec`(live=90s) 且 `timeout_exit_maker_only=True`
        #   ⇒ **只登记、不 taker**（runner.py:2229-2230），仓位继续等被动挂单。
        #   而 `max_one_side_seconds`(90s) 只封**加仓侧**，不强制离场。
        #   ⇒ 所以"90s 硬上限"不是平仓时限，而是"停止加仓、开始等"的时刻。
        #
        # `core.py:1264-1267` 也写明：
        #   「`timeout_exit_maker_only=true` 时 90s 超时只"停止加仓+挂减仓地板单"，
        #     行情不回头则持仓可无限拖长（实测 ADA 空单 78min、孤儿仓 4.8 天）
        #     ⇒ 违反用户"30s~5min 时域"约定。」
        #   ⇒ 改成 False 时：年龄 > `timeout_hard_taker_sec` 就**无条件 taker 平仓**。
        #
        # 成本对照：跨价成本仅 **4bp**（taker 费），而等被动出场实测代价 ~36bp。
        #
        # 回滚：删除 `MM_TIMEOUT_EXIT_MAKER_ONLY`（或设成 1）⇒ 恢复注册表原值。
        #
        # ⚠️ 注意：`LaneRiskLimits` 是 **frozen dataclass**，不能就地赋值
        # （实测 `FrozenInstanceError: cannot assign to field`）。
        # 必须用 `dataclasses.replace` 生成新实例。
        # 我第一版就是直接赋值、又被自己写的 `except` 吞掉 ⇒ 静默无效。
        try:
            _ovr = str(os.getenv("MM_TIMEOUT_EXIT_MAKER_ONLY", "") or "").strip()
            if _ovr:
                from dataclasses import replace as _dc_replace
                _ovr_b = _ovr.lower() not in ("0", "false", "no", "off")
                _old = bool(getattr(self.limits, "timeout_exit_maker_only", False))
                self.limits = _dc_replace(self.limits, timeout_exit_maker_only=_ovr_b)
                logger.warning(
                    "[T35] timeout_exit_maker_only %s -> %s（env 覆盖；回滚=删掉 "
                    "MM_TIMEOUT_EXIT_MAKER_ONLY）", _old, _ovr_b)
        except Exception as _e:  # noqa: BLE001
            logger.warning("[T35] timeout_exit_maker_only 覆盖失败: %s", _e)
        self.fill_notional = float(fill_notional)
        self.maker_fee_bp = _params_maker_fee_bp(venue)
        self.states: Dict[str, SymbolState] = {
            s: SymbolState(symbol=s) for s in self.symbols
        }
        # [F90 2026-09-14] 在营宇宙集合。`load_states` 会把**历史宇宙**的残留行
        # 一并读进内存（实测 DOGE 被移出宇宙后仍留在 lane_runtime_state），
        # 用集合区分「在营」与「孤儿持仓」——孤儿必须计入风险并强制退出。
        self._symbol_set: set = set(self.symbols)
        self.last_tick_ts: float = 0.0
        # [F92] 本进程首/末 tick：前端「本进程成交速率」数据源（时代速率会被
        # 早期故障期稀释，用户要看的是**现在**跑多快）。
        self._first_tick_ts: float = 0.0
        self.last_error: str = ""
        self.ticks: int = 0
        self.fills: int = 0
        self.flattens: int = 0
        self.realized_usd: float = 0.0
        # [F81] 成交桶水位线：每币已消费的最大桶时间戳（防漏桶/防重复消费）
        self._seg_watermark: Dict[str, int] = {}
        # ── [F281 2026-09-21] 新鲜盘口缓存（修「引擎 mid 结构性滞后 15 秒」）──────
        # H68 实测：`market_orderbook_snapshots` 间隔中位 **15,000ms**，而它是 mid 的唯一来源；
        # `asterdex_book_ticker` 间隔 **8~37ms**（快 1071 倍）。
        # ⇒ 引擎参考中价滞后 ~7.5 秒 ⇒ 报价挂在错误位置。
        # `MM_FRESH_MID=0` ⇒ 逐字关闭（旧行为 100% 不变 ✓）。
        # H69 滞后-代价曲线（6 币 24h，跨币等权）—— 这张表决定刷新间隔该多小：
        #     滞后0s -0.6267bp | 1s -0.7733 | 3s -0.9925 | 7.5s -1.3417
        #     15s -1.8393 | 30s -2.6515 | **60s -3.9249**
        # ⇒ 每多滞后 1 秒约损失 0.08~0.16bp/笔。**滞后 15s 的代价 = −1.2126bp/笔**，
        #   而 H63 实盘实测 fill 腿是 −2.2407bp ⇒ **滞后几乎解释了全部剩余亏损**。
        # 默认取 **200ms**（≈ lag 0.1s，落在曲线最平坦的一段；约 5 次查询/秒）。
        self._fresh_mid_enabled: bool = os.getenv("MM_FRESH_MID", "1").strip() not in ("0", "false", "False")
        try:
            self._fresh_mid_refresh_s: float = max(
                0.05, float(os.getenv("MM_FRESH_MID_REFRESH_MS", "200")) / 1000.0)
        except Exception:
            self._fresh_mid_refresh_s = 0.2
        self._fresh_book: Dict[str, tuple] = {}
        self._fresh_book_ts: float = 0.0
        self._fresh_mid_hits: int = 0
        # [h664] 方向分数影子快照(逐币,每 tick 覆盖;心跳/历史 60s 落盘)
        self._dir_score: Dict[str, dict] = {}
        # [h665] 实盘执行桥(mode=live 车道惰性初始化;纸面车道恒 None)
        self._live = None
        self._live_last_refresh = 0.0
        # [h667] 模拟做市按真实交易所条件:订单速率 1200/分 + 过滤器跳过计数
        from backend.services.market_maker.venue_filters import OrderRateLimit
        self._venue_ops = OrderRateLimit()
        self._venue_429 = 0
        self._venue_filter_skips = 0
        # [h672] 前端警报事件流(最近 20 条):闸门风暴/熔断/自愈/对账/车道暂停
        self._events: List[Dict[str, Any]] = []

        # [h397 2026-09-27] #9 微价偏离缓存（asterdex_depth_snapshots 顶档微价，
        # 供 F272 mp_block_bp 闸；与新鲜盘口同节流，仅 mp_block_bp>0 时刷新）
        self._mp_cache: Dict[str, float] = {}
        self._mp_cache_ts: float = 0.0
        self._obi_cache: Dict[str, tuple] = {}   # [h899] symbol -> (obi_top, obi_top5)
        self._mp_err: str = ""
        # [F95] 闸门拦截分布（进程内累计）：实盘「哪道闸门在吃成交」的可观测性
        self.skip_counts: Dict[str, int] = {}
        # [R201 2026-09-29] 闸门探针存**模块级** `GATE_PROBES`（见其定义处注释）——
        # `plan_tick` 是**模块级函数**（不是方法）✗ ⇒ 那里没有 `self`，
        # 我第一版在闸门里写 `self.gate_probe_counts[...]` 会 `NameError`，
        # 而且因为它在 `θ>0` 分支里 ⇒ **只有试跑真正开始时才会崩** ✗✗（最坏的一种：
        # 默认关闭时全绿、一启用就炸）。`status()` 从这里读出来发布 ✓。
        # [F339 2026-09-22] 车道级暂停计数（见 tick 循环里的说明）。
        # 为什么单独一个字典而不是塞进 `skip_counts`：`skip_counts` 只记
        # `dec.skip`，而车道暂停的键是 `vol_pause(sigma=0.71)` 这种**带参数的形状**，
        # 两者混在一起会让"单侧闸"与"车道闸"无法区分（本会话已因此误判过一次：
        # 看到 `vol_pause: 1422` 以为车道 σ 闸在工作，实际那是单侧闸）。
        self.lane_pause_counts: Dict[str, int] = {}
        self.lane_pause_last: Dict[str, Any] = {}
        self.day_pnl_usd: float = 0.0
        self.day_pnl_limit_usd: Optional[float] = None
        # [h749 2026-10-03] 冷却式日亏熔断状态(持久化 data/dl_fuse_state.json)
        self._dl_floor: float = 0.0
        self._dl_pause_until: float = 0.0
        self._dl_trip_count: int = 0
        self._dl_fused_universe: List[str] = []
        # [h759 2026-10-03] 实时淘汰快路:命中亏损淘汰判据的币(≤60s 刷新)
        self._decay_block: set = set()
        self._decay_block_ts: float = 0.0
        # [h784 2026-10-04] 崩盘熔断:平仓腿价格项深于 −150bp 的币,立即
        # "只减不加" 30 分钟(隔离连续跳空的崩盘币,不等 4h/30 腿的亏损淘汰)。
        self._crash_block: dict = {}
        self._load_dl_fuse_state()
        self.side_counts: Dict[str, int] = {"both": 0, "one": 0, "none": 0}
        # [F102] 最近若干 tick 的**成交判定输入快照**（环形缓冲，供审计"该成交却没成交"）。
        # 记录的是判定时**实际被检验的挂单**（重挂前的 quote）与区间高低/主动量，
        # 以及本 tick 判定出的成交数。这是把「报价路径」与「判定路径」分开的直接证据。
        # [F98] 挂宽均值（进程内累计）：k_vol>0 后挂宽随宽度变化，必须能直接看到
        # 实盘实际挂多宽——否则「实盘成交比回放少」只能靠猜（挂宽是首要嫌疑）。
        self._w_sum = {"bid": 0.0, "ask": 0.0}
        self._w_n = 0
        # [F232] 分侧计数：`avg_width_bp.bid/ask` 必须各除各侧决策数——
        # 此前两侧都除以 `_w_n`（含单边决策）⇒ 单边行情里未挂侧把另一侧均值稀释
        # （实测 bid 读数 1.86bp < 任何可能的挂宽 ✗）。与回放同改 ✓。
        self._w_n_side = {"bid": 0, "ask": 0}
        self._sigma_sum = 0.0
        # [F251 2026-09-16] 注册表 meta 热更新：params/limits/vol_baseline 的指纹与
        # 上次检查时间。F79 只保证「runner 重建时从注册表覆写」，但注册表随时可能被
        # 人工/进化/锚定脚本更新——旧进程里的 runner 会一直用旧基线（实测 09-15 夜
        # 22:27 重锚波动基准后，在跑 runner 未重建 ⇒ 波动闸按旧基线漏保护快盘窗口，
        # 2 小时 −$24.5）。tick() 每 60s 重读一次注册表，指纹变化才热采用。
        self._meta_fp: str = ""
        self._last_meta_check: float = 0.0
        # [F256 2026-09-16] 成交备注环（最近 60 笔的判定上下文，快盘自解释）
        self.fill_notes: List[Dict[str, Any]] = []
        # [F224 2026-09-15] 全部决策的 σ 均值（含被 vol_pause 拦下的）——
        # `avg_sigma` 只统计**挂出去**的决策 ⇒ 幸存者偏差（实测市场 σ_norm≈2.6 时
        # 读数只有 0.84，会让人误判"波动正常" ✗）。两个口径必须并存并分别标注 ✓。
        self._sigma_all_sum = 0.0
        self._sigma_all_n = 0
        # [F205 2026-09-15] 报价**分支**观测：冻结档（`frozen_width_bp`）与正常档的命中占比
        # + 基准半宽均值。F189 的教训：模型以为 `w_base_bp=12` 生效（挂 ~13bp），
        # 实盘却因冻结档实际只挂 5.5/4.7bp ✗ ⇒ 拿"改了但没生效"的配置承担了 −$63.8 敞口 ✗✗。
        # 这两个读数让"参数→行为"当场可核对，不必再从平均挂宽反推 ✓。
        self._mode_counts: Dict[str, int] = {"normal": 0, "frozen": 0, "unknown": 0}
        self._base_sum = 0.0
        # [F102] 最近若干 tick 的**成交判定输入快照**（环形，供审计"该成交却没成交"）：
        # 记录判定时**实际被检验的挂单**（重挂前的 quote）与区间高低/主动量、成交数。
        # 这是把「报价路径」与「判定路径」分开的直接证据（此前只能靠外部复算，不可靠）。
        self.recent_ticks: List[Dict[str, Any]] = []
        # [F105 2026-09-14] 「穿越→成交」转化率累计（进程内）：残留的实盘/回放速率差
        # 需要长窗口统计才能定位——短窗（12 tick）样本太小。分类：本侧穿越且成交 /
        # 穿越但腿量低于最小名义 / 穿越但挂单被判陈旧清除（F89a）。
        self.cross_counts: Dict[str, int] = {
            "cross_buy": 0, "cross_sell": 0, "fill_buy": 0, "fill_sell": 0,
            "nofill_min_notional": 0, "nofill_stale": 0, "nofill_other": 0,
            # [F107] 判定区间空/非空（数据层缺陷导致的成交机会损失，可观测）
            "win_judged": 0, "win_empty": 0,
            # [F134] 地板价减仓腿漏斗（引擎账内配对口径）：穿越后成交/未成交/未穿越/空区间
            "floor_cross_fill": 0, "floor_cross_nofill": 0,
            "floor_nocross": 0, "floor_win_empty": 0}
        # [F85] 复利比例：>0 时每 tick 用模拟账户权益 × 比例 决定腿量（0=固定）
        self.compound_ratio: float = float(params.compound_ratio) if hasattr(
            params, "compound_ratio") and params.compound_ratio else 0.0
        # ── [整顿轮·T58 2026-10-06] `compound_ratio` 加 env 覆盖 ──────────────
        #
        # **这是"口子"的真正根**。完整证据链（`which_cap_binds.py` 等）：
        #   `runner.py:6254`  self.fill_notional = max(10, **compound_ratio × equity**)
        #   `runner.py:1176`  _eff_notional ≈ fill_notional        （基础腿量）
        #   `_HARD_POS_MAX_MULT = 2.0` ⇒ 单币持仓上限 = 2 × 基础腿量
        #   `active_flow` 的 `min(cap, equity×5%)` 与 `probe_notional_usd`
        #   `max_leg_notional_mult`（F338）
        #
        # 实测：equity $10,015 时腿量恒为 **$501** = 0.05 × 10015
        # ⇒ **所有其它上限都是围绕这个"基础腿量"的倍数**，
        #   所以我改 `MM_PROBE_EQUITY_FRAC`(0.25) 与 `MM_AF_LEG_CAP_PCT`(0)
        #   **都没有效果** —— 不是开关坏了，是**根不在这里**。
        #
        # 而引擎自己的风险模型 `notional_cap_usd(stop=15bp,n=1)` 给的是 **$33,390**
        # ⇒ 人工把口子压小了 **66.6 倍**，正是用户说的
        #   「不要怕亏钱就弄什么缩小口子，影响交易的决定」。
        #
        # 本 env 只做**实验入口**，默认**不改行为**（不设 = 用注册表值）。
        # 回滚：删掉 `MM_COMPOUND_RATIO` 即回到注册表值。
        _cr_env = os.getenv("MM_COMPOUND_RATIO")
        if _cr_env:
            try:
                _cr = float(_cr_env)
                if _cr > 0:
                    self.compound_ratio = _cr
                    logger.warning("[T58] compound_ratio 被 env 覆盖: %s -> %s"
                                   "（腿量基数 %.0f -> %.0f）",
                                   params.compound_ratio, self.compound_ratio,
                                   float(params.compound_ratio or 0.0) * 10015.0,
                                   _cr * 10015.0)
            except ValueError:
                pass
        # [F176 2026-09-15] 挂单历史（每币最近若干条）：
        # 成交桶改为"桶结束后才落库"（F171 ✓）后，桶比 tick 晚 15~30s 可见 ⇒
        # 必须**延迟一档判定**：拿 L 个桶之前那张单，去判它当时真正存续的分片 ✓。
        # 这里只保留内存（每 tick 重建即可，无需持久化 ✓）。
        self._quote_hist: Dict[str, List[Dict[str, float]]] = {}
        # [F279 2026-09-16] 断点补齐计数（进程内）：live 每次重启都会在 mid_hist 里
        # 留一条**跨越停机时间的伪收益**（持久化的历史停在停机前，重启后第一条新快照
        # 直接接上）⇒ 已实现波动被抬高 ⇒ `vol_pause` 把该币站开整整一个窗口
        # （15s 网格 × 20 期 = 5 分钟）。模型侧数据连续、**没有**这条伪收益 ⇒
        # 这就是"模型报价时间 > 实盘报价时间"的一个确定性来源。
        self.gap_splices = 0          # 触发补齐的断点次数（含回退为清空）
        self.gap_splice_points = 0    # 补回来的中间快照总点数
        self.gap_last: Dict[str, Any] = {}   # 每币最近一次断点 {gap_ms, spliced, ts}
        # L=0 ⇒ 完全旧行为（可用环境变量即时回退 ✓）
        try:
            self.judge_lag_buckets: int = max(0, int(os.getenv("MM_JUDGE_LAG_BUCKETS", "3")))
        except Exception:
            self.judge_lag_buckets = 3

    def _maybe_refresh_universe(self, now_ts: float) -> None:
        """[h680] **自驱动宇宙雷达**:做市进程内按节奏评估/替换宇宙,
        不依赖 Windows 计划任务(DSH_HFT_UNIVERSE_SELECT 只是兜底)。

        节奏(env 可调):
          · 每 300s:轻量衰减巡检(现役币 spread_ok_share / Q / 敞口)⇒ 有衰减立即全量评估;
          · 每 1800s:全量评估(择优替换受 2h 最短驻留约束);
        替换后写 meta.symbols + ops_changes(+微试跑记录),下一 tick 热采用。
        """
        try:
            # [2026-10-09 用户规则] 手动固定名单:meta.universe_frozen=true ⇒
            # 雷达/优化器一律不改名单,只用固定币跑。恢复自动换币:删掉该标记。
            from backend.services import lane_registry as reg
            _lane0 = reg.get_lane(self.lane_id) or {}
            _meta0 = dict(_lane0.get("meta") or {})
            if _meta0.get("universe_frozen") or \
                    _meta0.get("universe_source") == "manual_fixed":
                self._radar_state = {"frozen": True, "as_of": now_ts,
                                     "symbols": list(_meta0.get("symbols") or [])}
                return
            # [h696] 评估节奏 30min → **15min**(用户:选币/淘汰过于滞后);衰减巡检 5min
            # [h767 2026-10-03 用户指令"全面提速"] 15min → **5min**;衰减巡检 5min → 2min。
            # ⚠️ 全量评估实测 ~140s,会占住 tick;5 分钟一轮意味着 ~47% 的时间在评估。
            #    测试阶段接受(要快速积累换币/衰减样本);实盘阶段应调回 900s。
            # [2026-10-08 用户规则] 选币实时化:全量评估 300s→60s,衰减巡检 120s→30s。
            # 趋势变化以秒计,5 分钟太慢(等评估完行情已经过了)。
            # [2026-10-09 重复来回做市] 节奏调回慢档:60→300 / 30→120。
            # ping-pong 时代的选币证据是 rt_bp 往返账,十几分钟才变一次;
            # 30s 一轮的"实时换币"是旧方向链的口径(且实测会每分钟把 ping-pong
            # 持仓换出宇宙 ⇒ orphan_taker 吃单砍仓)。刷新本身便宜,慢档省查询
            # 且不再给换币抖动留入口。回滚:env 设回 60/30。
            _every_full = float(os.getenv("MM_UNIVERSE_RADAR_SEC", "300") or 300)
            _every_decay = float(os.getenv("MM_UNIVERSE_DECAY_SEC", "120") or 120)
            _last = float(getattr(self, "_radar_last", 0.0) or 0.0)
            _last_d = float(getattr(self, "_radar_last_decay", 0.0) or 0.0)
            # [h683] 首次调用只**登记时间**并延后:全量评估实测 ~140s,
            # 放在 tick 里会把做市阻塞两分多钟(刚重启时实测卡住)。
            if _last <= 0 and _last_d <= 0:
                self._radar_last = now_ts
                self._radar_last_decay = now_ts
                self._radar_state = {"deferred_first": True, "as_of": now_ts}
                return
            _due_full = (now_ts - _last) >= _every_full
            _due_decay = (now_ts - _last_d) >= _every_decay
            if not _due_full and not _due_decay:
                return
            # [h683] 评估放**后台线程**(daemon):绝不阻塞 tick 循环
            if getattr(self, "_radar_busy", False):
                return
            self._radar_busy = True

            def _radar_work():
                try:
                    if float(getattr(self.limits, "active_flow_mode", 0.0) or 0.0) > 0:
                        self._refresh_flow_universe(now_ts)
                        self._radar_last = now_ts
                        self._radar_last_decay = now_ts
                        return
                    import importlib.util as _ilu
                    _spec = _ilu.spec_from_file_location(
                        "h329_selector_v5",
                        Path(__file__).resolve().parents[3] / "scripts"
                        / "h329_selector_v5.py")
                    _v5 = _ilu.module_from_spec(_spec)
                    _spec.loader.exec_module(_v5)
                    # [h709] 槽位数可配(默认 4;毒性行情靠**广度**提腿速,不放松闸门)
                    _slots = int(getattr(self.limits, "universe_slots", 4) or 4)
                    prop = _v5.evaluate(self.lane_id, slots=_slots, rev_slots=3,
                                        hours=float(_v5.ROLL_HOURS))
                    self._radar_last_decay = now_ts
                    if prop.get("frozen"):
                        self._radar_state = {"frozen": True, "as_of": now_ts}
                        return
                    cur = set(prop.get("current") or [])
                    new = list(prop.get("proposed") or [])
                    decayed = list(prop.get("decayed_out") or [])
                    if set(new) == cur:
                        self._radar_state = {"changed": False, "as_of": now_ts,
                                             "symbols": sorted(cur)}
                        self._radar_last = now_ts
                        return
                    if not decayed and _last > 0 and (now_ts - _last) < 7200.0:
                        self._radar_state = {"changed": False, "blocked_dwell": True,
                                             "as_of": now_ts, "proposed": new}
                        return
                    ok = _v5.apply_proposal(self.lane_id, prop, write_matrix=False)
                    self._radar_last = now_ts
                    # [h701 2026-10-02] 换币后**立即同步实盘车道** —— 用户要求
                    # "实盘跑的与模拟盘配置一样"。此前雷达只写 paper 的 meta.symbols,
                    # 实盘要等人工跑 sync_live_cfg.py ⇒ 换币后两车道宇宙漂移
                    # (实测 paper=[XRP,ENA,UNI,ONDO] vs live=[XRP,DOGE,LTC,SUI])。
                    if ok:
                        try:
                            import importlib.util as _ilu2
                            _s2 = _ilu2.spec_from_file_location(
                                "sync_live_cfg",
                                Path(__file__).resolve().parents[3] / "scripts"
                                / "sync_live_cfg.py")
                            _m2 = _ilu2.module_from_spec(_s2)
                            _s2.loader.exec_module(_m2)
                            _m2.main()
                            logger.info("[h701] 换币后已同步实盘车道(symbols+params)")
                        except Exception as _e2:
                            logger.warning("[h701] 换币后同步实盘失败: %s", _e2)
                    self._radar_state = {"changed": bool(ok), "as_of": now_ts,
                                         "symbols": new,
                                         "decay_driven": bool(decayed),
                                         "decayed": decayed}
                    if ok:
                        self._push_event(
                            "universe_swap",
                            f"宇宙替换 → {' '.join(new)}"
                            + (f"(衰减:{','.join(decayed)})" if decayed else "(择优)"),
                            now_ts)
                except Exception as e:   # 雷达失败绝不影响做市主循环
                    self._radar_state = {"error": f"{type(e).__name__}: {e}"}
                    logger.warning("[h680] 自驱动雷达失败: %s", e)
                finally:
                    self._radar_busy = False

            import threading
            threading.Thread(target=_radar_work, daemon=True,
                             name="universe-radar").start()
            return
        except Exception as e:   # 雷达调度失败绝不影响做市主循环
            self._radar_state = {"error": f"{type(e).__name__}: {e}"}
            logger.warning("[h680] 自驱动雷达调度失败: %s", e)

    def _refresh_flow_universe(self, now_ts: float) -> None:
        """主动流名单：观察池按成交额，交易位只收样本外仍赚钱的币。不跑做市选币。"""
        import json
        from pathlib import Path

        from backend.services import lane_registry as reg
        from backend.services.market_maker.flow_rules import load_learn_params
        from backend.services.market_maker.flow_universe import (
            exit_only_symbols, ranked_watch_pool, select_trading_slots, touch_admitted,
        )

        root = Path(__file__).resolve().parents[3]
        try:
            screen = json.loads((root / "data" / "vol_top20.json").read_text(encoding="utf-8"))
        except Exception as exc:
            self._radar_state = {"error": f"no_watch_pool: {exc}", "as_of": now_ts}
            return
        rows = list(screen.get("detail") or [])
        stop_bp = float(load_learn_params(root).get("disaster_stop_cap_bp") or 40.0)
        if screen.get("watch_pool") and not rows:
            pool = [str(s).upper() for s in screen.get("watch_pool") or []]
        else:
            pool = ranked_watch_pool(rows, stop_bp)
        sit_doc = None
        try:
            sit_doc = json.loads((root / "data" / "flow_situation_last.json").read_text(encoding="utf-8"))
        except Exception:
            sit_doc = None
        sit_fresh = False
        if isinstance(sit_doc, dict):
            try:
                sit_fresh = (now_ts - float(sit_doc.get("ts") or 0.0)) < 6 * 3600
            except (TypeError, ValueError):
                sit_fresh = False
        # ── [h898 2026-10-07] scalp 宇宙优先读 universe_optimizer 的 scout 文件 ──
        # 病根(实测):本函数的情境路径把 flow_situation_last.json 的 39 个**做市口径**
        # 币直接写进 meta.symbols(MM_SIT_SLOTS 默认 0 = 不截断)⇒ 把 universe_optimizer
        # 按"实时逐笔率+成绩单"选出的 scalp 宇宙**每 5 分钟顶回 39 个**(双生产者互写)。
        # 修法:scout 文件新鲜(<20min)就用它的名单(快进快出口径:实时流+成绩单),
        # meta.symbols 仍由本函数统一写(含 orphan 强平/驻留)⇒ 单一写者不打架。
        # 回滚:删掉 data/universe_optimizer.json 或让它过期 ⇒ 自动回退情境/槽位路径。
        # 注:[2026-10-09 进化重挂] PP 证据路径(下方第一个分支)优先于本路径；
        # 本路径是 PP 文件缺失/过期时的回退。
        _opt_proposed = None
        try:
            _opt = json.loads((root / "data" / "universe_optimizer.json")
                              .read_text(encoding="utf-8"))
            if (now_ts - float(_opt.get("ts_epoch") or 0.0)) < 20 * 60 \
                    and isinstance(_opt.get("after"), list) and _opt["after"]:
                _opt_proposed = [str(s).upper() for s in _opt["after"]]
        except Exception:
            _opt_proposed = None
        # ══ [2026-10-09 进化重挂] ping-pong 选币：rt_bp 往返账证据**优先** ══
        # 判据 = 该币 PP 往返的 胜率/赚亏幅度（pp_symbol_stats.json，由
        # scripts/pp_learn_tables.py 生产，h817 训练时隙每 ~20 分钟刷新）。
        # MM_PP_UNIVERSE 开启(默认 1)时：
        #   · 表新鲜(<45min) ⇒ 按 PP 证据选位；
        #   · 表过期/缺失   ⇒ **冻结现名单**（绝不吃回旧 AI 换币链——实测表
        #     过期 1 小时后 scout 重新接管，每 30 秒换币把 ping-pong 持仓换出
        #     宇宙 ⇒ orphan_taker 吃单砍仓 9 次）。
        # 回滚到旧链：MM_PP_UNIVERSE=0。
        _pp_uni_on = str(os.getenv("MM_PP_UNIVERSE", "1") or "1").strip().lower() not in (
            "0", "false", "no", "off")
        _pp_proposed = None
        _pp_new_in: List[str] = []
        _pp_frozen = False
        if _pp_uni_on:
            _lane0 = reg.get_lane(self.lane_id) or {}
            _meta0 = dict(_lane0.get("meta") or {})
            _pp_cur = [str(s).upper() for s in
                       (_meta0.get("symbols") or self.symbols or [])]
            try:
                _ppd = json.loads((root / "data" / "pp_symbol_stats.json").read_text(
                    encoding="utf-8"))
                if (now_ts - float(_ppd.get("ts") or 0.0)) < 45 * 60:
                    from backend.services.market_maker.flow_universe import (
                        pp_membership, select_pp_slots)
                    _pp_adm = {str(k).upper(): float(v) for k, v in
                               (_meta0.get("flow_admitted_at") or {}).items()}
                    _pp_proposed, _pp_new_in = select_pp_slots(
                        pool, _ppd, _pp_cur, _pp_adm, now_ts)
                else:
                    _pp_frozen = True
                    _pp_proposed = list(_pp_cur)
            except Exception:
                _pp_frozen = True
                _pp_proposed = list(_pp_cur)
        if _pp_proposed is not None:
            proposed = list(_pp_proposed)
            new_in = list(_pp_new_in)
            admitted = {}
            sit_fresh = False   # 走非情境的 admitted 读取口径(从 meta 读币龄)
            lane = reg.get_lane(self.lane_id) or {}
            meta = dict(lane.get("meta") or {})
            if not _pp_frozen:
                # 活币筛：60s 没真实成交且无仓的死币摘掉，空出的槽位按观察池
                # 成交额顺序补进（新币没有 PP 样本 ⇒ watch 照跑攒数，fail-open）。
                try:
                    _now_ms0 = int(now_ts * 1000)
                    _alive = []
                    for _s in proposed:
                        try:
                            _n = int(self._trades_60s(_s, _now_ms0) or 0)
                        except Exception:
                            _n = 0
                        _has_pos = abs(float((self.states.get(_s).qty
                                              if self.states.get(_s) else 0.0) or 0.0)) > 1e-12
                        if _n >= 1 or _has_pos:
                            _alive.append(_s)
                    proposed = _alive
                    # [2026-10-09] 槽位上限 = min(车道槽位, 选币纪律 4)：
                    # flow_universe.SLOT_CAP=4 是「交易名单最多 4 个」的既有规矩；
                    # limits.universe_slots=24 是旧 AI 换币链的宽松值，PP 时代不沿用
                    # （T13 实测：名单摊到几十个币 ⇒ 每条腿赚不到 1 美分）。
                    _slot = min(int(getattr(self.limits, "universe_slots", 6) or 6),
                                int(getattr(self.limits, "pp_slot_cap", 4) or 4))
                    if len(proposed) < _slot:
                        for _s in (pool or []):
                            _b = str(_s).upper()
                            if _b in proposed:
                                continue
                            # [2026-10-09 修] 补进只允许 watch/eligible：
                            # 有 PP 负证据(drop)的币不得靠"活币"混回来
                            # （实测 US n=21 亏>赚被判 drop，又被补进拉回名单）。
                            _b_stat = (_ppd.get("coins") or {}).get(_b) if isinstance(_ppd, dict) else None
                            if _b_stat and pp_membership(
                                    _b, _b_stat, now_ts, {}) == "drop":
                                continue
                            try:
                                _n = int(self._trades_60s(_b, _now_ms0) or 0)
                            except Exception:
                                _n = 0
                            if _n < 3:
                                continue
                            proposed.append(_b)
                            new_in.append(_b)
                            if len(proposed) >= _slot:
                                break
                    self._radar_fill_debug = {
                        "proposed_n": len(proposed), "slot": _slot,
                        "new_in": list(new_in), "evidence": "pp_rt_bp",
                    }
                except Exception:
                    pass
            else:
                self._radar_fill_debug = {
                    "proposed_n": len(proposed), "frozen": True,
                    "evidence": "pp_rt_bp(stale_freeze)",
                }
        elif _opt_proposed is not None:
            proposed = list(_opt_proposed)
            new_in = []
            admitted = {}
            sit_fresh = False   # 走非情境的 admitted 读取口径(从 meta 读币龄)
            # [2026-10-08 修 bug] meta 在这个分支里没定义(它在后面的 else 才定义),
            # 补进/淘汰用到它会 UnboundLocalError ⇒ 被 except 吞掉 ⇒ 名单卡死不更新。
            # 在这里先读一次。
            lane = reg.get_lane(self.lane_id) or {}
            meta = dict(lane.get("meta") or {})
            # [2026-10-08 修] scout 名单也要过活币筛:60s 没真实成交的死币摘掉。
            # 实测 PENDLE/LYN 60s 0 笔被 scout 带进 ⇒ 挂单没人吃 ⇒ 看着像"没交易"。
            try:
                _now_ms0 = int(now_ts * 1000)
                _alive = []
                for _s in proposed:
                    try:
                        _n = int(self._trades_60s(_s, _now_ms0) or 0)
                    except Exception:
                        _n = 0
                    _has_pos = abs(float((self.states.get(_s).qty
                                          if self.states.get(_s) else 0.0) or 0.0)) > 1e-12
                    if _n >= 1 or _has_pos:
                        _alive.append(_s)
                if len(_alive) < len(proposed):
                    proposed = _alive
                # [2026-10-08 修] 活币筛把名单砍到不足槽位时,**继续走补进**,
                # 不能停在这里(否则名单只剩 1 个死等)。把 proposed 置空标记,
                # 让下面补进逻辑把真活币填进来。
                if len(proposed) < int(getattr(self.limits, "universe_slots", 6) or 6):
                    pass  # 继续往下走补进
            except Exception:
                pass
            # ── [2026-10-08 重设计] 实时末位淘汰:每拍用实时 OFI 重算,不等 scout ──
            # 旧病根:scout 文件 15min 才更新 + state 里没有 ofi 字段 ⇒ 淘汰判不出。
            # 修法:用 worker 自己的 _ofi_60s(实时,5s 缓存)算在册币趋势分,
            # 跌破留任线立刻摘出;候选池里实时趋势最强的补上。持仓币只平仓不开新仓。
            try:
                from backend.services.market_maker.trend_score import (
                    trend_score as _ts_calc, SCORE_STAY as _STAY, SCORE_ENTER as _ENTER)
                _cur = [str(s).upper() for s in
                        (meta.get("symbols") or self.symbols or [])]
                _pos = {s: float(st.qty or 0.0) for s, st in self.states.items()}
                _now_ms = int(now_ts * 1000)
                # 实时算每个在册币的趋势分
                _scores = {}
                for _s in _cur:
                    try:
                        _ofi = float(self._ofi_60s(_s, _now_ms) or 0.0)
                    except Exception:
                        _ofi = 0.0
                    _scores[_s] = _ts_calc(_ofi, None, None)["score"]
                # 淘汰:跌破留任线且无仓 —— [h906 用户"几分钟后就没腿速"]
                # 加**宽限期**:连续 N 秒低于留任线才摘出。
                # 旧行为每拍秒踢(午夜 OFI 噪声一抖就换币),换进来的币要热机
                # ⇒ 腿速从 100/h 掉到 0。宽限 5 分钟,只摘"持续转弱"的币。
                _grace = float(os.getenv("MM_RADAR_WEAK_GRACE_SEC", "300") or 0.0)
                _weak_since = getattr(self, "_weak_since", None)
                if _weak_since is None:
                    _weak_since = {}
                    self._weak_since = _weak_since
                _weak = []
                for s in _cur:
                    _is_weak = (_scores.get(s, 0.0) < _STAY
                                and abs(_pos.get(s) or 0.0) <= 1e-12)
                    if _is_weak:
                        _ws = float(_weak_since.get(s) or now_ts)
                        if now_ts - _ws >= _grace:
                            _weak.append(s)
                        _weak_since[s] = _ws
                    else:
                        _weak_since.pop(s, None)
                if _weak:
                    proposed = [s for s in proposed if s not in _weak]
                    for _s in _cur:
                        if _s not in proposed and _s not in _weak \
                                and abs(_pos.get(_s) or 0.0) > 1e-12:
                            proposed.append(_s)
                    new_in = [s for s in proposed if s not in _cur]
                    self._push_event("universe_swap",
                                     f"实时淘汰 趋势转弱:{' '.join(_weak)}", now_ts)
                # 补进:候选池里实时趋势够强、且**此刻真有人在交易**的,补到槽位满
                # [2026-10-08 修] 死币(PENDLE/LYN 60s 0 笔)趋势分再高也不进——
                # 挂单没人吃等于白挂。必须近 60s 有真实成交才算活币。
                _slot = int(getattr(self.limits, "universe_slots", 6) or 6)
                if len(proposed) < _slot:
                    _cands = []
                    for _s in (pool or []):
                        _b = str(_s).upper()
                        if _b in proposed or _b in _cur:
                            continue
                        try:
                            _ofi = float(self._ofi_60s(_b, _now_ms) or 0.0)
                        except Exception:
                            continue
                        # 近 60s 成交笔数(活币门槛):_ofi_60s 返回 0 且没量 ⇒ 死币跳过
                        try:
                            _n = int(self._trades_60s(_b, _now_ms) or 0)
                        except Exception:
                            _n = 0
                        if _n < 3:
                            continue   # 60s 不到 3 笔 = 死币,不进
                        # [2026-10-08 用户规则2] 有明确趋势概率信号的币优先保进。
                        # PLAY 有 buy 信号(edge 0.11)却被末位淘汰踢掉 ⇒ 名单跟不上信号。
                        # 趋势概率判出方向(buy/sell)+ 活币 ⇒ 直接进,不看 OFI 趋势分。
                        _prob_side = None
                        try:
                            _prob_side = self._prob_side_for(_b)
                        except Exception:
                            _prob_side = None
                        if _prob_side in ("buy", "sell"):
                            _cands.append((999.0, _b))   # 有信号排最前
                            continue
                        _sc = _ts_calc(_ofi, None, None)["score"]
                        if _sc >= _ENTER:
                            _cands.append((_sc, _b))
                    _cands.sort(reverse=True)
                    for _sc, _b in _cands[: max(0, _slot - len(proposed))]:
                        proposed.append(_b)
                        new_in.append(_b)
                    # 留痕:补进结果写进雷达状态,便于排查
                    self._radar_fill_debug = {
                        "proposed_n": len(proposed), "cands": len(_cands),
                        "slot": _slot, "new_in": list(new_in),
                    }
            except Exception as _e:
                self._radar_fill_debug = {"error": f"{type(_e).__name__}: {_e}"}
        elif sit_fresh:
            # ── [整顿轮·T13 2026-10-05] 情境路径也必须遵守**槽位上限** ──
            #
            # 病根（实测）：situation 文档存在且新鲜时，名单被写成
            # `sit_doc["coins"][:40]` —— 一个**硬编码 40**，完全绕开
            # `select_trading_slots` 的 `SLOT_CAP=4`（flow_universe.py:14）。
            # 实测北京时间 17:03 的文档：新鲜（0.37h）、**39 个币**，
            # 于是 `limits.universe_slots=6` 形同虚设。
            #
            # 后果（这才是"不赚钱"的结构性根因）：
            #   单笔风险预算 = equity × 0.5% / 止损
            #                = $302.73 × 0.005 / 40bp = **$378.41**
            #   把它摊到 39 个标的 ⇒ **每个只剩 $9.70 名义**
            #   ⇒ 单腿 edge（1~14bp）× $9.70 ≈ **不到 1 美分**
            #   ⇒ 账户规模 $302 被摊薄成"每条腿赚几分钱"，永远长不大。
            #
            # 而实测资金效率差 100 倍：
            #   最好：牛来 38.25 / AAVE 19.65 / 2Z 15.68 / LYN 11.91 / BTW 9.59（每万名义净额）
            #   最差：PENDLE −28.75 / ENJ −27.72 / TRUMP −20.95 / INJ −17.01
            #   而 **BTC+ETH 占了 46.5% 的名义，贡献的却是负收益**
            #   ⇒ 钱被摊在最差的标的上。
            #
            # 修法：情境路径同样只保留 `universe_slots` 个（默认 6），
            # 并**按已验证的样本外 edge 排序**取前 N（与 `select_trading_slots`
            # 的排序键一致：`flow_rules.oos_conditional_mean` 的 `mean_y`）。
            # 情境文档里的币本身已经过情境筛选，所以这是"在合格者里挑最好的"，
            # 不是新增门禁（规矩第 4.1 条）。
            #
            # 回滚：`MM_SIT_SLOTS=0` ⇒ 恢复旧的 `[:40]` 行为。
            _sit_syms = [str(s).upper() for s in (sit_doc.get("coins") or {})]
            try:
                # ⚠️ 用模块级 `os`（第 22 行 `import os`）。
                # `_os` 只是 `plan_tick` **函数内**的局部别名（第 1209 行），
                # 本方法取不到 —— 首版误用 `_os` 导致
                # `NameError: name '_os' is not defined`，整条刷新静默失败
                # （radar_state 里能看到该错误）。已修正，并加运行时测试锁定。
                _sit_cap = int(os.getenv("MM_SIT_SLOTS", "0") or 0)
            except ValueError:
                _sit_cap = 0
            if _sit_cap <= 0:
                try:
                    _sit_cap = int(getattr(self.limits, "universe_slots", 6) or 6)
                except Exception:
                    _sit_cap = 6
            if _sit_cap > 0 and len(_sit_syms) > _sit_cap:
                # 样本外 edge 来源于 `flow_gate_last.json` 的 gates；
                # 情境分支此前不加载它，这里显式加载一次（失败则退化为原顺序）。
                _sit_gates: Dict[str, Any] = {}
                try:
                    _gd = json.loads((root / "data" / "flow_gate_last.json").read_text(
                        encoding="utf-8"))
                    _sit_gates = dict(_gd.get("gates") or {})
                except Exception:
                    _sit_gates = {}

                def _sit_edge(_s: str) -> float:
                    try:
                        from backend.services.market_maker.flow_rules import (
                            oos_conditional_mean as _ocm,
                        )
                        _g = _sit_gates.get(_s) or {}
                        _oos = _g.get("oos") or {}
                        _my = _ocm(_oos.get("values") or [], _oos.get("hits") or [],
                                   int(_oos.get("split") or 0),
                                   int(_oos.get("embargo") or 0))[0]
                        return -1e9 if _my is None else float(_my)
                    except Exception:
                        return 0.0
                _ranked = sorted(_sit_syms, key=_sit_edge, reverse=True)
                proposed = _ranked[:_sit_cap]
            else:
                proposed = _sit_syms
            new_in = []
            admitted = {}
        else:
            try:
                gate_doc = json.loads((root / "data" / "flow_gate_last.json").read_text(encoding="utf-8"))
                gates = dict(gate_doc.get("gates") or {})
            except Exception:
                gates = {}
            lane = reg.get_lane(self.lane_id) or {}
            meta = dict(lane.get("meta") or {})
            current = [str(s).upper() for s in (meta.get("symbols") or self.symbols or [])]
            admitted = {str(k).upper(): float(v) for k, v in (meta.get("flow_admitted_at") or {}).items()}
            proposed, new_in = select_trading_slots(pool, gates, current, admitted, now_ts)
        lane = reg.get_lane(self.lane_id) or {}
        meta = dict(lane.get("meta") or {})
        current = [str(s).upper() for s in (meta.get("symbols") or self.symbols or [])]
        # [2026-10-09 修] **空名单绝不落库**：活币筛 + 补进在冷清市况下可能
        # 把 proposed 清成 []，而 [] 一旦写进 meta.symbols，下一次 get_runner
        # 直接 fail-closed（F249：meta.symbols 为空 ⇒ 不建 runner ⇒ 车道停摆，
        # 实测 22:43 全场停机）。空 ⇒ 保留现名单（冻结）。
        if not proposed and current:
            proposed = list(current)
            new_in = []
            self._radar_fill_debug = {"proposed_n": len(proposed), "frozen": True,
                                      "evidence": "keep_current(empty_guard)"}
        if not sit_fresh:
            admitted = {str(k).upper(): float(v) for k, v in (meta.get("flow_admitted_at") or {}).items()}
        else:
            admitted = {sym: float((meta.get("flow_admitted_at") or {}).get(sym) or now_ts) for sym in proposed}
        admitted = touch_admitted(admitted, proposed, now_ts)
        positions = {s: float(st.qty or 0.0) for s, st in self.states.items()}
        exiting = exit_only_symbols(proposed, positions)
        self._radar_state = {
            "as_of": now_ts, "mode": "active_flow",
            "watch_pool": pool[:12], "symbols": proposed,
            "new_in": new_in, "exit_only": exiting,
            "changed": set(proposed) != set(current),
            "fill_debug": getattr(self, "_radar_fill_debug", None),
        }
        if set(proposed) == set(current) and admitted == {
                str(k).upper(): float(v) for k, v in (meta.get("flow_admitted_at") or {}).items()}:
            return
        meta["symbols"] = proposed
        meta["flow_admitted_at"] = admitted
        meta["flow_watch_pool"] = pool
        ops = list(meta.get("ops_changes") or [])
        from datetime import datetime, timezone
        ops.append({
            "by": "flow_universe", "op": "set_symbols",
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "before": current, "after": proposed,
            "reason": "主动流：每一拍只问眼前这一档，不使用做市价差和形态槽",
        })
        meta["ops_changes"] = ops[-40:]
        reg.register_lane(
            lane_id=self.lane_id,
            mode=lane.get("mode") or "paper",
            status=lane.get("status") or "active",
            meta=meta,
        )
        try:
            import importlib.util as _ilu2
            _s2 = _ilu2.spec_from_file_location(
                "sync_live_cfg", root / "scripts" / "sync_live_cfg.py")
            _m2 = _ilu2.module_from_spec(_s2)
            _s2.loader.exec_module(_m2)
            _m2.sync_flow_lists()
        except Exception as exc:
            logger.warning("[flow_universe] 同步名单到实盘失败: %s", exc)
        if set(proposed) != set(current):
            self._push_event(
                "universe_swap",
                f"主动流交易名单 → {' '.join(proposed) or '（空）'}",
                now_ts)

    def _push_event(self, kind: str, msg: str, now_ts: Optional[float] = None) -> None:
        """[h672] 前端警报事件:去抖(同 kind+msg 60s 内合并计数)。

        ⚠️ 必须放在 `__init__` **之后**:首版把它插在初始化中间,
        后半段初始化被吞进本方法体 ⇒ 每 tick `AttributeError:
        '_last_meta_check'`(worker 连续崩了约 30 分钟,方向卡/全部读数消失)。
        """
        _now = float(now_ts or time.time())
        if self._events and self._events[-1].get("kind") == kind \
                and self._events[-1].get("msg") == msg \
                and _now - float(self._events[-1].get("ts") or 0.0) < 60.0:
            self._events[-1]["ts"] = _now
            self._events[-1]["n"] = int(self._events[-1].get("n") or 1) + 1
            return
        self._events.append({"ts": _now, "kind": kind, "msg": msg, "n": 1})
        if len(self._events) > 20:
            self._events = self._events[-20:]

    def _read_account_equity(self) -> float:
        """[F85] 读模拟账户当前权益（复利模式的腿量/上限基准）。

        [F95 2026-09-14] **必须算上已实现盈亏**：`arbitrage_paper_accounts.total_equity`
        是「交易所分配资本」的口径——`record_paper_leg_fill` 只改 `available_balance`
        与 `realized_pnl`，**从不改 total_equity**（实测 160 笔成交后仍恒为 $300.00）。
        此前直接读 total_equity ⇒ 实盘复利完全失效（腿量永远 $300），而回放按
        running_equity 复利（7 天 $300→$389）——实盘/回放口径不一致。
        口径与回放对齐：**权益 = 分配资本 + 已实现盈亏**（不含浮动盈亏，避免与
        未平仓腿的盯市重复计入）。
        """
        if not self.account_id:
            return 0.0
        try:
            from sqlalchemy import text

            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    row = db.execute(text(
                        "SELECT total_equity, COALESCE(realized_pnl, 0)"
                        " FROM arbitrage_paper_accounts WHERE id=:i"
                    ), {"i": self.account_id}).first()
                    if not row or not row[0]:
                        return 0.0
                    return float(row[0]) + float(row[1] or 0.0)
        except Exception as e:
            logger.warning("[F85] 读账户权益失败: %s", e)
            return 0.0

    # ── 状态持久化 ──
    def ledger_positions(self, marks: Optional[Dict[str, float]] = None) -> Dict[str, dict]:
        """从 `lane_ledger` 重建持仓（账本是事实源）。失败返回空 dict（不抛）。"""
        try:
            from backend.services import lane_ledger
            rows = lane_ledger.open_positions(
                lane_id=self.lane_id, days=30.0, marks=marks or {}, since=None)
            return {str(r.get("symbol") or ""): dict(r) for r in rows
                    if str(r.get("symbol") or "")}
        except Exception as e:  # pragma: no cover
            logger.warning("[F301] ledger_positions 失败(忽略): %s", e)
            return {}

    def _reconcile_loaded_states(self, loaded: Dict[str, SymbolState]) -> Dict[str, Any]:
        """[F301 2026-09-16] 载入运行态后，用**账本**校验库存，分叉时以账本为准。

        事故现场（本会话）：人工按"换宇宙操作性平仓"把 ADA 平掉并写了平仓腿，
        但**运行中的进程持有内存态**，下一次 save_states 就把 `lane_runtime_state`
        覆写回 qty=154.02。随后 worker 重启，`load_states` 直接把这份陈旧状态当成
        持仓装入 ⇒ 5 秒后超过 1 小时超时 ⇒ 车道**又平了一次** ⇒ 账本净额变成
        −154.02（凭空多出一个空头），前端显示 −$29.98。

        根因：`load_states` 只读 `lane_runtime_state`（"重启后不丢库存"），
        而 `lane_ledger` 才是车道级事实源（其 docstring 明写「账本是唯一事实源」）。
        两者分叉时，旧实现无条件相信状态文件。

        修法：装入后逐币与账本重建结果比对；不一致 ⇒ **采用账本数量**并写一条
        零盈亏校正腿（`source=mm_state_reconcile`）留痕，同时清掉 `opened_ts`
        （避免对已被外部平掉的仓位触发超时/止损平仓）。

        边界（刻意保守）：
          · `marks` 传空 ⇒ 账本重建仍返回 qty（价格维度为 0），足以判定数量分叉；
          · 账本查询失败 ⇒ 返回空 ⇒ **不做任何改动**（绝不因为读不到账本就清仓）；
          · 状态里有仓而账本无该币 ⇒ 账本 qty 视为 0（账本无记录 = 无持仓）。
        """
        result = {"checked": 0, "corrected": [], "dropped": [], "skipped": None}
        led = self.ledger_positions()
        if not led:
            # 区分「账本查询失败」与「账本确实没有任何持仓」：前者由异常路径返回 {}
            # 且往往伴随平台级故障，此时宁可不动（fail-safe）。
            try:
                from backend.services import lane_ledger as _ll
                _ll.open_positions(lane_id=self.lane_id, days=1.0)   # 探针
            except Exception as e:
                result["skipped"] = f"ledger_unavailable: {type(e).__name__}: {e}"
                logger.warning("[F301] 账本不可用，跳过启动对账（不做任何改动）: %s", e)
                return result

        for sym, st in list(loaded.items()):
            if not str(sym) or str(sym).isdigit():
                continue          # 脏键保护
            result["checked"] += 1
            q_rt = float(getattr(st, "qty", 0.0) or 0.0)
            q_led = float((led.get(sym) or {}).get("qty") or 0.0)
            scale = max(abs(q_rt), abs(q_led))
            if scale <= 0:
                continue
            # [h669] venue 取整容差:账本按 stepSize 对齐、运行态可能仍是
            # 旧进程的未取整库存 ⇒ 差异 ≤ 1.5×stepSize 视为一致,不得清成本价。
            _tol = 1e-6 * scale
            try:
                from backend.services.market_maker.venue_filters import filters_for
                _step = float(filters_for(str(sym)).get("step_size") or 0.0)
                if _step > 0:
                    _tol = max(_tol, _step * 1.5)
            except Exception:
                pass
            if abs(q_led - q_rt) <= _tol:
                continue
            logger.warning(
                "[F301] 启动对账分叉 %s: runtime=%.8f ledger=%.8f ⇒ 采用账本",
                sym, q_rt, q_led)
            result["corrected"].append({"symbol": sym, "runtime_qty": q_rt,
                                        "ledger_qty": q_led})
            st.qty = q_led
            if abs(q_led) < 1e-12:
                # 账本已平 ⇒ 彻底清掉，否则 opened_ts 会让它再被超时平一次
                st.avg_px = 0.0
                st.avg_mid = 0.0
                st.opened_ts = 0.0
                st.stop_since = 0.0
            else:
                # 账本仍有仓但数量不同：数量以账本为准，成本基准无从得知 ⇒
                # 清掉 opened_ts 与成本，避免用错误的均价做止损判断。
                st.avg_px = 0.0
                st.avg_mid = 0.0
                st.opened_ts = 0.0
                st.stop_since = 0.0
        return result

    def reload_states(self) -> Dict[str, Any]:
        """[F301 2026-09-16] 运行期把运行态重新与账本对齐（不重启进程）。

        为什么需要：`load_states()` 只在进程启动时跑。但持仓可能被**进程之外**的动作
        改变——操作性平仓、对账写调整腿、手工改账本。此时内存态与账本分叉，
        而运行中的进程对此一无所知（本会话的事故正是如此：人工平仓后运行中的进程
        仍持有 qty=154 的内存态，并在下一次 save_states 时把陈旧状态写回 DB）。

        行为：逐币比对**运行时库存 vs 账本重建库存**，不一致 ⇒ 采用账本并清掉成本/
        开仓时刻（防止对已被外部平掉的仓位再触发超时或止损平仓）。返回对账摘要。
        """
        ensure_table()
        try:
            loaded = dict(self.states)
            res = self._reconcile_loaded_states(loaded)
            self.last_reconcile = res
            if res.get("corrected"):
                # 立刻落库，避免分叉被下一次 save_states 覆盖
                try:
                    self.save_states()
                except Exception as e:  # pragma: no cover
                    logger.warning("[F301] 对账后 save_states 失败(忽略): %s", e)
            return res
        except Exception as e:  # pragma: no cover
            self.last_error = f"reload_states: {e}"
            logger.warning("[F301] reload_states 失败: %s", e)
            return {"checked": 0, "corrected": [], "error": str(e)}

    def load_states(self) -> int:
        """从 DB 恢复运行态（重启后不丢库存）。

        [F301 2026-09-16] 恢复后**必须与 `lane_ledger` 对账**：账本是车道级事实源，
        状态文件可能被上一个进程用陈旧内存态覆写（详见 `_reconcile_loaded_states`
        记录的事故）。分叉时以账本为准并写校正腿留痕。

        [F249 2026-09-20] **必须按宇宙过滤**（"幽灵标的"根因之一）：
        此前无条件恢复 DB 里的**每一行** ⇒ 已被移出宇宙的币（实测 ZEC/TAO）会在
        每次重启后被"复活"进 `self.states`，车道继续给它们报价。而 `save_states`
        只增不删 ⇒ 那些行永久驻留 ⇒ **自我维持的循环**：清掉也会回来。
        保留规则：**在宇宙内，或仍有未平仓位**（后者必须保留，否则无法平仓）。
        """
        ensure_table()
        try:
            from sqlalchemy import text

            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    rows = db.execute(text(
                        "SELECT symbol, state_json FROM lane_runtime_state WHERE lane_id=:l"
                    ), {"l": self.lane_id}).mappings().all()
            n = 0
            skipped: list = []
            loaded: Dict[str, SymbolState] = {}
            _universe = set(self.symbols or [])
            for r in rows:
                st = SymbolState.from_dict(dict(r["state_json"] or {}))
                if not st.symbol:
                    st.symbol = str(r["symbol"])
                # 不在宇宙且无仓位 ⇒ 跳过（不复活幽灵标的）
                if _universe and st.symbol not in _universe and abs(float(st.qty or 0.0)) < 1e-12:
                    skipped.append(st.symbol)
                    continue
                loaded[st.symbol] = st
                self.states[st.symbol] = st
                n += 1
            if skipped:
                logger.info("[F249] load_states 跳过 %d 个非宇宙且无仓位的陈旧运行态: %s",
                            len(skipped), sorted(skipped))
            self.last_reconcile = self._reconcile_loaded_states(loaded)
            return n
        except Exception as e:
            self.last_error = f"load_states: {e}"
            logger.warning("[F60] load_states 失败: %s", e)
            return 0

    def backfill_mid_hist(self, *, keep: int = 240) -> int:
        """[F109 2026-09-14] 冷启动补齐 `mid_hist`：滑动窗口类闸门不能在重启后"失明"。

        背景（实测）：趋势闸 `trend_blocked_side`、波动闸 `vol_regime_blocked`（σ 的输入）
        与冻结闸 `0 < slow_range_bp < frozen_max_move_bp` 全部读 `state.mid_hist`。
        运行态持久化里带 `mid_hist`，但**进程重启后它是空的**（或只有重启后积累的那几条）：
        实测 20:30 时实盘 186 条 vs 回放 240 条 ⇒ 冻结闸在趋势行情下会与回放分叉
        （实盘用更短的窗口 ⇒ 更容易判定"冻结" ⇒ 挂 3bp 窄单 ✗），而"冻结"本身就是
        决定挂宽档位的关键信号 ⇒ 直接改变成交与收益。

        做法：从盘口快照表按**每快照一条**（与 tick 循环 F102 的追加口径一致）补最近
        `keep` 条中价，并把 `last_mid_src_ms` 锚到最后一条，避免下一 tick 重复追加。
        只在窗口明显不足时补（不覆盖正在运行中的真实窗口）。
        """
        if keep <= 0:
            return 0
        try:
            from sqlalchemy import text

            from backend.core.tenant import system_identity
            from backend.database.connection import MarketSessionLocal

            filled = 0
            with system_identity():
                with MarketSessionLocal() as db:
                    # [h376 2026-09-27] asterdex 场馆回填源修正：market_orderbook_snapshots
                    # 对 asterdex 只有 XRP 有数据（其余 6 个 #2 新币 24h 零行）⇒
                    # asterdex 改用 asterdex_book_ticker（1s，32 币全），按 15s 桶降采样
                    # （与 tick 循环 F102 的 ~15s 追加口径一致；否则 240 条 1s 数据只覆盖
                    # 4 分钟，趋势闸的 20 期窗口会失真成 20 秒）。
                    _is_aster = str(self.venue or "").lower() == "asterdex"
                    for sym, st in list(self.states.items()):
                        if len(st.mid_hist or []) >= keep:
                            continue
                        if _is_aster:
                            rows = db.execute(text(
                                "SELECT (event_ts_ms/15000)::bigint*15000 AS ts,"
                                " (array_agg(bid_px ORDER BY event_ts_ms DESC))[1] AS bb,"
                                " (array_agg(ask_px ORDER BY event_ts_ms DESC))[1] AS ba"
                                " FROM asterdex_book_ticker"
                                " WHERE symbol=CONCAT(CAST(:s AS TEXT),'USDT')"
                                " AND event_ts_ms > (extract(epoch from now())*1000"
                                "   - 3*3600*1000)::bigint"
                                " AND bid_px>0 AND ask_px>bid_px"
                                " GROUP BY ts ORDER BY ts DESC LIMIT :n"
                            ), {"s": sym, "n": int(keep)}).mappings().all()
                            if not rows:
                                continue
                            mids = [((float(r["bb"]) + float(r["ba"])) / 2.0)
                                    for r in reversed(rows)]
                            st.mid_hist = [m for m in mids if m > 0][-keep:]
                            st.last_mid_src_ms = int(rows[0]["ts"])
                            filled += 1
                            continue
                        rows = db.execute(text(
                            "SELECT timestamp, best_bid, best_ask"
                            " FROM market_orderbook_snapshots"
                            " WHERE exchange=:e AND symbol=:s"
                            " AND best_bid>0 AND best_ask>best_bid"
                            " ORDER BY timestamp DESC LIMIT :n"
                        ), {"e": self.venue, "s": sym, "n": int(keep)}).mappings().all()
                        if not rows:
                            continue
                        mids = [((float(r["best_bid"]) + float(r["best_ask"])) / 2.0)
                                for r in reversed(rows)]
                        st.mid_hist = [m for m in mids if m > 0][-keep:]
                        st.last_mid_src_ms = int(rows[0]["timestamp"])
                        filled += 1
            if filled:
                logger.info("[F109] mid_hist 冷启动补齐: %d 个币 × %d 期", filled, keep)
            return filled
        except Exception as e:  # pragma: no cover - 补历史失败不能挡住车道启动
            logger.warning("[F109] mid_hist 补齐失败(忽略): %s", e)
            return 0

    def _splice_mid_hist(self, st: SymbolState, prev_ms: int, now_ms: int) -> int:
        """[F279 2026-09-16] 停机断点补齐：把 `prev_ms` 与 `now_ms` 之间**缺失的快照**
        插回 `mid_hist`，避免产生一条跨越停机的伪收益。

        现场（本轮实测）：进程重启后 `load_states` 把持久化的 `mid_hist` 原样恢复
        （末尾停在停机前最后一 tick），而 `backfill_mid_hist`（F109）有一条
        `len(mid_hist) >= keep 就跳过` 的短路 ⇒ **重启后不会补**。于是重启后第一条
        新快照与停机前那条中价直接相邻 ⇒ 中间 5~20 分钟的真实价格路径被压缩成
        **一条巨大收益** ⇒ `realized_vol_bp` 被抬高到基准 1.7 倍以上 ⇒
        `vol_regime_blocked` 把该币站开，直到这条伪收益滚出 20 期窗口
        （15s 网格 ⇒ **每次重启约 5 分钟不报价**，两个币一起）。

        模型侧（`portfolio_replay`）读的是**连续**快照序列，没有这条伪收益 ⇒
        实盘比模型少报价、少成交。这是"模型与实盘行为不一致"的一个确定性来源，
        而不是策略差异。

        做法：按时间升序读回 `(prev_ms, now_ms)` 区间的中价插在末尾之前；
        查不到（数据也缺）时退化为**清空窗口**（宁可冷启动，也不要伪收益 ✗）。
        返回补回的点数；≥0 表示处理成功（0 = 退化为清空）。
        """
        try:
            from sqlalchemy import text

            from backend.core.tenant import system_identity
            from backend.database.connection import MarketSessionLocal

            with system_identity():
                with MarketSessionLocal() as db:
                    rows = db.execute(text(
                        "SELECT best_bid, best_ask FROM market_orderbook_snapshots"
                        " WHERE exchange=:e AND symbol=:s"
                        "   AND timestamp > :a AND timestamp < :b"
                        "   AND best_bid>0 AND best_ask>best_bid"
                        " ORDER BY timestamp ASC LIMIT 240"
                    ), {"e": self.venue, "s": st.symbol, "a": int(prev_ms),
                        "b": int(now_ms)}).mappings().all()
            mids = [((float(r["best_bid"]) + float(r["best_ask"])) / 2.0) for r in rows]
            mids = [m for m in mids if m > 0]
            if not mids:
                # 数据本身也缺 ⇒ 冷启动（清空），绝不让伪收益进窗口
                st.mid_hist = []
                return 0
            st.mid_hist = (list(st.mid_hist or []) + mids)[-240:]
            return len(mids)
        except Exception as e:  # pragma: no cover - 补齐失败不得挡住 tick
            logger.warning("[F279] 断点补齐失败(退化为清空): %s", e)
            st.mid_hist = []
            return 0

    def orphan_states(self) -> Dict[str, SymbolState]:
        """[F90 2026-09-14] 孤儿持仓 = 不在当前宇宙、但运行态里仍有非零仓位。

        事故背景：把 DOGE 移出宇宙后，`lane_runtime_state` 的历史行仍被
        `load_states` 读进 `self.states`，而 tick 主循环、共享库存账本（净敞口
        上限）、`fetch_market` 全部只遍历 `self.symbols` ⇒ 该仓位既**不进风险
        上限**、也**永远不会被平掉**、盈亏**永不实现**。当时 DOGE 恰好是空仓，
        所以只表现为状态里的僵尸行；若带仓移除则是静默漏仓。
        """
        out: Dict[str, SymbolState] = {}
        for s, st in self.states.items():
            if s in self._symbol_set:
                continue
            if abs(float(getattr(st, "qty", 0.0) or 0.0)) > 1e-12:
                out[s] = st
        return out

    def risk_symbols(self) -> List[str]:
        """[F90] 计入共享风险账本的币种 = 在营宇宙 + 孤儿持仓（风险不可见是最危险的）。"""
        return list(self.symbols) + list(self.orphan_states())

    def prune_flat_orphans(self) -> List[str]:
        """[F90] 丢弃**已平掉**的孤儿运行态（内存 + DB），避免死币种被反复写回。"""
        dead = [s for s in list(self.states)
                if s not in self._symbol_set
                and abs(float(getattr(self.states[s], "qty", 0.0) or 0.0)) <= 1e-12]
        for s in dead:
            self.states.pop(s, None)
            self._seg_watermark.pop(s, None)
        if dead:
            try:
                from sqlalchemy import text

                from backend.core.tenant import system_identity
                from backend.database.connection import SessionLocal

                with system_identity():
                    with SessionLocal() as db:
                        for s in dead:
                            db.execute(text(
                                "DELETE FROM lane_runtime_state"
                                " WHERE lane_id=:l AND symbol=:s"
                            ), {"l": self.lane_id, "s": s})
                        db.commit()
            except Exception as e:
                logger.warning("[F90] prune_flat_orphans DB 清理失败: %s", e)
        return dead

    def save_states(self) -> None:
        ensure_table()
        try:
            import json

            from sqlalchemy import text

            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            # [h669 自愈护栏] 有仓位但成本价≤0 的状态不得持久化(现场事故:
            # UNI/ENA 曾以 avg_px=0 落盘,面板显示"开仓 0.000000")。落盘前
            # 用账本口径(lane_ledger.current_positions)校正成本价。
            _heal = {}
            for _sym, _st in self.states.items():
                if abs(float(_st.qty or 0.0)) > 1e-12 \
                        and float(_st.avg_px or 0.0) <= 0:
                    _heal[_sym] = _st
            # [h848 用户"调高速度"] 自愈节流:`lane_ledger.open_positions` 是**全账本
            # 扫描**(实测 1816ms,随账本增长更慢),而自愈条件(有仓但成本价≤0)
            # 在探索模式下经常命中 ⇒ 每 tick 花 1.8 秒只为修一个显示字段。
            # 现在最多每 60 秒跑一次自愈(成本价缺失并不影响引擎决策:
            # 止损/离场用的是 state.avg_px,而它是下一笔成交时就会写入的)。
            global _HEAL_LAST_TS
            if _heal and (time.time() - _HEAL_LAST_TS) < 60.0:
                _heal = {}
            if _heal:
                _HEAL_LAST_TS = time.time()
                try:
                    from backend.services import lane_ledger
                    # [h737 2026-10-03 修 bug] 原调用 `current_positions(...)` 不存在
                    # ⇒ 每次 AttributeError 被静默吞掉 ⇒ 自愈从未运行(ARB 开仓价
                    # 0.000000 持久化的直接原因)。正确函数是 open_positions(关键字参数)。
                    for _pos in lane_ledger.open_positions(lane_id=self.lane_id):
                        _st0 = _heal.get(str(_pos.get("symbol") or "").upper())
                        if _st0 is None or float(_pos.get("avg_px") or 0.0) <= 0:
                            continue
                        _px0 = float(_pos["avg_px"])
                        # [h669 修正] ±15% 现价 sanity:被污染的账本旧腿可能把
                        # 几小时前的价格当成本价 ⇒ 注入后重启会幽灵止盈(实测
                        # ENA 0.217→0.272 造成 +2430bp 假利润)。偏离过大不注入。
                        _fb = self._fresh_book_for(str(_pos.get("symbol") or ""))
                        if _fb and _fb[0] > 0 and _fb[1] > _fb[0]:
                            _mid_now = (float(_fb[0]) + float(_fb[1])) / 2.0
                            if _mid_now > 0 and abs(_px0 / _mid_now - 1.0) > 0.15:
                                logger.warning("[h669] 自愈拒绝 %s:账本成本价 %.6f"
                                               " 偏离现价 %.6f 超 15%",
                                               _st0.symbol, _px0, _mid_now)
                                continue
                        _st0.avg_px = _px0
                        _st0.avg_mid = float(_pos.get("avg_mid") or 0.0)
                        logger.warning("[h669] 状态自愈 %s avg_px=%.6f",
                                       _st0.symbol, _st0.avg_px)
                except Exception as e:
                    logger.warning("[h669] 状态自愈取数失败: %s", e)

            with system_identity():
                with SessionLocal() as db:
                    # [h844 用户"调高速度"] 批量 UPSERT:此前是 33 条 execute + 1 commit,
                    # 每条 SQLAlchemy 往返约 1.5ms ⇒ 实测 save_states 56ms/tick。
                    # 改成 executemany(一次往返带全部参数)⇒ 预计降到 ~10ms。
                    _rows = [{"l": self.lane_id, "s": st.symbol,
                              "j": json.dumps(st.to_dict(), ensure_ascii=False)}
                             for st in self.states.values()]
                    if _rows:
                        db.execute(text(
                            "INSERT INTO lane_runtime_state (lane_id, symbol, state_json, updated_ts)"
                            " VALUES (:l, :s, CAST(:j AS JSONB), now())"
                            " ON CONFLICT (lane_id, symbol) DO UPDATE SET"
                            " state_json=EXCLUDED.state_json, updated_ts=now()"
                        ), _rows)
                    db.commit()
        except Exception as e:
            self.last_error = f"save_states: {e}"
            logger.warning("[F60] save_states 失败: %s", e)

    def reset_account(self, balance: float) -> Dict[str, Any]:
        """[F246 2026-09-16] **运行时内安全重置**：车道专属模拟账户金额改为 `balance`。

        为什么走 runner 方法而不是直接改库：runner 常驻内存持有每币 qty/avg_px（DB 只是
        快照），外部改库会在下一个 tick 被内存旧仓覆盖 ✗（F219 的教训）。本方法
        **先清内存再写库**，不需要停后端、不需要重启 ✓。
        语义（与 scripts/mm_reset_lane.py 一致）：
          ① 每币持仓/挂单清零（保留 mid_hist/vol_baseline_bp/spread_baseline —— 清掉会让
             车道重启后"失明"，F109/F213 的坑 ✓）；
          ② 挂单历史清空（F230：旧挂单不得再被延迟判定成交 ✓）；
          ③ 账户行：total_equity=available=balance、realized=0、其余金额列 0；
             分所行（车道 venue）：allocated=available=balance；
          ④ meta：shadow_equity=balance、stats_since=now（新统计时代 ✓）、ops_changes 审计；
          ⑤ 账本 lane_ledger **不动**（不可篡改 ✓）。
        """
        import json
        from datetime import datetime, timezone

        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal
        from backend.services import lane_registry as reg

        balance = float(balance)
        if balance <= 0 or not self.account_id:
            raise ValueError("balance 必须 > 0 且车道绑定模拟账户")

        # ① 内存清零（保留标定数据）
        for st in self.states.values():
            for k in ("qty", "avg_px", "avg_mid", "opened_ts", "last_ts",
                      "quote_bid", "quote_ask", "quote_mid", "quote_ts", "stop_since"):
                setattr(st, k, 0.0)
            st.toxic_streak = 0
        self._quote_hist.clear()
        self.equity = balance

        now_iso = datetime.now(timezone.utc).astimezone().isoformat()
        # [F261 2026-09-16] 只清零**真实存在**的金额列。此前列表含 unrealized_pnl /
        # floating_pnl / used_margin —— arbitrage_paper_accounts 实际没有这些列
        # （information_schema 实测：total_equity/available_balance/frozen_balance/
        # realized_pnl/estimated_points_value）⇒ 重置 API 在写库时
        # UndefinedColumn 500 ✗（且内存已先清零，需重跑一次完整重置）。
        money_zero = ("frozen_balance", "realized_pnl", "estimated_points_value")

        with system_identity():
            with SessionLocal() as db:
                sets = [f"{c}=0" for c in money_zero]
                db.execute(text(
                    "UPDATE arbitrage_paper_accounts SET total_equity=:b,"
                    " available_balance=:b, " + ", ".join(sets)
                    + ", updated_at=now() WHERE id=:i"),
                    {"b": balance, "i": int(self.account_id)})
                _v = str(self.venue or "asterdex").lower()
                db.execute(text(
                    "UPDATE arbitrage_paper_exchange_balances SET allocated_usd=:b,"
                    " available_usd=:b, frozen_usd=0, updated_at=now()"
                    " WHERE account_id=:i AND lower(exchange)=:ex"),
                    {"b": balance, "i": int(self.account_id), "ex": _v})
                # ── [整顿轮·T61 2026-10-06] **其余交易所必须归零** ──────────────
                #
                # 缺陷（用户当场发现："你给我弄个7万账户是怎么回事"）：
                #   本方法原来**只改本车道那一个 venue 的行**，而
                #   `record_paper_leg_fill`（**每笔成交**都会跑）末尾是：
                #       total_allocated = sum(b.allocated_usd for b in all_bals)
                #       account.total_equity = total_allocated   # 跨**全部 7 个**交易所
                #   ⇒ 其余 6 个交易所各留 10000 的默认分配
                #   ⇒ 下一笔成交就把权益重算成 Σ = **70000**
                #   ⇒ **用户设的 150 根本不可能生效**
                #      （实测 equity $70,047.72 = 70000 + realized_pnl 47.72），
                #      而 `compound_ratio` 复利又拿这个假权益去定腿量
                #      ⇒ 腿量被放大到约 20× 权益。
                #
                # 修法：本车道专用账户，**把该账户下其它交易所的分配清零**，
                # 使 Σ allocated 恒等于用户设定的 `balance` ⇒ 重置真正生效。
                # 回滚：删掉这一条 UPDATE（回到"只改本 venue"的旧行为）。
                db.execute(text(
                    "UPDATE arbitrage_paper_exchange_balances SET allocated_usd=0,"
                    " available_usd=0, frozen_usd=0, updated_at=now()"
                    " WHERE account_id=:i AND lower(exchange)<>:ex"),
                    {"i": int(self.account_id), "ex": _v})
                db.commit()
        self.save_states()

        lane = reg.get_lane(self.lane_id) or {}
        meta = dict(lane.get("meta") or {})
        old_since, old_eq = meta.get("stats_since"), meta.get("shadow_equity")
        meta["shadow_equity"] = balance
        meta["stats_since"] = now_iso
        # [h457 2026-09-28] **账户级重置边界**：面板的手续费/已实现按它裁剪，
        # 与试跑脚本会改写的 stats_since 解耦（用户实测："账户没重置，手续费却被清零"✗）。
        # 只有本函数（用户点重置）会写它。
        meta["account_reset_at"] = now_iso
        ops = meta.get("ops_changes")
        if not isinstance(ops, list):
            ops = []
        ops.append({"ts": now_iso, "op": "reset_account_from_frontend",
                    "account_id": int(self.account_id), "balance": balance,
                    "old_stats_since": str(old_since), "old_shadow_equity": old_eq,
                    "reason": "用户在前端重置车道专属模拟账户"})
        meta["ops_changes"] = ops[-20:]
        reg.update_meta(self.lane_id, meta)

        # [F262 2026-09-16] 重置后**自动对账**：运行态清零 ⇒ 账本重建必须同步归零。
        # 否则前端立刻报「账本与运行态分叉」（实测重置后 SOL +0.568 = $55.41 幽灵
        # 警告，需手工跑 mm_reconcile_books.py 才消 ✗）。语义与对账脚本一致：写
        # 零盈亏校正行（fill_px=mid、fee 0、source=reconcile / reason=reset_align，
        # ts=重置时刻 → 落在新时代内 ⇒ 时代视图与 30 天视图同时归零 ✓），
        # **不改任何旧行**（账本不可篡改 ✓）。失败只告警，不阻塞重置。
        try:
            from backend.services import lane_ledger as _ll
            from backend.services.market_maker import reconcile as _rc

            _marks = _rc.latest_marks(list(self.symbols), str(self.venue or "asterdex"))
            _residue = _rc.scope_residues(lane_id=self.lane_id, since=None, until=None)
            _n_adj = 0
            for _sym, _v in _residue.items():
                if abs(float(_v)) <= 1e-9 or (_marks.get(_sym) or 0.0) <= 0:
                    continue
                _side = "sell" if _v > 0 else "buy"
                _ok = _ll.record_fill(
                    lane_id=self.lane_id, symbol=_sym, side=_side, qty=abs(float(_v)),
                    fill_px=_marks[_sym], mid_px=_marks[_sym], fee_rate=0.0,
                    ts=datetime.fromisoformat(now_iso),
                    meta={"source": "reconcile", "reason": "reset_align",
                          "runtime_qty": 0.0, "ledger_qty": float(_v)})
                _n_adj += 1 if _ok else 0
            if _n_adj:
                logger.info("[F262] 重置后自动对账写入 %d 条零盈亏校正行", _n_adj)
        except Exception as _e:  # pragma: no cover - 对账失败不阻塞重置
            logger.warning("[F262] 重置后自动对账失败(忽略): %s", _e)

        logger.info("[F246] 车道 %s 账户已重置为 %.2f（运行时内，无重启）",
                    self.lane_id, balance)
        return {"ok": True, "lane_id": self.lane_id, "account_id": int(self.account_id),
                "balance": balance, "equity": balance, "stats_since": now_iso}

    # ── 行情 ──
    def _refresh_fresh_book(self, db) -> None:
        """[F281 2026-09-21] 一次性把**新鲜盘口**拉进缓存（价差 8~37ms 的源）。

        病根（H68 实测）：`market_orderbook_snapshots` 是 **15 秒**一行，
        而它是引擎 mid 的唯一来源 ⇒ 引擎参考中价**结构性滞后 ~7.5 秒**。
        证据（H67，实盘账本逐笔有符号偏差）：买单 **+2.7517bp** / 卖单 **−3.9835bp**
        —— 教科书式的滞后签名；`corr(帧延迟,|Δmid|)=−0.087` ⇒ 不是单笔延迟，是数据源旧。
        后果：H66 测得 |账本mid−真实mid| 中位 **5.5440bp**，是中位价差 1.2bp 的 **4.6 倍**。

        实现：按 `MM_FRESH_MID_REFRESH_MS`（默认 500ms）节流，**一条 SQL** 取
        `asterdex_book_ticker` 里每个符号的最新一行。全部车道币 + 孤儿币一起取，
        所以每个 tick 的开销是 **1 条查询**（不是每币一条）。

        只提供 `(bid, ask)`，**不碰** `snap_ms` —— 后者锚定 `seg_window` 与成交桶水位
        （F92/F176/F178），动它会破坏延迟一档判定的整套口径。
        """
        import time as _t

        if not getattr(self, "_fresh_mid_enabled", False):
            return
        # ⚠️ `text` 在 `fetch_market` 里是**局部 import** ⇒ 这里必须自己 import，
        # 否则 NameError（F281 首版就是这样静默失效的：except 吞掉 + debug 级日志）✗
        from sqlalchemy import text
        _now = _t.time()
        _every = self._fresh_mid_refresh_s
        if self._fresh_book and (_now - self._fresh_book_ts) < _every:
            return
        try:
            syms = list(dict.fromkeys(list(self.symbols) + list(self.orphan_states())))
            if not syms:
                return
            # ⚠️ **必须逐符号查询**。实测（debug_fresh_book_perf.py，表 1.097 亿行）：
            #     单条 `symbol = ANY(:ss) ORDER BY symbol, ts DESC`  = **134,152 ms** ✗✗
            #     逐符号 `symbol=:s ORDER BY ts DESC LIMIT 1` ×10  = **      8 ms** ✓
            #   差 **17,000 倍**。原因是索引是 `(symbol, event_ts_ms)`：
            #   ANY(数组) 形式无法把它当**有序**扫描用，退化成大排序。
            #   130 秒的查询会把 15 秒的 tick 循环彻底卡死 ⇒ 必须逐符号。
            cache = {}
            for s in syms:
                vs = s if s.endswith("USDT") else f"{s}USDT"
                row = db.execute(text(
                    "SELECT bid_px::float b, ask_px::float a, event_ts_ms::float ts,"
                    " bid_qty::float bq, ask_qty::float aq"
                    "  FROM asterdex_book_ticker"
                    " WHERE symbol = :s AND bid_px > 0 AND ask_px > bid_px"
                    " ORDER BY event_ts_ms DESC LIMIT 1"
                ), {"s": vs}).mappings().first()
                if row:
                    # [h663 审计#5 修复] 缓存带事件时间戳:盘口流停更后旧值必须
                    # 按年龄失效,不得静默用冻结 mid 接单。
                    cache[vs] = (float(row["b"]), float(row["a"]), float(row["ts"]),
                                 float(row["bq"] or 0.0), float(row["aq"] or 0.0))
            if cache:
                self._fresh_book = cache
                self._fresh_book_ts = _now
            else:
                logger.warning("[F281] 新鲜盘口查询返回 0 行（symbols=%d）", len(syms))
        except Exception as e:
            # 数据源不可用 ⇒ 静默退回旧的 15s 快照口径（fail-safe，绝不让 tick 挂掉）
            # 但要**留痕**：此前这里是 logger.debug，导致 F281 静默失效而无人发现 ✗
            self._fresh_mid_err = f"{type(e).__name__}: {e}"
            logger.warning("[F281] 新鲜盘口刷新失败，回退 15s 快照 mid: %s", e)

    def _fresh_book_for(self, symbol: str):
        """[F281] 取某符号的新鲜 `(bid, ask)`；无则返回 None（调用方回退旧口径）。

        [h663 审计#5 修复] 缓存条目 = (bid, ask, event_ts_ms):取用时按年龄失效
        (超过 FRESH_BOOK_MAX_AGE_S 视为停更,回退 15s 快照路径,不再用冻结 mid)。
        """
        if not getattr(self, "_fresh_mid_enabled", False):
            return None
        vs = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
        entry = self._fresh_book.get(vs)
        if not entry:
            return None
        b, a = float(entry[0]), float(entry[1])
        if len(entry) > 2:
            import time as _t
            age_s = _t.time() - float(entry[2]) / 1000.0
            if age_s > float(os.getenv("MM_FRESH_MID_MAX_AGE_SEC", "10") or 10):
                return None   # 停更:回退快照(其自身有 180s 陈旧保护)
        return (b, a) if (b > 0 and a > b) else None

    def _refresh_microprice(self, db) -> None:
        """[h397 2026-09-27] #9 微价偏离输入：从 asterdex_depth_snapshots 最新一档
        计算 microprice 相对中价的偏移（bp），供 F272 `mp_block_bp` 闸消费。

        与 `_refresh_fresh_book` 同节流（500ms）、同逐符号查询模式（索引
        (symbol, event_ts_ms)，ANY 数组会退化全排序）。只在 mp_block_bp>0 时调用
        （0 时跳过，省每 tick 一次深度查询）。
        """
        import time as _t

        from sqlalchemy import text
        _now = _t.time()
        if self._mp_cache and (_now - self._mp_cache_ts) < self._fresh_mid_refresh_s:
            return
        try:
            from backend.services.market_maker.core import (
                microprice_skew_bp, order_book_imbalance)

            cache = {}
            obi_cache = {}
            for s in list(dict.fromkeys(list(self.symbols) + list(self.orphan_states()))):
                vs = s if s.endswith("USDT") else f"{s}USDT"
                row = db.execute(text(
                    "SELECT bids, asks FROM asterdex_depth_snapshots"
                    " WHERE symbol = :s AND bids IS NOT NULL AND asks IS NOT NULL"
                    " ORDER BY event_ts_ms DESC LIMIT 1"
                ), {"s": vs}).mappings().first()
                if row:
                    _bids = list(row["bids"] or [])
                    _asks = list(row["asks"] or [])
                    skew = microprice_skew_bp(_bids, _asks)
                    if abs(skew) > 0:
                        cache[vs] = skew
                    # [h899] 同一份深度顺带算 OBI(趋势概率模型的 obi_top/obi_top5)
                    obi_cache[vs] = (order_book_imbalance(_bids, _asks, 1),
                                     order_book_imbalance(_bids, _asks, 5))
            if cache:
                self._mp_cache = cache
                self._mp_cache_ts = _now
            if obi_cache:
                self._obi_cache = obi_cache
        except Exception as e:  # pragma: no cover - 深度源不可用 ⇒ 0 偏移（闸不干预）
            self._mp_err = f"{type(e).__name__}: {e}"
            logger.warning("[h397] 微价刷新失败（闸不干预）: %s", e)

    def _microprice_skew_for(self, symbol: str) -> float:
        vs = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
        return float(getattr(self, "_mp_cache", {}).get(vs) or 0.0)

    def _obi_for(self, symbol: str) -> tuple:
        """[h899] 读 OBI 缓存(与微价同一次深度刷新)。无 ⇒ (0.0, 0.0) 中性。"""
        vs = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
        return getattr(self, "_obi_cache", {}).get(vs) or (0.0, 0.0)

    def _refresh_depth_spread(self, db) -> None:
        """[h625] 读每个币最新 20 档，算出买卖价差（bp）。

        档数不够、价无效、或超过 book_slot_max_age_sec ⇒ 不放入缓存。
        调用方看到缺失就当「没有新鲜深度」，选档闸不干预。
        """
        import time as _t

        from sqlalchemy import text

        now = _t.time()
        if getattr(self, "_depth_spread_ts", 0.0) and (now - float(self._depth_spread_ts)) < 1.0:
            return
        max_age = float(getattr(self.limits, "book_slot_max_age_sec", 15.0) or 15.0)
        cache: Dict[str, float] = {}
        try:
            for s in list(self.symbols):
                vs = s if str(s).endswith("USDT") else f"{s}USDT"
                row = db.execute(text(
                    "SELECT bids, asks, event_ts_ms FROM asterdex_depth_snapshots"
                    " WHERE symbol = :s AND bids IS NOT NULL AND asks IS NOT NULL"
                    " ORDER BY event_ts_ms DESC LIMIT 1"
                ), {"s": vs}).mappings().first()
                if not row:
                    continue
                bids = list(row["bids"] or [])
                asks = list(row["asks"] or [])
                if len(bids) < 10 or len(asks) < 10:
                    continue
                try:
                    bb = float(bids[0][0])
                    ba = float(asks[0][0])
                    ts_ms = float(row["event_ts_ms"] or 0)
                except (TypeError, ValueError, IndexError):
                    continue
                if bb <= 0 or ba <= bb or ts_ms <= 0:
                    continue
                if now - ts_ms / 1000.0 > max_age:
                    continue
                mid = (bb + ba) / 2.0
                cache[s] = (ba - bb) / mid * 1e4
            self._depth_spread = cache
            self._depth_spread_ts = now
        except Exception as e:  # pragma: no cover
            logger.warning("[h625] 深度价差刷新失败（本闸不干预）: %s", e)

    def _lagged_quote(self, symbol: str, hi_ms: int) -> Optional[Dict[str, float]]:
        """[F176] 取"依据标签 ≤ hi_ms 的最近一张挂单"（延迟判定的判定对象）。

        为什么需要：成交桶现在"桶结束后才落库"（F171）⇒ 桶比 tick 晚 15~30s 可见，
        若仍用**当前**挂单去判，晚到的桶会被 `quote_ts` 过滤器排除 ✗。
        正确做法是回到"那张单当时存续的分片"：标签 ≤ 分片上界的最近一张单 ✓。
        返回 None ⇒ 调用方传 `(0,0,0)` ⇒ 本 tick 不判成交（宁可漏判也不误判 ✓）。
        """
        hist = self._quote_hist.get(symbol) or []
        best = None
        for q in hist:
            try:
                if float(q.get("basis") or 0.0) <= float(hi_ms) and (
                        best is None or float(q["basis"]) > float(best["basis"])):
                    best = q
            except Exception:
                continue
        return best

    def fetch_market(self, since_ms: int) -> Dict[str, Dict[str, Any]]:
        """读每个币的最新盘口 + 自「已消费桶标签」以来的区间成交汇总。

        [F81 2026-09-14] 成交桶水位线：market_trades_aggregated 按 **15 秒桶**存储、
        时间戳=桶起点，每个桶必须**恰好消费一次**（与回放 [left,right] 语义一致）。
        [F92 2026-09-14] **下界不能再取墙钟**：`last_tick_ts` 落在桶中间，会系统性
        排除「标签 ≤ 下界 < 标签+15s」的那个桶（实测实盘/同窗口回放 = 8.9/101 笔每小时，
        只吃到 9% 的成交）。窗口两端改为锚在**快照桶标签**上（见 `seg_window`），
        上界只到本次快照那一桶为止 ⇒ 不漏桶、不读半成品、不把未来成交算进来。
        """
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import MarketSessionLocal

        out: Dict[str, Dict[str, Any]] = {}
        with system_identity():
            with MarketSessionLocal() as db:
                # [F281] 刷新新鲜盘口缓存（一次查询覆盖全部车道币 + 孤儿币）
                self._refresh_fresh_book(db)
                # [h397] #9 微价偏离（mp_block_bp>0 时才刷新深度，0 时零额外查询）
                if float(getattr(self.limits, "mp_block_bp", 0.0) or 0.0) > 0:
                    self._refresh_microprice(db)
                if float(getattr(self.limits, "book_slot_min_bp", 0.0) or 0.0) > 0:
                    self._refresh_depth_spread(db)
                _seg_src_head = (os.getenv("MM_SEG_SOURCE", "tick") or "tick").strip().lower()
                if _seg_src_head not in ("tick", "bucket"):
                    _seg_src_head = "tick"
                for s in self.symbols:
                    # [h653 2026-09-30 用户指令] 盘口/成交数据要实时,不用滞后快照:
                    # tick 源下 mid/价差直接吃实时盘口流(asterdex_book_ticker,8~37ms),
                    # 15s 快照表(market_orderbook_snapshots)只作回退。
                    # 病根(H69/17:3x 现场):快照滞后 15s ≈ −1.2bp/腿;且快照管道
                    # 不服务新币时(SUI/ENA)整个币被 continue,实时盘口明明有数据。
                    _fb_pre = self._fresh_book_for(s)
                    _ob = None
                    if (_seg_src_head == "tick" and _fb_pre is not None
                            and _fb_pre[0] > 0 and _fb_pre[1] > _fb_pre[0]):
                        _best_bid, _best_ask = _fb_pre
                        snap_ms = int(time.time() * 1000)   # 实时锚:墙钟
                        self._fresh_mid_hits += 1
                    else:
                        _ob = db.execute(text(
                            "SELECT timestamp, best_bid, best_ask FROM market_orderbook_snapshots"
                            " WHERE exchange=:e AND symbol=:s AND best_bid>0 AND best_ask>best_bid"
                            " ORDER BY timestamp DESC LIMIT 1"
                        ), {"e": self.venue, "s": s}).mappings().first()
                        if not _ob:
                            continue
                        snap_ms = int(_ob["timestamp"])
                        _best_bid, _best_ask = float(_ob["best_bid"]), float(_ob["best_ask"])
                    # [F92] 窗口锚在快照桶标签上（下界=已消费标签，上界=当前快照标签）
                    # [F176 2026-09-15] **延迟一档判定**：F171 之后成交桶"桶结束后才落库"
                    # ⇒ 桶比 tick 晚 15~30s 可见 ✗；若仍按"当前快照"取上界，晚到的桶会被
                    # `quote_ts` 过滤器排除（实测空分片率 47%→85.7%、成交 184→27.5/h ✗✗）。
                    # 所以上界改为 `snap − L×15s`，并判定"那时真正在挂的那张单" ✓
                    # （挂单历史见 `_quote_hist`，由 tick 循环维护 ✓）。L=0 则完全旧行为 ✓。
                    st_seg = self.states.setdefault(s, SymbolState(symbol=s))
                    # [h621 2026-09-29] **成交判定源开关**：tick（默认 = 逐笔
                    # asterdex_trades，实时、无桶滞后——用户指正"有单独的实时盘口，
                    # 不能用滞后的 15s"，且明确要求修复直接生效、不留默认关闭）。
                    # bucket = 回退通道：出问题时设 MM_SEG_SOURCE=bucket 一键回退。
                    _seg_src = (os.getenv("MM_SEG_SOURCE", "tick") or "tick").strip().lower()
                    if _seg_src not in ("tick", "bucket"):
                        _seg_src = "tick"
                    # tick 源下"延迟一档判定"（judge_lag_buckets）失去意义——那是给
                    # **桶落库滞后 15~30s** 设计的补偿；逐笔 p50 1.5s。强制 L=0 ⇒
                    # 判定对象=当前挂单，与下方 vol_at_price 的报价口径严格一致。
                    _L = (0 if _seg_src == "tick"
                          else int(getattr(self, "judge_lag_buckets", 0) or 0))
                    _hi_use, _qts_use, _jq = snap_ms, float(
                        getattr(st_seg, "quote_ts", 0.0) or 0.0), None
                    if _L > 0:
                        _hi_use = snap_ms - _L * SEG_BUCKET_MS
                        _jq = self._lagged_quote(s, _hi_use)
                        if _jq is not None:
                            _qts_use = float(_jq.get("ts") or 0.0)
                        # [F178] 安全网：过滤器的时间点**不得超过窗口上界**。
                        # 延迟判定下，被判定挂单必然是在 `≤ hi` 之前挂出的 ⇒ 它的挂单时刻
                        # 不可能晚于 hi ✗。若历史缺失/异常导致取到"当前挂单的 ts"，
                        # 过滤器会把窗口里的桶**全部排除**（实测 n_eff 恒为 0 ✗✗）。
                        _qts_use = min(float(_qts_use or 0.0), float(_hi_use) / 1000.0)
                    _lo, _hi, _qts = seg_window(
                        int(getattr(st_seg, "last_seg_ms", 0) or 0), _hi_use, _qts_use)
                    _vol_le_bid = _vol_ge_ask = 0.0     # [h621] tick 源才有值
                    _vis_quote = None
                    if _seg_src == "tick":
                        # [h621 2026-09-29] tick 级成交源（asterdex_trades 逐笔）：
                        #   · 无桶滞后（桶路径 15~30s ⇒ 需要 judge_lag 补偿；逐笔 p50 1.5s）；
                        #   · 无网格空洞（空桶不落行 ⇒ 历史填充率 47.5%）；
                        #   · 判定口径 = 价位真实量（plan_tick 的 tick_fill 分支），
                        #     同时消灭 9.9% 幻影成交与 18% 的数量虚记（本轮取证实测）。
                        # ⚠️ 与桶路径**不共用水位语义**（桶标签=15s 网格 vs 逐笔=连续毫秒）
                        #    ⇒ 切换源需重启 worker；首个 tick 用旧桶标签水位只是安全下界
                        #    （多消费 ≤1 桶，且 qts 过滤保证只影响当前报价存续期）。
                        from backend.services.market_maker.core import (
                            aggregate_trades, quote_visible_at, tick_trade_hi_ms,
                        )
                        # 上界 = 墙钟 − 3s（逐笔落库余量）。不能拉回 15 秒快照：
                        # 快照比墙钟慢约 20 秒，一拉回去窗口就是空的。
                        # 被判定的单 = 这 3 秒之前已经挂出的那张，不是此刻刚算的新价。
                        _LAG_T = 3000
                        _LOOK_T = 120_000
                        _hi_t = tick_trade_hi_ms(int(time.time() * 1000), snap_ms, _LAG_T)
                        _vis_quote = quote_visible_at(self._quote_hist.get(s) or [], _hi_t)
                        if _vis_quote is not None:
                            _qb_j = float(_vis_quote.get("bid") or 0.0)
                            _qa_j = float(_vis_quote.get("ask") or 0.0)
                            _qts_ms = int(float(_vis_quote.get("ts") or 0.0) * 1000)
                        else:
                            _qb_j = _qa_j = 0.0
                            _qts_ms = 0
                        # 挂单历史里没有够老的记录时，用状态里那张已经挂过 3 秒的单。
                        # 否则成交窗口是空的，买单会一直挂着、永远不开仓。
                        if _qb_j <= 0 and _qa_j <= 0:
                            _st_qts = int(float(getattr(st_seg, "quote_ts", 0.0) or 0.0) * 1000)
                            if (_st_qts > 0 and _hi_t >= _st_qts + _LAG_T
                                    and (float(st_seg.quote_bid or 0.0) > 0
                                         or float(st_seg.quote_ask or 0.0) > 0)):
                                _qb_j = float(st_seg.quote_bid or 0.0)
                                _qa_j = float(st_seg.quote_ask or 0.0)
                                _qts_ms = _st_qts
                        _lo_t = max(int(getattr(st_seg, "last_seg_ms", 0) or 0),
                                    _hi_t - _LOOK_T,
                                    _qts_ms)
                        _sym_u = s if str(s).endswith("USDT") else f"{s}USDT"
                        _rows_t = (db.execute(text(
                            "SELECT event_ts_ms, price, qty, is_buyer_maker"
                            " FROM asterdex_trades"
                            " WHERE symbol=:sym"
                            "   AND event_ts_ms > :lo AND event_ts_ms <= :hi"
                            " ORDER BY event_ts_ms"
                        ), {"sym": _sym_u, "lo": _lo_t, "hi": _hi_t}).all()
                            if _hi_t > _lo_t and (_qb_j > 0 or _qa_j > 0) else [])
                        _agg_t = aggregate_trades(
                            [r[0] for r in _rows_t], [float(r[1]) for r in _rows_t],
                            [float(r[2]) for r in _rows_t], [bool(r[3]) for r in _rows_t],
                            quote_bid=_qb_j, quote_ask=_qa_j)
                        if _rows_t:
                            st_seg.last_seg_ms = max(_lo_t, int(_rows_t[-1][0]))
                            self._seg_watermark[s] = st_seg.last_seg_ms
                        tr = {"lo": _agg_t["seg_low"], "hi": _agg_t["seg_high"],
                              "sv": _agg_t["taker_sell"], "bv": _agg_t["taker_buy"],
                              "mts": (int(_rows_t[-1][0]) if _rows_t else None),
                              "n_eff": _agg_t["n"]}
                        # [h621 临时诊断] 每 4 tick 打一次判定输入（定位零成交用，量小无碍）
                        if int(self.ticks) % 4 == 0 and os.getenv("MM_TICK_DIAG", "").strip() in ("1", "true", "True"):
                            try:
                                import pathlib as _pl
                                _dp = _pl.Path(__file__).resolve().parents[3] / "logs" / "h621_diag.log"
                                with open(_dp, "a", encoding="utf-8") as _f:
                                    _f.write(
                                        f"{time.strftime('%H:%M:%S')} {s} tick={self.ticks} "
                                        f"win[{_lo_t},{_hi_t}] n={_agg_t['n']} "
                                        f"lo={_agg_t['seg_low']} hi={_agg_t['seg_high']} "
                                        f"sv={_agg_t['taker_sell']:.4f} bv={_agg_t['taker_buy']:.4f} "
                                        f"qb={_qb_j} qa={_qa_j} "
                                        f"vlb={_agg_t['vol_le_bid']:.6f} vga={_agg_t['vol_ge_ask']:.6f}\n")
                            except Exception:   # noqa: BLE001
                                pass
                        _lo, _hi = _lo_t, _hi_t
                        _vol_le_bid = float(_agg_t["vol_le_bid"])
                        _vol_ge_ask = float(_agg_t["vol_ge_ask"])
                    else:
                        tr = db.execute(text(
                            "SELECT MIN(low_price) FILTER (WHERE timestamp + :bk > :qts) AS lo,"
                            " MAX(high_price) FILTER (WHERE timestamp + :bk > :qts) AS hi,"
                            " COALESCE(SUM(taker_sell_volume) FILTER"
                            "   (WHERE timestamp + :bk > :qts),0) AS sv,"
                            " COALESCE(SUM(taker_buy_volume) FILTER"
                            "   (WHERE timestamp + :bk > :qts),0) AS bv,"
                            " MAX(timestamp) AS mts,"
                            " COUNT(*) FILTER (WHERE timestamp + :bk > :qts) AS n_eff"
                            " FROM market_trades_aggregated"
                            " WHERE exchange=:e AND symbol=:s"
                            " AND timestamp > :lo AND timestamp <= :hi"
                        ), {"e": self.venue, "s": s, "lo": _lo, "hi": _hi,
                            "bk": SEG_BUCKET_MS, "qts": _qts}).mappings().first()
                    # 水位只前进，且只在**真读到桶**时前进（读不到就停住 → 下个 tick 补收）
                    if tr and tr["mts"] is not None:
                        st_seg.last_seg_ms = max(_lo, int(tr["mts"]))
                        self._seg_watermark[s] = st_seg.last_seg_ms
                    best_bid, best_ask = _best_bid, _best_ask
                    mid = (best_bid + best_ask) / 2.0
                    _n_eff = int(tr["n_eff"] or 0) if tr else 0
                    _sv = float(tr["sv"]) if (tr and _n_eff) else 0.0
                    _bv = float(tr["bv"]) if (tr and _n_eff) else 0.0
                    # [F86] 本桶主动流失衡 OFI∈[-1,1]（+1=全主动买）→ 流向毒性闸
                    _ofi = ((_bv - _sv) / (_bv + _sv)) if (_bv + _sv) > 0 else 0.0
                    # [h354] P2 形态输入：60s 成交 VWAP（vwap_revert_bp>0 时才查）
                    _vwap60 = 0.0
                    if float(getattr(self.limits, "vwap_revert_bp", 0.0) or 0.0) > 0:
                        _vw = db.execute(text(
                            "SELECT COALESCE(sum(price*qty)/NULLIF(sum(qty),0), 0) AS vw"
                            " FROM asterdex_trades"
                            " WHERE symbol = CONCAT(CAST(:s AS TEXT), 'USDT')"
                            "   AND event_ts_ms > CAST(:t0 AS BIGINT)"
                            "   AND event_ts_ms <= CAST(:t1 AS BIGINT)"
                        ), {"s": s, "t0": snap_ms - 60000, "t1": snap_ms}).mappings().first()
                        _vwap60 = float(_vw["vw"]) if _vw else 0.0
                    _touch = getattr(self, "_fresh_book", {}).get(
                        s if str(s).endswith("USDT") else f"{s}USDT") or ()
                    _bid_qty = float(_touch[3]) if len(_touch) >= 5 else 0.0
                    _ask_qty = float(_touch[4]) if len(_touch) >= 5 else 0.0
                    out[s] = {
                        "ts_ms": snap_ms,
                        "mid": mid,
                        "half_spread": max(0.0, (best_ask - best_bid) / 2.0),
                        # 相对价差（波动归一的输入；与 F59 回放同口径）
                        "rel_spread": ((best_ask - best_bid) / mid) if mid > 0 else 0.0,
                        "seg_low": float(tr["lo"]) if (tr and _n_eff and tr["lo"]) else 0.0,
                        "seg_high": float(tr["hi"]) if (tr and _n_eff and tr["hi"]) else 0.0,
                        "seg_sell": _sv,
                        "seg_buy": _bv,
                        "ofi": self._ofi_60s(s, int(snap_ms)),
                        "vwap60": _vwap60,
                        # [h397] #9 微价偏离（F272 mp_block_bp 闸输入；未刷新/源缺失=0）
                        "mp_skew": self._microprice_skew_for(s),
                        # [h899] 顶档盘口失衡(趋势概率模型特征;与微价同一次深度刷新)
                        "obi_top": float(self._obi_for(s)[0]),
                        # [h625] 新鲜 20 档价差；没有则 None，选档闸不干预
                        "depth_spread_bp": (getattr(self, "_depth_spread", {}) or {}).get(s),
                        # [F92] 观测：窗口/生效桶数（前端「链路」可见性 + 巡检用）
                        "seg_lo_ms": _lo, "seg_hi_ms": _hi, "seg_buckets": _n_eff,
                        # [h621] tick 源：价位真实量与口径标记（plan_tick tick_fill 分支）
                        "vol_le_bid": _vol_le_bid,
                        "vol_ge_ask": _vol_ge_ask,
                        "bid_qty": _bid_qty,
                        "ask_qty": _ask_qty,
                        "tick_fill": _seg_src == "tick",
                        # 逐笔：判定当时已经挂着的那张单。桶路径仍用延迟一档的 _jq。
                        "judged_quote": (
                            (float(_vis_quote.get("bid") or 0.0),
                             float(_vis_quote.get("ask") or 0.0),
                             float(_vis_quote.get("mid") or 0.0))
                            if _vis_quote is not None else (
                                (float(_jq.get("bid") or 0.0),
                                 float(_jq.get("ask") or 0.0),
                                 float(_jq.get("mid") or 0.0))
                                if _jq is not None else (0.0, 0.0, 0.0))),
                        # [h754 A1] 严格成交审计输入:被判定挂单的挂出时刻 + 本次判定的
                        # 逐笔窗口(落进账本 meta,离线审计用精确值而非反推)。
                        "judge_ts": (float(_vis_quote.get("ts") or 0.0)
                                     if _vis_quote is not None
                                     else float((_jq or {}).get("ts") or 0.0)),
                        "win_lo_ms": int(_lo) if _seg_src == "tick" else 0,
                        "win_hi_ms": int(_hi) if _seg_src == "tick" else 0,
                    }
                # [F90 2026-09-14] 孤儿持仓估值行情：只取盘口（强制退出/估值用），
                # **不消费成交桶**（该币不报价，水位线保持不动）。
                for s in self.orphan_states():
                    # [h376] asterdex 用原生 book_ticker（market_orderbook_snapshots 缺币）
                    if str(self.venue or "").lower() == "asterdex":
                        ob = db.execute(text(
                            "SELECT event_ts_ms AS ts,"
                            " (array_agg(bid_px ORDER BY event_ts_ms DESC))[1] AS bb,"
                            " (array_agg(ask_px ORDER BY event_ts_ms DESC))[1] AS ba"
                            " FROM asterdex_book_ticker"
                            " WHERE symbol=CONCAT(CAST(:s AS TEXT),'USDT')"
                            " AND event_ts_ms > (extract(epoch from now())*1000"
                            "   - 3*3600*1000)::bigint"
                            " AND bid_px>0 AND ask_px>bid_px"
                            " GROUP BY ts ORDER BY ts DESC LIMIT 1"
                        ), {"s": s}).mappings().first()
                        if ob and float(ob["bb"]) > 0 and float(ob["ba"]) > float(ob["bb"]):
                            ob = {"best_bid": float(ob["bb"]), "best_ask": float(ob["ba"]),
                                  "timestamp": int(ob["ts"])}
                        else:
                            ob = None
                    else:
                        ob = db.execute(text(
                            "SELECT timestamp, best_bid, best_ask FROM market_orderbook_snapshots"
                            " WHERE exchange=:e AND symbol=:s AND best_bid>0 AND best_ask>best_bid"
                            " ORDER BY timestamp DESC LIMIT 1"
                        ), {"e": self.venue, "s": s}).mappings().first()
                    if not ob:
                        continue
                    best_bid, best_ask = float(ob["best_bid"]), float(ob["best_ask"])
                    # [F281] 孤儿持仓也吃新鲜盘口 —— 它们的 mid 用于估值与强制退出，
                    # 一个滞后 15 秒的 mid 会让"该不该强平"这个判断本身出错。
                    _fb2 = self._fresh_book_for(s)
                    if _fb2 is not None and _fb2[0] > 0 and _fb2[1] > _fb2[0]:
                        best_bid, best_ask = _fb2
                    mid = (best_bid + best_ask) / 2.0
                    out[s] = {
                        "ts_ms": int(ob["timestamp"]),
                        "mid": mid,
                        "half_spread": max(0.0, (best_ask - best_bid) / 2.0),
                        "rel_spread": ((best_ask - best_bid) / mid) if mid > 0 else 0.0,
                        "seg_low": 0.0, "seg_high": 0.0, "seg_sell": 0.0, "seg_buy": 0.0,
                        "ofi": 0.0, "orphan": True,
                    }
        return out

    def _record_fills(self, decision: TickDecision) -> None:
        """把本 tick 判定成交的腿写入：① 六维账本（车道级）；② 统一模拟账户总账。
        两本账的分工：
          - `lane_ledger` 是**车道级**事实源（六维归因、晋升判定）；
          - 统一账户总账（`arbitrage_paper_ledger`，strategy_type=MM）让 MM 与
            S3/S8/SDN 同账管理——账户权益、可用余额、按策略盈亏一处可见。
        """
        if not decision.fills:
            return
        try:
            from datetime import datetime, timezone

            from backend.services import lane_ledger
            from backend.services.market_maker.venue_filters import (
                passes as _venue_passes,
                round_px as _venue_px,
                round_qty as _venue_qty,
            )

            for f in decision.fills:
                # [h667] 模拟按 Asterdex 真实条件:数量 stepSize/价格 tickSize 对齐
                # + MIN_NOTIONAL + PERCENT_PRICE 带。不过滤的腿不得入账
                # (真实交易所会拒单;纸面必须同条件,否则不可比)。
                _q2 = _venue_qty(f.symbol, float(f.qty))
                _p2 = _venue_px(f.symbol, float(f.px))
                _okf, _why = _venue_passes(f.symbol, _p2, _q2,
                                            float(f.mid or 0.0),
                                            reduce_only=bool(f.is_flatten))
                if _q2 <= 0 or not _okf:
                    self._venue_filter_skips += 1
                    continue
                f.qty, f.px = _q2, _p2
                # [h755 A2 2026-10-03] 队列位置标签(不改判定,只观测):
                # 我方报价 vs 真实盘口最优价 ⇒ inside(在价差内=我们就是最优价,
                # 无排队对手)/touch(同价,有排队)/behind(比最优价差,本不该成交)。
                # 用途:校准 QUEUE_SHARE 常数(实测 57% inside / 32% touch / 10% behind,
                # 30% 常数在 inside 情形严重低估)。
                _qpos, _qimp = "unknown", 0.0
                # ── [整顿轮·T52 2026-10-06] **记录"新鲜盘中价"以便交叉校验** ──
                # 背景（R066 实测）：记账用的 `mid_px` 取自 `market_row.mid`
                # （见本文件 692 行），与**真实盘口中价**相比存在**系统性偏置**：
                #     buy 腿 引擎偏高 +10.17bp (t=+2.97)
                #     sell 腿 引擎偏低  −5.52bp (t=−2.94)
                #     统一方向 +7.75bp        (t=+4.03)
                # ⇒ 由于 `spread_bp` 完全建立在这个 mid 上，**spread/price 拆分失真**。
                #
                # 注意本块 5318 行的既有写法：
                #     `_midq = float(f.mid or 0.0) or ((_bb + _ba) / 2.0)`
                # —— 它**优先用旧的 f.mid**，只有在 f.mid 为空时才用新鲜盘口。
                # 所以 `qpos_bp` 也建立在同一个偏置 mid 上。
                #
                # 修法（纯遥测，零延迟：`_fb` 本来就已经取了）：
                # 把**新鲜盘口的中价**也写进 meta ⇒ 事后可**直接从账本**算出
                # 真实捕获 `(book_mid − fill_px)`，不必再依赖外部 tick 库，
                # 也没有 ±1s 的时间对齐误差。
                # 回滚：删掉 meta 里的 `book_mid` 键即可（不改任何判定）。
                _book_mid = 0.0
                try:
                    _fb = self._fresh_book_for(str(f.symbol))
                    if _fb and float(_fb[0]) > 0 and float(_fb[1]) > float(_fb[0]):
                        _bb, _ba = float(_fb[0]), float(_fb[1])
                        _book_mid = (_bb + _ba) / 2.0
                        _midq = float(f.mid or 0.0) or ((_bb + _ba) / 2.0)
                        if str(f.side) == "buy":
                            _qimp = (float(f.px) - _bb) / _midq * 1e4
                        else:
                            _qimp = (_ba - float(f.px)) / _midq * 1e4
                        _qpos = ("inside" if _qimp > 0.25 else
                                 "touch" if _qimp >= -0.25 else "behind")
                except Exception:
                    pass
                notional = abs(float(f.qty) * float(f.px))
                paid = abs(float(getattr(f, "fee_usd", 0.0) or 0.0))
                fee_rate = (paid / notional) if notional > 0 else 0.0
                price_bp = (f.price_usd / notional * 1e4) if notional > 0 else 0.0
                # [h784 2026-10-04] **崩盘熔断**:平仓腿的价格项深于 −150bp
                # ⇒ 该币立即进入"只减不加"30 分钟(实测 SI 深夜连续 5 条
                # −191~−280bp 的腿,合计 −$2.9U;亏损淘汰的 4h/30 腿门槛太慢)。
                if bool(f.is_flatten) and price_bp < -150.0:
                    _csym = str(f.symbol or "").upper()
                    try:
                        self._crash_block[_csym] = time.time() + 1800.0
                        logger.warning("[h784] 崩盘熔断:%s 平仓腿 %.0fbp ⇒ 只减不加 30min",
                                       _csym, price_bp)
                        self._push_event("crash_block",
                                         f"崩盘熔断:{_csym} {price_bp:.0f}bp ⇒ 30 分钟只减不加")
                    except Exception:
                        pass
                lane_ledger.record_fill(
                    lane_id=self.lane_id, symbol=f.symbol, side=f.side, qty=f.qty,
                    fill_px=f.px, mid_px=f.mid, fee_rate=fee_rate,
                    price_bp=price_bp,
                    # [F279 2026-09-16] 库存周期 id：让开仓腿与平仓腿可配对。
                    # 此前恒为 NULL（实测 5255/5255），往返级归因做不了。
                    position_id=(f.position_id or None),
                    ts=datetime.fromtimestamp(float(f.ts), tz=timezone.utc),
                    meta={"source": "F60_shadow", "flatten": f.is_flatten,
                          "notional": round(notional, 4),
                          "price_usd": round(f.price_usd, 6),
                          # [h755 A2] 队列位置(inside/touch/behind + 相对最优价 bp)
                          "qpos": _qpos, "qpos_bp": round(_qimp, 3),
                          # [F335 2026-09-22] **出口原因**必须落盘。
                          #
                          # 事故：`lane_ledger.meta_json` 此前对 flatten 只记
                          # `{"flatten": true}`，**不记走的是哪条出口**。
                          # 后果：114 笔 flatten 里"止损 / 止盈 / 超时 / 孤儿 / 反向"
                          # 各占多少**无法直接回答**，只能从 `price_bp` 的分布反推
                          # （实测双峰：−26bp 与 +21~+50bp 两簇）。
                          # 反推能猜对方向，但**分不清"止损触发"与"超时强平"**
                          # —— 而这两者的修法完全不同（改阈值 vs 改超时）。
                          #
                          # `dec.skip` 在各出口分支里已被赋值（`stop_loss` /
                          # `take_profit` / `timeout_maker_only` / `orphan_flatten`
                          # / `ofi_flatten`），这里直接取用即可。
                          # ⚠️ 放在 `meta` 里而不是新列：账本表结构不动，
                          # 且 `record_fill` 会把 meta 原样合并进 `meta_json`。
                          "exit_reason": str(getattr(decision, "skip", "") or ""),
                          "exit_action": str(getattr(decision, "action", "") or ""),
                          # [F340 2026-09-22] **真实出口**（F335 的 `exit_reason`
                          # 取自 `dec.skip`，而实测 14 天 1,089 条 flatten 里
                          # **1,071 条两个键都不存在** ⇒ 归因基本失效）。
                          #
                          # 两条独立的失效机制（都已实测确认）：
                          #   ① 快照差集 `keys_diff` 用的是 F335 部署**之后**的行，
                          #      所以"1,070 条无键"里混着 F335 之前的旧行；
                          #   ② 真正的坑：`dec.skip` 可能残留同一 tick 早先**闸门**
                          #      的名字（`vol_pause` / `trend_up` / `ofi_toxic_*`），
                          #      于是 flatten 腿被标成"被闸门拦下"而不是"被强平"
                          #      ⇒ 我据此得出"trend_up/ofi 是主要出口"的结论**不成立**。
                          # `exit_path` 由每条出口分支**显式命名自己**，是本车道
                          # 唯一可信的出口读数（`stop_loss_taker` /
                          # `take_profit_taker` / `timeout_taker` /
                          # `ofi_flatten_taker` / `orphan_taker(*)`）。
                          "exit_path": str(getattr(decision, "exit_path", "") or ""),
                          "regime": str(getattr(decision, "regime", "") or ""),
                          # [T52] 新鲜盘口中价 —— 与 `mid_px`（来自 market_row）
                          # 并列记录，使 spread 的真实性可被交叉校验（R066）。
                          "book_mid": round(_book_mid, 10),
                          # 盘口中间价算出的捕获。spread_bp 列仍用决策中价，
                          # 因为价格盈亏也用那同一个中价，两列相加才等于真实盈亏。
                          # 分析「进场赚了多少价差」必须用这一列，不能用 spread_bp。
                          "book_capture_bp": (
                              round(((_book_mid - float(f.px)) if str(f.side) == "buy"
                                     else (float(f.px) - _book_mid)) / _book_mid * 1e4, 4)
                              if _book_mid > 0 and float(f.px) > 0 else None),
                          # 平仓才有：开仓价到平仓价，不靠仓位编号事后配对。
                          "rt_bp": (None if getattr(f, "rt_bp", None) is None
                                    else round(float(f.rt_bp), 4)),
                          # [2026-10-09] 进场腿：挂单时刻相对买一/卖一的偏移（bp）
                          # —— 插进价差的**真**口径（>0/买、<0/卖 = 违规）。
                          "pp_arm_rel_bp": (None if getattr(f, "pp_arm_rel_bp", None) is None
                                            else round(float(f.pp_arm_rel_bp), 4)),
                          "rt_entry_px": round(float(getattr(f, "rt_entry_px", 0.0) or 0.0), 10),
                          "hold_sec_true": round(float(getattr(f, "hold_sec_true", 0.0) or 0.0), 3),
                          # [F257] 判定依据一并入库：桶极值 + 挂单价。
                          # 没有它，事后无法判断这笔成交的价在真实市场里是否存在。
                          "seg_low": round(float(getattr(f, "seg_low", 0.0) or 0.0), 10),
                          "seg_high": round(float(getattr(f, "seg_high", 0.0) or 0.0), 10),
                          "px_exact_hit": getattr(f, "px_exact_hit", None),
                          # [h402] #13 挂单时刻入账（h369：成交时刻归因被机械污染）
                          "quote_ts": round(float(getattr(f, "quote_ts", 0.0) or 0.0), 3),
                          # [h754 A1] 严格成交审计输入:判定用的挂单时刻与逐笔窗口
                          "judge_ts": round(float(getattr(f, "judge_ts", 0.0) or 0.0), 3),
                          "win_lo_ms": int(getattr(f, "win_lo_ms", 0) or 0),
                          "win_hi_ms": int(getattr(f, "win_hi_ms", 0) or 0)},
                )
                self._persist_fill_basis(f, fee_rate=fee_rate)
                self._record_account_fill(f, fee_rate=fee_rate)
        except Exception as e:
            self.last_error = f"record_fills: {e}"
            logger.warning("[F60] record_fill 失败: %s", e)

    def _live_tick_fills(self, dec: "TickDecision", st, m: Dict[str, Any],
                         now_ts: float) -> None:
        """[h665] 实盘车道每 tick 的成交处理:
        ① 模拟成交腿作废(纸面判定在实盘无意义);
        ② 从 dec.fills 推导期望报价(腿即报价意图:bid=买单腿,ask=卖单腿)
           → 执行桥撤改真实限价单(post-only,减仓侧 reduce-only);
        ③ 真实成交回报(userTrades)入账(同一 _record_fills 路径,meta 源 live),
           并增量更新本地库存。
        """
        live = self._live_bridge()
        if live is None or not live.enabled:
            # [h665 修复] 桥不可用(无 Key/非 live):模拟腿**必须清空**——
            # 否则 tick 循环的 self.fills += len(dec.fills) 会把"已作废的模拟
            # 成交"计进心跳(观测谎言),也不得下真实单。
            dec.fills = []
            return
        desired: Dict[str, Optional[Dict[str, Any]]] = {"bid": None, "ask": None}
        for f in dec.fills:
            side_key = "bid" if f.side == "buy" else "ask"
            cur = desired[side_key] or {"px": float(f.px), "qty": 0.0,
                                        "reduce": bool(f.is_flatten)}
            cur["qty"] = float(cur.get("qty") or 0.0) + abs(float(f.qty))
            cur["reduce"] = bool(cur.get("reduce") or f.is_flatten)
            desired[side_key] = cur
        if desired["bid"] and float(getattr(dec, "bid", 0.0) or 0.0) > 0:
            desired["bid"]["px"] = float(dec.bid)
        if desired["ask"] and float(getattr(dec, "ask", 0.0) or 0.0) > 0:
            desired["ask"]["px"] = float(dec.ask)
        # ── [2026-10-09 重复来回做市 ping-pong] resting 挂单同步 ──────────
        # 本拍没有成交腿的一侧，只要决策带出了挂单数量（dec.bid_qty/ask_qty）
        # 就照挂（ping-pong 双侧 resting 单靠这个活）；旧路径不填这些字段
        # （保持 0/False）⇒ 行为与之前逐字一致。
        for _sk, _px, _q, _red in (
                ("bid", getattr(dec, "bid", 0.0), getattr(dec, "bid_qty", 0.0),
                 getattr(dec, "bid_reduce", False)),
                ("ask", getattr(dec, "ask", 0.0), getattr(dec, "ask_qty", 0.0),
                 getattr(dec, "ask_reduce", False))):
            if desired[_sk] is None and float(_px or 0.0) > 0 \
                    and float(_q or 0.0) > 0:
                desired[_sk] = {"px": float(_px), "qty": float(_q),
                                "reduce": bool(_red)}
        # 模拟腿作废(报价意图已提取到 desired)
        dec.fills = []
        live.sync_quotes(st.symbol, desired)
        # 真实回报入账
        fills = live.poll_fills(st.symbol)
        mid = float(m.get("mid") or 0.0)
        real = []
        for f in fills:
            qty = float(f.get("qty") or 0.0)
            px = float(f.get("px") or 0.0)
            if qty <= 0 or px <= 0:
                continue
            side = str(f.get("side") or "")
            is_reduce = ((side == "sell" and st.qty > 1e-12)
                         or (side == "buy" and st.qty < -1e-12))
            realized = 0.0
            if is_reduce and abs(st.qty) > 1e-12 and float(st.avg_px or 0.0) > 0:
                sgn = -1.0 if side == "sell" else 1.0
                realized = (px - float(st.avg_px)) * qty * sgn
            pf = PlannedFill(symbol=st.symbol, side=side, qty=qty, px=px,
                             mid=mid or px, ts=now_ts, is_flatten=is_reduce,
                             spread_usd=0.0, price_usd=realized,
                             fee_usd=float(f.get("fee") or 0.0),
                             position_id=str(f.get("order_id") or ""),
                             quote_ts=now_ts)
            real.append(pf)
            st.qty += (1.0 if side == "buy" else -1.0) * qty
        if real:
            _dec2 = TickDecision(symbol=st.symbol, action=dec.action,
                                 skip=dec.skip, fills=real,
                                 skip_side=dec.skip_side)
            self._record_fills(_dec2)
            # 心跳计数:实盘只计真实成交腿
            self.fills += len(real)
            self.flattens += sum(1 for f in real if f.is_flatten)

    def _persist_fill_basis(self, f: "PlannedFill", *, fee_rate: float) -> None:
        """[F257 2026-09-20] 把**成交判定依据**追加落盘（JSONL）。

        ## 为什么必须落盘

        H28 现场发现：在流动性充足的币上，**47.7% 的引擎成交，其记录的成交价
        在 ±20s / ±2bp 窗口内的真实逐笔成交里找不到对应**（偏差中位 5.26bp、
        p75 9.6bp、max 28.6bp）；余下 52.3% 里我们没有比真实成交更好（+0.10bp）。

        机制：`core.fill_side` 用**15 秒桶内的最低价**判成交
        （`seg_low < quote_bid` 且桶内有主动卖量），**成交价却记作我们的挂单价**。
        ⇒「桶内极值顺带穿过挂单价」与「真有成交发生在我们的价位上」
        被当成同一件事，但它们的经济含义完全不同。

        ## 为什么不能只靠内存

        `fill_notes` 是**内存环**（最近 60 条，进程重启即丢），
        而验证需要**历史**：把当时的报价与桶极值留下来，事后才能
        用真实逐笔重放判定"这笔成交在当时存在吗"。
        本项目此前**不存任何历史报价流** ⇒ 这个问题在账本里永远看不出来。

        ## 落盘内容

        每笔一行 JSON（`logs/mm_fill_basis.jsonl`）：
            ts / symbol / side / qty / fill_px / engine_mid / seg_low / seg_high
            / fee_rate / flatten / position_id / px_exact_hit

        **不抛异常**：落盘失败绝不能影响交易链路（与 `lane_ledger.record_fill` 同一原则）。
        """
        try:
            import json as _json
            import time as _time

            path = Path(__file__).resolve().parents[3] / "logs" / "mm_fill_basis.jsonl"
            rec = {
                "ts": float(f.ts), "iso": _time.strftime(
                    "%Y-%m-%dT%H:%M:%S", _time.localtime(float(f.ts))),
                "symbol": f.symbol, "side": f.side,
                "qty": round(float(f.qty), 10), "fill_px": float(f.px),
                "engine_mid": float(f.mid),
                "seg_low": float(getattr(f, "seg_low", 0.0) or 0.0),
                "seg_high": float(getattr(f, "seg_high", 0.0) or 0.0),
                "fee_rate": float(fee_rate), "flatten": bool(f.is_flatten),
                "position_id": str(f.position_id or ""),
                "px_exact_hit": getattr(f, "px_exact_hit", None),
                "edge_bp": round(float(f.edge_bp or 0.0), 4),
            }
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(_json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001
            # 只记一次警告级别，避免每笔成交刷屏
            if getattr(self, "_fill_basis_warned", False) is not True:
                self._fill_basis_warned = True
                logger.warning("[F257] 成交判定依据落盘失败（不影响交易）: %s", e)

    def _record_account_fill(self, f: PlannedFill, *, fee_rate: float) -> None:
        """把一笔做市成交汇入**统一模拟账户**（按策略分账）。"""
        if not self.account_id:
            return
        try:
            from backend.services.rebate_arb.arbitrage_paper_account_service import (
                ArbitragePaperAccountService,
            )

            notional = abs(f.qty * f.px)
            fee_usd = abs(fee_rate) * notional
            # 账户侧：扣手续费 + 记已实现盈亏（价差 + 价格），合计等于本笔净额
            svc = ArbitragePaperAccountService()
            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    # ── [F285 2026-09-21] **保留仓位周期标识** ────────────────────
                    # 病根（H76 实测）：这里此前**硬编码** `f"mm:{f.symbol}"` ⇒
                    # 账本里 `position_id` 只有 8 个不同值（就是 8 个币名），
                    # **往返配对信息全部丢失** ⇒ 无法做「每往返净额」归因 ✗✗。
                    # 而 `PlannedFill.position_id` **本来就带着**周期号
                    # （`fill_basis.jsonl` 里能看到 `mm:SOL:3` 这种形式，F279 已实现）——
                    # 是这一行把它丢掉的。这是本项目第 28 条教训的重复：
                    # **"有字段" ≠ "被写进去了"。**
                    # 回退：`f.position_id` 为空时退回旧格式，保证不产生 NULL。
                    _pid = str(getattr(f, "position_id", "") or "").strip()
                    if not _pid:
                        _pid = f"mm:{f.symbol}"
                    svc.record_paper_leg_fill(
                        db, int(self.account_id), self.venue,
                        position_id=_pid,
                        strategy_type=self.strategy_type,
                        phase="flatten" if f.is_flatten else "fill",
                        fee_paid=fee_usd, rebate_received=0.0, slippage_cost=0.0,
                        pnl_delta=float(f.spread_usd + f.price_usd),
                        note=f"{f.symbol} {f.side} {'平仓' if f.is_flatten else '做市成交'}",
                        metadata={"lane_id": self.lane_id, "symbol": f.symbol,
                                  "side": f.side, "qty": round(f.qty, 10),
                                  "px": f.px, "mid": f.mid,
                                  "edge_bp": round(f.edge_bp, 4),
                                  "position_id": _pid,
                                  "source": "F60_shadow"},
                        force_log=True,
                    )
                    # record_paper_leg_fill 只写不提交（由调用方控制事务边界）；
                    # 做市驱动器是独立调度任务，必须自己提交，否则流水被回滚。
                    db.commit()
        except Exception as e:
            logger.warning("[F60] 账户入账失败（不影响车道账本）: %s", e)

    # ── 主循环 ──
    def _refresh_decay_block(self, now_ts: float) -> None:
        """[h759] 每 60s 刷新"实时淘汰"集合(亏损判据命中的币 ⇒ 只减不加)。

        为什么不只靠雷达:雷达换币是 15 分钟级(宇宙变更 + 驻留保护),而**风险
        不该等** —— 命中的币在下一个 tick 就停止加仓,持仓靠减仓侧自然流出;
        雷达随后在 ≤15 分钟内完成正式换币。
        失败时保持上一轮集合(不因读库失败而放开亏损币)。
        """
        if now_ts - float(getattr(self, "_decay_block_ts", 0.0) or 0.0) < 60.0:
            return
        self._decay_block_ts = now_ts
        try:
            from backend.services.market_maker.qspeed import pnl_decayed_coins
            _hit = set(pnl_decayed_coins(self.lane_id))
            # [h784 2026-10-04] 并入崩盘熔断(仍在隔离期的币)
            _crash_active = {s for s, until in getattr(self, "_crash_block", {}).items()
                             if float(until) > now_ts}
            # [h786 2026-10-04 用户"现在就是修复"] **回填扫描**:
            # worker 重启会清空崩盘熔断状态(内存态)⇒ 重启后没有新深亏损腿就不会
            # 再触发,重启窗口内已经崩过的币(实测 SI)继续被加仓。
            # ⇒ 每次刷新回看近 60 分钟的深亏损平仓腿(price_bp < −150),把它们的
            #    币立即拉黑(幂等:已在黑名单的跳过)。
            try:
                import psycopg as _pg
                from backend.services.market_maker import attribution as _att
                with _pg.connect(_att._main_dsn(), autocommit=True) as _c, _c.cursor() as _cur:
                    _cur.execute(
                        "SELECT DISTINCT symbol FROM lane_ledger"
                        " WHERE lane_id=%s AND event='fill'"
                        "   AND ts > now() - interval '60 minutes'"
                        "   AND COALESCE(meta_json->>'flatten','false')='true'"
                        "   AND price_bp < -150",
                        (self.lane_id,))
                    for (_sym,) in _cur.fetchall():
                        _s = str(_sym or "").upper()
                        if _s and _s not in self._crash_block:
                            self._crash_block[_s] = time.time() + 1800.0
                            logger.warning("[h786] 回填崩盘熔断:%s(近 60 分钟有深亏损平仓腿)", _s)
                            self._push_event("crash_block",
                                             f"回填崩盘熔断:{_s} ⇒ 30 分钟只减不加")
            except Exception:
                pass
            _hit |= _crash_active
            if _hit != set(getattr(self, "_decay_block", set()) or set()):
                logger.warning("[h759] 实时淘汰集合变更: %s → %s(这些币只减不加)",
                               sorted(getattr(self, "_decay_block", set()) or []),
                               sorted(_hit))
                self._push_event("decay_block",
                                 f"实时淘汰:{','.join(sorted(_hit)) or '（空）'} 只减不加")
            self._decay_block = _hit
        except Exception as _e:
            logger.warning("[h759] 实时淘汰刷新失败(保留上一轮): %s", _e)

    def _funding_for(self, symbol: str) -> float:
        """[h752 C1a] 从 perp_funding(market 库)取 asterdex 最新资金费率,
        300s 缓存。失败/缺失返回 0.0(软闸不动作,旧行为)。"""
        try:
            _now = time.time()
            if not hasattr(self, "_fund_cache"):
                self._fund_cache: Dict[str, float] = {}
                self._fund_ts: float = 0.0
            if _now - getattr(self, "_fund_ts", 0.0) < 300.0:
                return float(self._fund_cache.get(symbol, 0.0))
            from sqlalchemy import text as _st
            from backend.database.connection import market_engine as _me
            with _me.connect() as _c:
                _rows = _c.execute(_st(
                    "SELECT DISTINCT ON (symbol) symbol, funding_rate"
                    " FROM perp_funding WHERE exchange='asterdex'"
                    " AND symbol LIKE '%USDT' ORDER BY symbol, timestamp DESC"
                )).mappings().all()
            _fresh = {}
            for _r in _rows:
                _sym = str(_r["symbol"]).replace("USDT", "").upper()
                _fresh[_sym] = float(_r["funding_rate"] or 0.0)
            self._fund_cache = _fresh
            self._fund_ts = _now
            return float(self._fund_cache.get(symbol, 0.0))
        except Exception:
            return 0.0

    def _dl_fuse_path(self) -> Path:
        # [h750 修 bug] 相对路径依赖进程 CWD:worker 的 CWD 不是项目根时,
        # 读/写静默落到别处 ⇒ 种子不生效、误触发(实测 14:26 伪触发)。
        # 用 __file__ 锚定项目根(parents[3] = backend/services/market_maker 的上三级)。
        return Path(__file__).resolve().parents[3] / "data" / "dl_fuse_state.json"

    def _load_dl_fuse_state(self) -> None:
        import json as _json
        _dbg = []
        try:
            p = self._dl_fuse_path()
            if p.exists():
                d = _json.loads(p.read_text(encoding="utf-8"))
                if d.get("lane_id") == self.lane_id:
                    self._dl_floor = float(d.get("floor_usd") or 0.0)
                    self._dl_pause_until = float(d.get("pause_until") or 0.0)
                    self._dl_trip_count = int(d.get("trip_count") or 0)
                    self._dl_fused_universe = list(d.get("fused_universe") or [])
                    _dbg.append(f"OK floor={self._dl_floor} trips={self._dl_trip_count}")
                else:
                    _dbg.append(f"lane_id_mismatch got={d.get('lane_id')}")
            else:
                _dbg.append(f"file_missing path={p}")
        except Exception as _e:
            _dbg.append(f"EXC {_e}")
        try:
            with open("logs/dl_debug.txt", "a", encoding="utf-8") as _f:
                _f.write(f"{time.time()} LOAD lane={self.lane_id} {','.join(_dbg)}\n")
        except Exception:
            pass

    def _persist_dl_fuse_state(self) -> None:
        import json as _json
        try:
            self._dl_fuse_path().write_text(_json.dumps({
                "lane_id": self.lane_id, "floor_usd": self._dl_floor,
                "pause_until": self._dl_pause_until,
                "trip_count": int(getattr(self, "_dl_trip_count", 0) or 0),
                "fused_universe": list(getattr(self, "_dl_fused_universe", []) or []),
                "ts": time.time(),
            }, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _kpi_toxicity_pause(self, now_ts: float) -> Optional[str]:
        """[h766] markout KPI 毒化熔断:连续 2 轮 toxic ⇒ 停一个冷却周期。

        数据源 `data/markout_kpi_last.json`(h727 任务每 30 分钟刷新,
        字段 ratio=逆向/捕获、verdict)。状态 `data/kpi_toxic_state.json`
        记录 (last_ts, streak, pause_until)。
        · streak ≥ 2 ⇒ pause_until = now + daily_loss_cooldown_sec(15min),streak 清零;
        · 暂停期内返回覆盖原因(前端可见);
        · 读不到 KPI ⇒ 不干预(宁可不管,也不误停)。
        """
        import json as _json
        try:
            _p = Path(__file__).resolve().parents[3] / "data" / "markout_kpi_last.json"
            if not _p.is_file():
                return None
            _k = _json.loads(_p.read_text(encoding="utf-8"))
            _ratio = _k.get("adverse_capture_ratio")
            _ts = float(_k.get("ts") or 0.0)
            if _ratio is None or _ts <= 0:
                return None
            _sp = Path(__file__).resolve().parents[3] / "data" / "kpi_toxic_state.json"
            _st = {}
            if _sp.is_file():
                try:
                    _st = _json.loads(_sp.read_text(encoding="utf-8")) or {}
                except Exception:
                    _st = {}
            # 暂停期内 ⇒ 保持
            _pu = float(_st.get("pause_until") or 0.0)
            if _pu > now_ts:
                return f"kpi_toxic(毒化熔断冷却,{int(_pu - now_ts)}s 后自动复开)"
            if _pu > 0:
                _st["pause_until"] = 0.0
            # [h766b 用户指令] 测试阶段**只提示不暂停**(kpi_toxic_action="alert")
            _act = str(getattr(self.limits, "kpi_toxic_action", "alert") or "alert").lower()
            # 只对**新的一轮** KPI 计数(ts 变化才计)
            if float(_st.get("last_ts") or 0.0) != _ts:
                _st["last_ts"] = _ts
                _toxic = float(_ratio) > 1.2
                _st["streak"] = (int(_st.get("streak") or 0) + 1) if _toxic else 0
                if int(_st.get("streak") or 0) >= 2:
                    if _act == "pause":
                        _cd = float(getattr(self.limits, "daily_loss_cooldown_sec", 900.0) or 900.0)
                        _st["pause_until"] = now_ts + _cd
                        _st["streak"] = 0
                        logger.warning("[h766] KPI 毒化连续 2 轮(ratio=%.2f)⇒ 停 %.0fs",
                                       float(_ratio), _cd)
                        self._push_event("kpi_toxic",
                                         f"KPI 毒化熔断:ratio={float(_ratio):.2f},停 {int(_cd)}s")
                    else:
                        # 只提示:写事件 + 日志 + 状态文件,不动交易
                        logger.warning("[h766b] KPI 毒化连续 2 轮(ratio=%.2f)【仅提示,不暂停】",
                                       float(_ratio))
                        self._push_event("kpi_toxic_alert",
                                         f"⚠ KPI 毒化(仅提示):逆向/捕获={float(_ratio):.2f}"
                                         f"(>1.2 连续 2 轮),markout={_k.get('markout_bp')}bp")
                        _st["streak"] = 0
                try:
                    _sp.write_text(_json.dumps(_st, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
            if float(_st.get("pause_until") or 0.0) > now_ts:
                return f"kpi_toxic(毒化熔断,{int(float(_st['pause_until']) - now_ts)}s 后自动复开)"
            return None
        except Exception:
            return None

    def _daily_loss_state_machine(self, day_pnl: float, now_ts: float) -> Optional[str]:
        """[h749] 冷却式日亏熔断状态机(替代"暂停到日界"的旧行为)。

        语义(用户要求:熔断有时限、到点自动复开、复开后不立即重复触发):
          - 暂停期(now < pause_until)⇒ 返回覆盖原因(含剩余秒数)。
          - 复开期:只有当日亏**跌破记录地板一个限额档**时才再次触发;
            触发一次 = 记录地板 + 停 `daily_loss_cooldown_sec` 秒。
          - 日亏回升越过地板 ⇒ 清地板(全自动复位)。
        `daily_loss_cooldown_sec<=0` ⇒ 返回 None(旧行为,由 plan_tick 内部判)。
        """
        import datetime as _dt

        # ⓪ [h766 2026-10-03] **KPI 毒化熔断**(24/7 实时风险闸,与日亏熔断并列):
        # 动机:实测 KPI 逆向/捕获 = 1.64(toxic>1.2,markout −3.96bp vs 捕获 +2.42bp)
        # 时,日亏仍在扩大 —— 被动报价在**单边趋势**里持续被反向挑选,而日亏阶梯
        # 熔断只按"已经亏了多少"动作,反应太慢(今天到 −6.3U 才跳第 3 次)。
        # 机制:markout KPI 连续 **2 轮** toxic(每轮 30 分钟,由 h727 任务刷新)
        # ⇒ 主动停 `daily_loss_cooldown_sec`(15min)冷却,到点自动复开;
        # 停一次后计数清零,避免"毒化期无限停摆"(复开后若仍毒化,两轮后再停)。
        _kpi_pause = self._kpi_toxicity_pause(now_ts)
        if _kpi_pause:
            return _kpi_pause
        _cd = float(getattr(self.limits, "daily_loss_cooldown_sec", 0.0) or 0.0)
        if _cd <= 0:
            return None
        _pct = float(getattr(self.limits, "daily_loss_stop_pct", 0.0) or 0.0)
        if _pct <= 0 or self.equity <= 0:
            return None
        _limit = abs(self.equity * _pct / 100.0)
        # ① 冷却期内 ⇒ 保持暂停(带剩余时间,供前端提示)
        if self._dl_pause_until > 0 and now_ts < self._dl_pause_until:
            _left = int(self._dl_pause_until - now_ts)
            return f"daily_loss(熔断冷却,{_left}s 后自动复开)"
        # ①′ [h750] 冷却刚到期 ⇒ 强制雷达做一次"按亏损衰减"的换币
        # (不复用原宇宙原地重启:熔断的是**这批币**,换一批再打)
        if self._dl_pause_until > 0 and now_ts >= self._dl_pause_until:
            self._dl_pause_until = 0.0
            self._persist_dl_fuse_state()
            self._radar_last = 0.0
            self._radar_last_decay = 0.0
            logger.warning("[h750] 熔断冷却到期,已强制雷达换币(亏损币衰减)")
        # ② 日亏回升越过**限额线 + 0.5U 裕度** ⇒ 复位计数(熔断周期彻底关闭)。
        # ⚠️ 裕度是必须的:compound 块每 tick 用账户权益重算 equity(实测 283↔300
        # 抖动)⇒ limit 每 tick 波动 ⇒ 无裕度时"刚越过线"的下一个 tick 就因
        # limit 变大而再次越线 → trips 反复 1→0→1 → 每次从 lim_n=1×limit 起跳
        # → 熔断无限循环(dl_debug 实测:tick1 limit=2.10 trip=False → tick2
        # limit=1.983 trips=0 trip=True)。
        _trips = int(getattr(self, "_dl_trip_count", 0) or 0)
        if _trips and day_pnl > -_limit + 0.5:
            self._dl_trip_count = 0
            self._persist_dl_fuse_state()
        # ③ [h750 简化] 触发线**按触发次数阶梯抬高**:第 N+1 次触发线 = 限额×N。
        # 复开后不会因"当日仍亏"而立即再触发(那是 14:42 循环熔断的根因:
        # 地板减法在 floor≈pnl 时对撞)。每次触发 = 停一个冷却周期(15min)。
        _lim_n = _limit * max(1.0, float(_trips + 1))
        try:
            with open("logs/dl_debug.txt", "a", encoding="utf-8") as _f:
                _f.write(f"{now_ts} SM pnl={day_pnl:.3f} trips={_trips} "
                         f"limit={_limit:.3f} lim_n={_lim_n:.3f} floor={self._dl_floor:.3f} "
                         f"pause_until={self._dl_pause_until:.0f} trip={day_pnl <= -_lim_n}\n")
        except Exception:
            pass
        if day_pnl <= -_lim_n:
            self._dl_trip_count = _trips + 1
            self._dl_pause_until = now_ts + _cd
            self._dl_fused_universe = list(self.symbols)
            self._persist_dl_fuse_state()
            _until = _dt.datetime.fromtimestamp(self._dl_pause_until).strftime("%H:%M")
            self._push_event("lane_pause",
                             f"日亏熔断 {day_pnl:.2f}U(第{self._dl_trip_count}次,"
                             f"下次线 −{_limit * (self._dl_trip_count + 1):.2f}U),"
                             f"停 {int(_cd / 60)}min,{_until} 自动复开并换币(熔断宇宙:"
                             f"{','.join(self._dl_fused_universe)})")
            logger.warning("[h750] 日亏熔断 trip=%.2fU 第%d次 复开于 %s 熔断宇宙=%s",
                           day_pnl, self._dl_trip_count, _until,
                           ",".join(self._dl_fused_universe))
            return (f"daily_loss({day_pnl:.2f}U,第{self._dl_trip_count}次,"
                    f"{int(_cd / 60)}min 后自动复开并换币)")
        return None

    def _maybe_reload_meta(self, *, force: bool = False) -> None:
        """[F251] 每 60s 检查一次注册表 meta，params/limits/vol_baseline 变了就热采用。

        F79 的注册表权威性只在 runner 重建时生效；本方法把同一份权威性延伸到运行中：
          - `meta.params` → self.params / self.limits（按 dataclass 字段过滤，与 get_runner 同构）；
          - `meta.replay_baseline.vol_baseline_bp` → 各币 st.vol_baseline_bp（注册表有值才覆写，
            缺失币沿用旧值并告警一次——与 get_runner 的 F96 语义一致）；
          - self.meta 整体刷新（report() 读它）。
        指纹一致时不动作（避免每 tick 重建 dataclass 与日志噪声）。
        """
        now = time.time()
        if not force and self._last_meta_check and now - self._last_meta_check < 60.0:
            return
        self._last_meta_check = now
        try:
            import json

            from backend.services import lane_registry as reg
            from backend.services.market_maker.core import LaneRiskLimits, QuoteParams

            lane = reg.get_lane(self.lane_id)
            if not lane:
                return
            meta = dict(lane.get("meta") or {})
            stored = dict(meta.get("params") or {})
            fp_p = json.dumps({k: stored.get(k) for k in QuoteParams.__dataclass_fields__},
                              sort_keys=True, default=str)
            fp_l = json.dumps({k: stored.get(k) for k in LaneRiskLimits.__dataclass_fields__},
                              sort_keys=True, default=str)
            vb = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})
            fp_v = json.dumps(vb, sort_keys=True, default=str)
            # ── [F283 2026-09-21] **symbols 必须进指纹** ──────────────────────────
            # 病根：原指纹只有 params/limits/vol_baseline ⇒ `apply_to_lane()`
            # （`coin_select_hft.py:384`，写 `meta["symbols"]`）**即使被调用，
            # 运行中的 runner 也不会重新读宇宙** ✗ —— AI 选币换币后引擎照旧挂老币。
            # 这是"AI 选币好像没更新过"的**第二个**原因（第一个是从没人调用 applier）。
            fp_s = json.dumps(list(meta.get("symbols") or []), sort_keys=True, default=str)
            fp = fp_p + "|" + fp_l + "|" + fp_v + "|" + fp_s
            if fp == self._meta_fp and not force:
                return
            if self._meta_fp:
                logger.info("[F251] 车道 %s 注册表 meta 变更，热采用 params/limits/vol_baseline",
                            self.lane_id)
            # ── [F283] 宇宙热更新 ────────────────────────────────────────────────
            # 新增币：建 `SymbolState`（新币无持仓，qty=0）。
            # 移除币：**不动** `self.states` —— 那些持仓会由 `orphan_states()`
            # （F90）识别为孤儿并强制退出 ✓，直接删会丢掉在建仓位 ✗。
            _new_syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
            if _new_syms and _new_syms != list(self.symbols):
                _added = [s for s in _new_syms if s not in self._symbol_set]
                _removed = [s for s in self.symbols if s not in set(_new_syms)]
                self.symbols = _new_syms
                self._symbol_set = set(_new_syms)
                for _s in _new_syms:
                    self.states.setdefault(_s, SymbolState(symbol=_s))
                logger.warning("[F283] 宇宙热更新：+%s  −%s（共 %d 币；"
                               "移除币的持仓由 orphan 逻辑强制退出）",
                               _added, _removed, len(_new_syms))
            self.params = QuoteParams(**{k: v for k, v in stored.items()
                                         if k in QuoteParams.__dataclass_fields__})
            # [F280] env 显式优先（见 apply_env_param_overrides 的说明）
            apply_env_param_overrides(self.params)
            self.limits = LaneRiskLimits(**{k: v for k, v in stored.items()
                                            if k in LaneRiskLimits.__dataclass_fields__})
            # [F282 2026-09-21] `compound_ratio`（= 杠杆倍数的来源）此前**只在
            # `__init__` 读一次** ⇒ 改注册表后**不重启 worker 就不生效** ✗
            # （F189 同一类陷阱：改了但没生效，而 status 里看不出区别）。
            # 腿量由 `compound_ratio × equity` 决定，所以它必须随 meta 热更新 ✓。
            _cr = getattr(self.params, "compound_ratio", None)
            self.compound_ratio = float(_cr) if _cr else 0.0
            for sym, st in self.states.items():
                if vb.get(sym):
                    st.vol_baseline_bp = float(vb[sym])
            self.meta = meta
            self._meta_fp = fp
        except Exception as e:  # pragma: no cover - 热更新失败不影响本 tick 决策
            logger.debug("[F251] 注册表热更新检查失败(忽略): %s", e)

    def _maybe_judge_direction(self, now_ts: float) -> None:
        """[h626 确定性版 2026-09-30] 每 5 分钟用最近 60 分钟**开仓腿**重算方向卡。

        与已停的 LLM 版（读成交账本问 deepseek-flash）的区别：
          · 完全确定性：`judge_direction(min_n=15, worse_bp=-2, gap_bp=2)`，
            0 API 调用、0 延迟、任何一张历史卡都能离线重算验证；
          · 输入改为开仓腿的「成交后至今 markout」（名义加权），不再把
            price_bp=0 的开仓腿喂给模型（LLM 版失效的输入级原因之一）；
          · 四个硬闸由 judge_direction 语义保证（每币最多停一边 / 样本不足
            不停 / 两边分不开不停）；减仓侧豁免与"禁全车道单向"由
            plan_tick 的 block_add_side 应用点保证（h626 测试锁定）。
        取数失败 ⇒ 保留上一张卡并记日志（不因 DB 抖动反复开停）。
        """
        last = float(getattr(self, "_direction_judge_ts", 0.0) or 0.0)
        if float(now_ts) - last < 300.0:
            return
        self._direction_judge_ts = float(now_ts)
        from backend.services.market_maker.direction_card import (
            direction_rows_from_legs, fetch_open_legs, judge_direction,
            summarize_direction,
        )
        log = list(getattr(self, "_direction_log", None) or [])
        try:
            legs = fetch_open_legs(lane_id=self.lane_id, minutes=60)
            mids = self._direction_mids()
            rows = direction_rows_from_legs(legs, mids)
            # [h652 §9.3 候选] min_n 自适配:0=固定 15(现网);1=按该边 60 分钟腿频
            # clamp(round(0.25×rate), 12, 30)。默认关,上线须独立试跑。
            min_n_by: Optional[Dict[str, int]] = None
            if float(getattr(self.limits, "direction_min_n_adaptive", 0.0) or 0.0) > 0:
                from backend.services.market_maker.attribution import adaptive_min_n
                min_n_by = {}
                for r in rows:
                    sym = str(r.get("symbol") or "").upper()
                    n = int(r.get("n") or 0)
                    if n > 0:
                        min_n_by[sym] = adaptive_min_n(float(n))
            card = judge_direction(rows, min_n=15, worse_bp=-2.0, gap_bp=2.0,
                                   min_n_by=min_n_by)
            summary = summarize_direction(rows, card)
            self._direction_block = dict(card or {})
            log.append({
                "ts": float(now_ts), "text": summary,
                "n_legs": len(legs), "n_rows": len(rows),
            })
            self._direction_card = {
                "every_sec": 300, "stopped": False, "source": "deterministic",
                "as_of": float(now_ts), "block_add": dict(card or {}),
                "summary": summary, "log": log[-36:],
                # [h664] 融合分数影子(五层算法的每币读数;dir_score_fusion=0 不作用交易,
                # 但面板必须能看见新算法在算什么)
                "fused": {k: v for k, v in (getattr(self, "_dir_score", {}) or {}).items()
                          if k in set(self.symbols or [])},
            }
            self._direction_log = log[-36:]
        except Exception as e:  # 取数失败:保留上一张卡,方向闸不因 DB 抖动反复开停
            logger.warning("[h626] 确定性方向卡取数失败(保留上一张卡): %s", e)
            log.append({"ts": float(now_ts), "text": f"取数失败保留上一张卡: {e}"})
            self._direction_log = log[-36:]
            if isinstance(getattr(self, "_direction_card", None), dict):
                self._direction_card["log"] = self._direction_log

    def _maybe_update_qspeed(self, now_ts: float) -> None:
        """[h657] Q 速控(腿速自适应):每 60s 重算每币 Q 分数并落动作。

        limits.q_speed_gate:0=影子(只算只记,mult 恒 1.0,不动交易);
        1=减速侧启用(Q<0.4 停加仓 / 0.4~0.7 半速,滞后再武装)。
        动作只作用于加仓腿目标量(plan_tick 的 q_size_mult),减仓腿 F91 豁免。
        """
        last = float(getattr(self, "_q_update_ts", 0.0) or 0.0)
        if float(now_ts) - last < 60.0:
            return
        self._q_update_ts = float(now_ts)
        try:
            from backend.services.market_maker.qspeed import (
                append_history, compute_q_scalp, gather_coin_metrics, speed_action,
            )
        except Exception as e:
            logger.warning("[h657] Q 速控模块加载失败: %s", e)
            return
        gate = float(getattr(self.limits, "q_speed_gate", 0.0) or 0.0)
        _q_state = dict(getattr(self, "_q_state", {}) or {})
        out: Dict[str, Any] = {}
        for sym in list(self.symbols):
            try:
                m = gather_coin_metrics(self.lane_id, sym)
                if m is None:   # [h664 审计#3] 数据源故障:保留上一状态,不静默清零
                    logger.warning("[h657] Q 速控 %s 指标取数失败(保留上一状态)", sym)
                    continue
                # [h898] scalp 口径:成交率 + 实时 edge + 市场流(不再用做市价差/捕获)
                q = compute_q_scalp(fill_rate_per_h=m["fill_rate_per_h"],
                                    edge_1h_bp=m.get("edge_1h_bp", 0.0),
                                    flow_30m=m.get("flow_30m", 0.0),
                                    stop_rate_30m=m.get("stop_rate_30m", 0.0))
                st = dict(speed_action(q, _q_state.get(sym),
                                       now_min=float(now_ts) / 60.0))
                st["q"] = q
                if gate <= 0:      # 影子模式:只算只记,交易行为逐字不变
                    st["mult"] = 1.0
                    st["action"] = "shadow:" + str(st.get("action") or "")
                # [h672] Q 停加仓/风暴事件 → 前端警报(先读旧状态再覆盖)
                # [h684] **重启后第一次不推**:重启时 `_q_state` 为空 ⇒ 已停币会被
                # 反复当成"新进入暂停"重新播报(用户:"通知怎么还在")。只在
                # 进程内真正发生 pause 转移时推。
                _prev_act = str((_q_state.get(sym) or {}).get("action") or "")
                _q_state[sym] = st
                _first_q = not bool(getattr(self, "_q_first_done", False))
                if gate > 0 and not _first_q \
                        and str(st.get("action") or "") == "pause" \
                        and "pause" not in _prev_act:
                    _storm = float(m.get("stop_rate_30m") or 0.0)
                    self._push_event(
                        "q_pause",
                        (f"{sym} 止损风暴 {_storm:.0f} 次/30min,停加仓"
                         if _storm >= 2 else f"{sym} Q={q:.2f} 低质量,停加仓"),
                        now_ts)
                # ⚠️ [h662c 审计#2 修复] 不能写 `st.get("mult") or 1.0`:
                # mult=0.0(停加仓)是合法值,or 会把它吞成 1.0 ⇒ 心跳/历史说谎
                # 且 q_decayed_coins 的数据源断链(falsy-0 第四次)。
                _mult = float(st.get("mult") if st.get("mult") is not None else 1.0)
                # [h686] 带上"停加仓开始时间/退避"⇒ 前端能显示**试探复入倒计时**
                out[sym] = {"q": q, "action": st.get("action"), "mult": _mult}
                if st.get("paused_since") is not None:
                    out[sym]["paused_min"] = round(
                        float(now_ts) / 60.0 - float(st["paused_since"]), 2)
                    out[sym]["retry_in_min"] = round(max(
                        0.0, float(st.get("backoff_min") or 10.0)
                        - (float(now_ts) / 60.0 - float(st["paused_since"]))), 2)
                append_history({"ts": float(now_ts), "symbol": sym, "q": q,
                                "action": st.get("action"), "mult": _mult,
                                "gate": gate, "metrics": m})
            except Exception as e:  # 单币失败不阻断其它币,也不阻断交易
                logger.warning("[h657] Q 速控 %s 计算失败: %s", sym, e)
        self._q_state = _q_state
        self._q_snapshot = out
        self._q_first_done = True   # [h684] 之后才允许播报"进入暂停"事件
        # [h664] 方向分数影子历史(与 Q 同 60s 节流落盘,供分桶验证)
        try:
            from backend.services.market_maker.dirscore import append_history as _dh
            for _s, _ds in (getattr(self, "_dir_score", {}) or {}).items():
                _dh({"ts": float(now_ts), "symbol": _s, **_ds})
        except Exception as e:
            logger.warning("[h664] 方向分数历史落盘失败: %s", e)

    def _direction_mids(self) -> Dict[str, float]:
        """[h626 确定性版] 方向卡用的当前中价:优先新鲜盘口,退而取 state.quote_mid。

        只在 5 分钟判定时调用（不在每秒热路径），允许逐币查询。
        """
        out: Dict[str, float] = {}
        for s in list((getattr(self, "states", None) or {}).keys()):
            st = self.states.get(s)
            mid = 0.0
            try:
                fb = self._fresh_book_for(s)
                if fb is not None and float(fb[0] or 0.0) > 0 \
                        and float(fb[1] or 0.0) > float(fb[0] or 0.0):
                    mid = (float(fb[0]) + float(fb[1])) / 2.0
            except Exception:
                mid = 0.0
            if mid <= 0 and st is not None:
                mid = float(getattr(st, "quote_mid", 0.0) or 0.0)
            if mid > 0:
                out[str(s)] = mid
        return out

    def _ofi_60s(self, symbol: str, hi_ms: int) -> float:
        """[h689] **流因子(OFI)独立 60s 窗口** —— 三个综合里权重 0.40 的那个。

        病根(2026-10-01 实测):`fetch_market` 里 OFI 复用"成交判定窗口",而那个窗口
        的上界被 `qts_ms`(当前可见挂单时间)截断 ⇒ 窗口只有 **288ms**
        (`win[1790846200738,1790846201024]`),聚合表没有这么细的桶 ⇒
        `n=0 sv=0 bv=0` ⇒ **OFI 恒为 0**(方向分数只剩微价+趋势两路,
        所以融合一直"显得没用")。

        修法:OFI 用固定 60s 回溯窗口(设计口径 h351:E[Δmid|OFI] 在 30/60/120s),
        与成交判定窗口**解耦**;5s 缓存避免每 tick 打库。
        """
        try:
            now_ms = int(time.time() * 1000)
            _cache = getattr(self, "_ofi_cache", None)
            if _cache is None:
                _cache = self._ofi_cache = {}
            hit = _cache.get(symbol)
            if hit and now_ms - hit[0] < 5000:
                return hit[1]
            from sqlalchemy import text as _t

            from backend.core.tenant import system_identity
            from backend.database.connection import MarketSessionLocal

            with system_identity():
                with MarketSessionLocal() as db:
                    # [h689b] 用**原始逐笔**算流因子:聚合表 `market_trades_aggregated`
                    # 实测漏数据(BNB 近 2 分钟买量=0,而原始逐笔有 1.95 的主动买)
                    # ⇒ 会把 OFI 算成 −1.0(假"全卖")。原始表带 `is_buyer_maker`,
                    # 是真值口径,且与成交判定同源。
                    row = db.execute(_t(
                        "SELECT COALESCE(SUM(qty) FILTER"
                        "   (WHERE is_buyer_maker IS FALSE),0) AS bv,"
                        " COALESCE(SUM(qty) FILTER"
                        "   (WHERE is_buyer_maker IS TRUE),0) AS sv"
                        " FROM asterdex_trades"
                        " WHERE symbol=CONCAT(CAST(:s AS TEXT),'USDT')"
                        "   AND event_ts_ms > :lo AND event_ts_ms <= :hi"
                    ), {"s": symbol, "lo": int(hi_ms) - 60_000,
                        "hi": int(hi_ms)}).mappings().first()
            bv = float(row["bv"] or 0.0) if row else 0.0
            sv = float(row["sv"] or 0.0) if row else 0.0
            ofi = ((bv - sv) / (bv + sv)) if (bv + sv) > 0 else 0.0
            _cache[symbol] = (now_ms, ofi)
            return ofi
        except Exception:
            return 0.0

    def _prob_side_for(self, symbol: str) -> Optional[str]:
        """[2026-10-08 用户规则2] 趋势概率判方向(buy/sell/None)。
        用于选币:有明确信号的币优先保进名单。失败 ⇒ None(不挡)。"""
        try:
            from backend.services.market_maker.trend_prob import (
                load_model, predict_side_edge)
            from backend.services.market_maker.trend_features import compute_features
            now_ms = int(time.time() * 1000)
            _cache = getattr(self, "_prob_side_cache", None)
            if _cache is None:
                _cache = self._prob_side_cache = {}
            hit = _cache.get(symbol)
            if hit and now_ms - hit[0] < 8000:
                return hit[1]
            from sqlalchemy import text as _t
            from backend.core.tenant import system_identity
            from backend.database.connection import MarketSessionLocal
            with system_identity():
                with MarketSessionLocal() as db:
                    ofi = self._ofi_60s(symbol, now_ms)
                    rows = [(float(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]))
                            for x in db.execute(_t(
                                "SELECT event_ts_ms/1000.0, bid_px, ask_px, bid_qty, ask_qty"
                                " FROM asterdex_book_ticker"
                                " WHERE symbol=CONCAT(CAST(:s AS TEXT),'USDT')"
                                " AND event_ts_ms > :t AND bid_px>0 ORDER BY event_ts_ms"),
                                {"s": symbol, "t": now_ms - 600_000}).fetchall()
                            if x and x[1] and x[2]]
            feats = compute_features(rows, ofi)
            m = load_model(str(Path(__file__).resolve().parents[3]))
            r = predict_side_edge(str(Path(__file__).resolve().parents[3]),
                                  feats, model=m)
            side = r[0] if r else None
            _cache[symbol] = (now_ms, side)
            return side
        except Exception:
            return None

    def _trades_60s(self, symbol: str, hi_ms: int) -> int:
        """[2026-10-08] 近 60s 真实成交笔数(活币门槛)。死币(0 笔)不进候选。"""
        try:
            now_ms = int(time.time() * 1000)
            _cache = getattr(self, "_trades_cache", None)
            if _cache is None:
                _cache = self._trades_cache = {}
            hit = _cache.get(symbol)
            if hit and now_ms - hit[0] < 5000:
                return hit[1]
            from sqlalchemy import text as _t
            from backend.core.tenant import system_identity
            from backend.database.connection import MarketSessionLocal
            with system_identity():
                with MarketSessionLocal() as db:
                    row = db.execute(_t(
                        "SELECT COUNT(*) AS n FROM asterdex_trades"
                        " WHERE symbol=CONCAT(CAST(:s AS TEXT),'USDT')"
                        "   AND event_ts_ms > :lo AND event_ts_ms <= :hi"
                    ), {"s": symbol, "lo": int(hi_ms) - 60_000,
                        "hi": int(hi_ms)}).mappings().first()
            n = int(row["n"] or 0) if row else 0
            _cache[symbol] = (now_ms, n)
            return n
        except Exception:
            return 0

    def _live_bridge(self):
        """[h665] 实盘执行桥:mix live 车道惰性初始化;纸面车道返回 None。"""
        if self.live_mode is False:
            return None
        if self._live is None:
            from backend.services.market_maker.live_bridge import LiveBridge
            self._live = LiveBridge(self._lane_meta())
            if not self._live.enabled:
                logger.warning("[h665] 实盘车道 %s 未配置 API Key,执行桥 no-op",
                               self.lane_id)
        return self._live

    def _lane_meta(self) -> dict:
        from backend.services import lane_registry as reg
        lane = reg.get_lane(self.lane_id) or {}
        return {"lane_id": self.lane_id, "mode": lane.get("mode"),
                "status": lane.get("status"), "meta": lane.get("meta") or {}}

    def tick(self, *, now_ts: Optional[float] = None) -> Dict[str, Any]:
        """跑一个调度周期。返回本 tick 摘要（可直接进日志/API）。"""
        from backend.services.market_maker.core import InventoryBook, Position

        # [h843 用户"怎么调高速度"] 逐段计时探针:把 tick 的耗时拆开,
        # 才能知道该优化谁(实测 tick 工作约 630ms,CPU 只占 15% ⇒ 全在等 I/O)。
        # 开关:MM_TICK_PROBE=1(默认开);开销=几次 perf_counter,可忽略。
        _ph: Dict[str, float] = {}
        _tp0 = time.perf_counter()

        def _mark(name: str) -> None:
            _ph[name] = round((time.perf_counter() - _tp0) * 1000, 1)

        ok, reason = check_fee_guard(self.maker_fee_bp)
        if not ok:
            self.last_error = reason
            return {"ok": False, "reason": reason, "decisions": []}
        # [F251] 注册表 meta 热更新（60s 一次，指纹变化才采用）
        self._maybe_reload_meta()
        now_ts = float(now_ts or time.time())
        self._maybe_judge_direction(now_ts)
        # [h657] Q 速控(内部 60s 节流;影子模式不动作)
        self._maybe_update_qspeed(now_ts)
        # [h759] 实时淘汰快路(60s 节流):命中的币只减不加,不等雷达周期
        self._refresh_decay_block(now_ts)
        # [h680] **自驱动宇宙雷达**(进程内节奏,不依赖 DSH 计划任务):
        # 每 5 分钟轻量衰减巡检;每 30 分钟全量评估。衰减驱动替换立即生效。
        self._maybe_refresh_universe(now_ts)
        # [h665] 实盘账户刷新(5s 节流;余额/持仓进桥快照供风控与心跳)
        if self.live_mode and now_ts - self._live_last_refresh >= 5.0:
            _lb = self._live_bridge()
            if _lb is not None:
                _lb.refresh_account()
            self._live_last_refresh = now_ts
        if not self._first_tick_ts:
            self._first_tick_ts = now_ts
        since_ms = int((self.last_tick_ts or (now_ts - 60.0)) * 1000)
        try:
            market = self.fetch_market(since_ms)
        except Exception as e:
            self.last_error = f"fetch_market: {e}"
            logger.warning("[F60] fetch_market 失败: %s", e)
            return {"ok": False, "reason": self.last_error, "decisions": []}
        _mark("fetch_market")

        decisions: List[Dict[str, Any]] = []
        data_ages: List[float] = []
        # [§82/P5-A] 日亏闸输入：整个 tick 只读一次账本（各币共用同一日亏）
        day_pnl = 0.0
        _dl_override: Optional[str] = None
        _dl_managed = False
        if lane_limits_enforce_enabled():
            day_pnl = lane_day_pnl_usd(self.lane_id, now_ts)
            _dl_override = self._daily_loss_state_machine(day_pnl, now_ts)
            _dl_managed = float(getattr(self.limits, "daily_loss_cooldown_sec", 0.0) or 0.0) > 0
            if day_pnl:
                logger.info("[F60] 车道 %s 本日已实现净额 = %.2f USD（日亏闸输入）",
                            self.lane_id, day_pnl)
        # [F85 2026-09-14] 复利模式：每 tick 读模拟账户权益，腿量 = 权益 × 比例。
        # 复利研究结论（30 天/6 天回放）：全权益腿复利 +6.25% vs 固定 +5.34%
        # （6 天窗口），且回撤不增；固定腿量 + 权益联动上限反而会在小额亏损后
        # 死锁入场侧（上限 < 腿量）。容量上限：$10k 腿 41% 段被队列份额截断、
        # $30k 腿 73%——复利增长在 $10k 腿量附近开始饱和。
        if (self.compound_ratio or 0) > 0 and self.account_id:
            try:
                _eq = self._read_account_equity()
                if _eq and _eq > 0:
                    self.equity = float(_eq)
                    self.fill_notional = max(10.0, float(self.compound_ratio) * self.equity)
            except Exception as _e:
                self.last_error = f"compound_equity: {_e}"
        # [F72] 组合级共享库存账本：所有币共用，`max_net_exposure_ratio`
        # （组合净敞口上限）才有意义——此前每币各自一本账，6 个币各持 $100
        # 时组合已 $600 同向暴露，却谁都看不到。
        from backend.services.market_maker.core import InventoryBook, Position

        # [F94 2026-09-14] 在挂同向腿的风险预留（跨币累加）：净敞口上限必须把
        # **已经挂在场的腿**算进去，否则多币同向同时成交会把敞口顶穿（实测回放
        # 峰值 7.9× 上限、实盘 3.4×）。本 tick 每个币重挂时先减掉自己的旧贡献，
        # 再按新挂单加回（等价于「撤旧单 → 挂新单」的真实时序）。
        pending_up = 0.0
        pending_down = 0.0
        pending_gross = 0.0
        _pending = {"up": 0.0, "down": 0.0, "gross": 0.0}

        shared_book = InventoryBook()
        marks: Dict[str, float] = {}
        # [F90] 孤儿持仓同样计入共享库存账本：净敞口上限必须覆盖真实风险，
        # 不能因为币种不在宇宙里就「看不见」它的仓位。
        for s in self.risk_symbols():
            m0 = market.get(s)
            st0 = self.states.get(s)
            if m0 and st0 and abs(st0.qty) > 1e-12:
                shared_book.positions[s] = Position(
                    qty=st0.qty, avg_px=st0.avg_px, avg_mid=st0.avg_mid,
                    opened_ts=st0.opened_ts, last_ts=st0.last_ts)
            if m0:
                marks[s] = float(m0["mid"])
        # [F94c] 在挂腿预留：必须在 marks 建好之后，按「加仓/减仓」区分计入
        for _s in self.risk_symbols():
            _st = self.states.get(_s)
            if not _st:
                continue
            _u, _d, _g = pending_contrib(_st, marks.get(_s, 0.0), self.fill_notional)
            pending_up += _u
            pending_down += _d
            pending_gross += _g
        _pending = {"up": pending_up, "down": pending_down, "gross": pending_gross}
        _flow_on = float(getattr(self.limits, "active_flow_mode", 0.0) or 0.0) > 0
        _exit_only = []
        if _flow_on:
            _exit_only = [s for s, st in self.states.items()
                          if s not in self._symbol_set
                          and abs(float(st.qty or 0.0)) > 1e-12]
        for s in list(self.symbols) + _exit_only:
            m = market.get(s)
            st = self.states.setdefault(s, SymbolState(symbol=s))
            if not m:
                decisions.append({"symbol": s, "action": "pause", "skip": "no_market"})
                continue
            if s in _exit_only:
                m["flow_exit_only"] = True
            fresh, age, why = check_data_freshness(m.get("ts_ms"), now_ts)
            # [h621 临时诊断] 每 4 拍记一次。1 秒一拍时不能每个币都写。
            # 逐笔诊断只在显式打开时写。32 个币每拍都写，日志涨到几百万行，循环会被磁盘拖住。
            if int(self.ticks) % 4 == 0 and os.getenv("MM_TICK_DIAG", "").strip() in ("1", "true", "True"):
                try:
                    import pathlib as _pl4
                    _dp4 = _pl4.Path(__file__).resolve().parents[3] / "logs" / "h621_diag.log"
                    with open(_dp4, "a", encoding="utf-8") as _f:
                        _f.write(f"{time.strftime('%H:%M:%S')} LOOP {s} "
                                 f"tick_fill={m.get('tick_fill')} fresh={fresh} why={why} "
                                 f"seg=({m.get('seg_low')},{m.get('seg_high')}) "
                                 f"qb={st.quote_bid} qa={st.quote_ask} qts_age={round(now_ts-st.quote_ts,1) if st.quote_ts else None}\n")
                except Exception:   # noqa: BLE001
                    pass
            if age >= 0:
                data_ages.append(age)
            if not fresh:
                # 主动流有仓且盘口过期：仍进入离场（吃单避险），不把仓冻住。
                _afm_stale = float(getattr(self.limits, "active_flow_mode", 0.0) or 0.0)
                if not (_afm_stale > 0 and st is not None and abs(float(st.qty or 0.0)) > 1e-12):
                    st.quote_bid = st.quote_ask = st.quote_ts = 0.0
                    decisions.append({"symbol": s, "action": "pause", "skip": why,
                                      "data_age_sec": round(age, 1)})
                    continue
                m["book_stale"] = True
            # [F102] mid_hist 只在**快照更新**时追加（与回放"每快照一条"同口径）：
            # 此前每 tick 无条件追加 ⇒ 重复率 55~78% ⇒ 冻结检测窗口被拉长 2~4 倍
            # ⇒ 冻结档几乎不生效 ⇒ 实盘长期按 6~9bp 挂而回放按 3bp 挂 ⇒ 成交只有 0.38×。
            # [h663 审计#6 修复] tick 源(h653)下 snap_ms=墙钟,每秒都变 ⇒ mid_hist 从
            # 15s 一条被压成 1s 一条,所有按"期"标定的窗口(趋势20期/VPN20桶/r900/σ)
            # 压缩 ~15×。修法:去重比较按 15s 桶标签,mid 值仍用实时盘口。
            _snap_ms_now = int(m.get("ts_ms") or 0)
            if _snap_ms_now > 0:
                _snap_ms_now -= _snap_ms_now % SEG_BUCKET_MS
            if _snap_ms_now != int(st.last_mid_src_ms or 0):
                # [F279 2026-09-16] 停机断点：先补齐中间快照，再追加本条。
                # `mid_splice_on_gap` 默认 **False** ⇒ 与接线前逐字一致（可一键回退 ✓）。
                _prev_ms = int(st.last_mid_src_ms or 0)
                _gap_ms = (_snap_ms_now - _prev_ms) if _prev_ms > 0 else 0
                if (_gap_ms > MID_SPLICE_MIN_GAP_MS
                        and bool(getattr(self.limits, "mid_splice_on_gap", False))):
                    _pts = self._splice_mid_hist(st, _prev_ms, _snap_ms_now)
                    self.gap_splices += 1
                    self.gap_splice_points += int(_pts)
                    self.gap_last[st.symbol] = {
                        "ts": _snap_ms_now, "gap_ms": _gap_ms, "spliced": int(_pts)}
                st.mid_hist.append(float(m["mid"]))
                st.last_mid_src_ms = _snap_ms_now
                if len(st.mid_hist) > 240:
                    st.mid_hist = st.mid_hist[-240:]
                # [h338] VPIN 滚动 |OFI|：与 mid_hist 同口径（只在快照更新时追加）
                # [h692c] VPIN 窗口与标定对齐:**每 15s 追加一次** ⇒ 20 样本 ≈ 300s。
                # 病根:原实现每 tick(1s)追加 ⇒ 20 样本只是 ~20s 窗口,比标定短 15×
                # ⇒ 高波动时 VPIN 恒高、闸门长亮(实测 vpin_high 占拦截第一,633 次)。
                _vt = getattr(self, "_vpin_append_ts", None)
                if _vt is None:
                    _vt = self._vpin_append_ts = {}
                if float(now_ts) - float(_vt.get(s, 0.0) or 0.0) >= 15.0:
                    _vt[s] = float(now_ts)
                    st.ofi_abs_hist.append(abs(float(m.get("ofi") or 0.0)))
                    if len(st.ofi_abs_hist) > 20:
                        st.ofi_abs_hist = st.ofi_abs_hist[-20:]
                # [h454] 自适应趋势门槛的分布输入：近 240 期 |r300|（≈1h，与 mid_hist 同口径）
                # ⚠️ 第一版写成裸 `except: pass` + 依赖外层未导入的 trend_move_bp
                #    ⇒ NameError 被静默吞掉、ar300_hist 恒为空、闸门"接了线没通电" ✗
                #    （与 F296 同类教训）。现在显式局部导入 + 失败只告警一次。
                try:
                    from backend.services.market_maker.core import (
                        trend_move_bp as _tmb_h454,
                    )
                    _tm = _tmb_h454(st.mid_hist, int(
                        getattr(self.limits, "trend_lookback", 20) or 20))
                    st.ar300_hist.append(abs(float(_tm or 0.0)))
                    if len(st.ar300_hist) > 240:
                        st.ar300_hist = st.ar300_hist[-240:]
                except Exception as _e454:  # noqa: BLE001
                    if not getattr(self, "_h454_warned", False):
                        logger.warning("[h454] ar300_hist 追加失败（自适应闸将退回绝对下限）: %s",
                                       _e454)
                        self._h454_warned = True
            # [F102] 判定输入快照：被检验的挂单（重挂前的 quote）+ 区间高低/主动量
            _qb0, _qa0, _qm0 = (float(st.quote_bid or 0.0), float(st.quote_ask or 0.0),
                                float(st.quote_mid or 0.0))
            # [F179 2026-09-15] 观测口径必须与**判定对象**一致：F176 之后 `plan_tick`
            # 判的是"延迟一档的那张单"（`m["judged_quote"]`）⇒ 穿越/地板/对照计数也必须
            # 用它 ✓，否则会出现"成交数 > 穿越数"这种自相矛盾的读数（实测 36 笔成交 /
            # 43 次穿越 ✗）。未启用延迟（L=0）时等于旧行为 ✓。
            if self.judge_lag_buckets > 0:
                _jq_obs = m.get("judged_quote") or (0.0, 0.0, 0.0)
                _qb0, _qa0, _qm0 = (float(_jq_obs[0] or 0.0), float(_jq_obs[1] or 0.0),
                                    float(_jq_obs[2] or 0.0))
            # [F94] 撤旧单 → 挂新单：先减掉本币旧的在挂腿，plan_tick 挂完后再加回
            # [F94c] 撤旧单 → 挂新单：按「加仓/减仓」区分先减掉本币旧贡献
            _u0, _d0, _g0 = pending_contrib(st, marks.get(s, 0.0), self.fill_notional)
            _pending["up"] = max(0.0, _pending["up"] - _u0)
            _pending["down"] = max(0.0, _pending["down"] - _d0)
            _pending["gross"] = max(0.0, _pending["gross"] - _g0)
            # [F74] 波动信号：当前已实现波动相对基准的倍数（低波动≈0 → w 退化为 w_base）
            from backend.services.market_maker.core import realized_vol_bp

            vol_cur = realized_vol_bp(st.mid_hist, self.limits.vol_window)
            sigma = (max(0.0, vol_cur / st.vol_baseline_bp - 1.0)
                     if st.vol_baseline_bp > 0 else 0.0)
            # [h667] 订单速率只在真正撤改时扣。只看盘、价没变，不计单。
            # 先前每个币每一秒都扣 1 次：32 个币 × 60 秒 = 1920，超过 1200/分，
            # 排在后面的币被模拟 429 挡住，该挂的单也出不去。
            _rate_bid = float(st.quote_bid or 0.0)
            _rate_ask = float(st.quote_ask or 0.0)
            # [h688] **方向分数融合 D(三个综合)前置计算**:微价+流+趋势,
            # 必须在 plan_tick 之前算,才能用 D 决定本 tick 的加仓量(否则滞后一 tick)。
            # fusion=0/1 = 影子(只算只记);fusion>=2 = **中段带否决**:
            #   |D| < dir_min_abs(默认 0.35)= 三路不一致 ⇒ 加仓腿量 ×0(=停加仓)。
            # 依据(12h 影子分桶):|D| 最低桶 −0.25bp,其余桶 +0.38~+0.66bp;
            # 尾部桶 +0.04 不可预测 ⇒ **只用已验证的低分否决,不用尾部分数放大仓位**。
            _d_val = None
            try:
                from backend.services.market_maker.core import trend_move_bp as _tmb_d
                from backend.services.market_maker.dirscore import direction_score
                _d_val = direction_score(
                    mp_skew_bp=float(m.get("mp_skew") or 0.0),
                    ofi=float(m.get("ofi") or 0.0),
                    trend_bp=float(_tmb_d(list(st.mid_hist or []), 20) or 0.0),
                    # [h694] 15 分钟 fade 项(60×15s=900s;mid_hist keep=240 足够)
                    trend900_bp=float(_tmb_d(list(st.mid_hist or []), 60) or 0.0))
                self._dir_score[s] = {"d": _d_val,
                                      "mp": float(m.get("mp_skew") or 0.0),
                                      "ofi": float(m.get("ofi") or 0.0)}
            except Exception:  # 影子计算失败不影响交易链路
                self._dir_score[s] = {"d": None}
            # [h692] **尺寸倾斜(fusion>=3)**:否决版与侧封锁版都实测 0 成交,
            # 正确用法是两侧都挂、把仓位按 D 倾斜(bid=1+kD, ask=1−kD)。
            # fusion=2(侧封锁)保留但默认不用。
            _dir_mult = 1.0
            _dir_block_side = None
            _add_size_mult = None
            try:
                _fusion = float(getattr(self.limits, "dir_score_fusion", 0.0) or 0.0)
                _dmin = float(getattr(self.limits, "dir_min_abs", 0.15) or 0.15)
                if _fusion >= 3.0 and _d_val is not None:
                    _k = float(getattr(self.limits, "dir_skew_k", 0.5) or 0.5)
                    _db = max(0.25, min(1.75, 1.0 + _k * float(_d_val)))
                    _da = max(0.25, min(1.75, 1.0 - _k * float(_d_val)))
                    _add_size_mult = {"bid": _db, "ask": _da}
                elif _fusion >= 2.0 and _d_val is not None:
                    if float(_d_val) >= _dmin:
                        _dir_block_side = "sell"      # 看涨 ⇒ 不挂卖(不加空)
                    elif float(_d_val) <= -_dmin:
                        _dir_block_side = "buy"       # 看跌 ⇒ 不挂买(不加多)
            except Exception:
                _dir_block_side = None
            _card_block = (getattr(self, "_direction_block", {}) or {}).get(s)
            _merged_block = _dir_block_side or _card_block
            # [h691] **反事实记录**:被封锁的那一侧"如果挂了会怎样"。
            # 病根:方向卡用我们自己的成交 markout 学习 ⇒ 天然带选择偏差
            # (只看到"成交了的腿",看不到"被它封掉的机会")。要判定封锁对不对,
            # 必须记录反事实:封锁时刻的中价 + 假设挂单价,之后离线算 forward markout。
            # 节流:每币每 60s 最多一条。
            if _merged_block:
                try:
                    _sh = getattr(self, "_blk_shadow", None)
                    if _sh is None:
                        _sh = self._blk_shadow = {}
                    if now_ts - float(_sh.get(s, 0.0) or 0.0) >= 60.0:
                        _sh[s] = now_ts
                        _mid_now = float(m.get("mid") or 0.0)
                        if _mid_now > 0:
                            import json as _json
                            _p = (Path(__file__).resolve().parents[3] / "data"
                                  / "dir_block_shadow.jsonl")
                            _p.parent.mkdir(parents=True, exist_ok=True)
                            with open(_p, "a", encoding="utf-8") as _f:
                                _f.write(_json.dumps({
                                    "ts": float(now_ts), "symbol": s,
                                    "blocked_side": str(_merged_block),
                                    "src": ("fusion" if _dir_block_side else "card"),
                                    "mid": _mid_now,
                                    "half_spread": float(m.get("half_spread") or 0.0),
                                    "d": _d_val,
                                }, ensure_ascii=False) + "\n")
                except Exception:
                    pass
            dec, _meta = plan_tick(
                state=st, mid=m["mid"], seg_low=m["seg_low"], seg_high=m["seg_high"],
                seg_taker_sell=m["seg_sell"], seg_taker_buy=m["seg_buy"],
                now_ts=now_ts, params=self.params, limits=self.limits,
                equity=self.equity, fill_notional=self.fill_notional,
                taker_fee_bp=TAKER_FEE_BP, maker_fee_bp=self.maker_fee_bp,
                half_spread=float(m.get("half_spread") or 0.0),
                book_stale=bool(m.get("book_stale")),
                flow_exit_only=bool(m.get("flow_exit_only")),
                sigma_norm=sigma,
                book=shared_book, marks=marks,
                # [F86] 流向毒性闸输入：本桶主动流失衡（决策时可见的已完成桶）
                ofi=float(m.get("ofi") or 0.0),
                # [§82/P5-A] 日亏闸输入：本 UTC 日车道已实现净额（关闭时不参与判定）
                # [h749] 冷却式熔断接管时传 0(状态机在 runner 层已判,避免双重触发)
                day_pnl_usd=(0.0 if _dl_managed else
                            (day_pnl if lane_limits_enforce_enabled() else 0.0)),
                # [h749] 冷却式熔断的暂停覆盖(非空=按此原因暂停;None=正常判据)
                lane_pause_override=_dl_override,
                pending=_pending,
                # 逐笔路径同样用「当时在挂的单」，不能用此刻刚算出来的新价。
                judged_quote=(m.get("judged_quote") if (
                    self.judge_lag_buckets > 0 or bool(m.get("tick_fill"))) else None),
                # [h754 A1] 严格成交审计输入(精确挂单时刻 + 逐笔窗口)
                judge_ts=float(m.get("judge_ts") or 0.0),
                win_lo_ms=int(m.get("win_lo_ms") or 0),
                win_hi_ms=int(m.get("win_hi_ms") or 0),
                # [F347] 学习工件（side_mode="model" 的方向信号来源）
                ai_model=(self.meta or {}).get("ai_model"),
                # [h354] P2 形态输入：60s 成交 VWAP
                vwap60=float(m.get("vwap60") or 0.0),
                # [h752 C1a] 资金费率(加密特化软闸输入,300s 缓存)
                funding_rate=float(self._funding_for(s) or 0.0),
                # [h759] 实时淘汰快路(≤60s 刷新,命中亏损判据的币只减不加)
                decay_blocked=bool(s in getattr(self, "_decay_block", set())),
                # [h397] #9 微价偏离（F272 mp_block_bp 闸输入）
                mp_skew_bp=float(m.get("mp_skew") or 0.0),
                # [h899] 顶档盘口失衡(趋势概率模型特征,透传)
                obi_top=float(m.get("obi_top") or 0.0),
                # [h405] #12 分形态×分币种启停表（lane meta 热采用）
                pattern_matrix=(self.meta or {}).get("pattern_matrix"),
                # [h621] tick 级成交判定：发生率+数量都用逐笔口径（默认 False=旧行为）
                tick_fill=bool(m.get("tick_fill")),
                vol_le_bid=float(m.get("vol_le_bid") or 0.0),
                vol_ge_ask=float(m.get("vol_ge_ask") or 0.0),
                bid_qty=float(m.get("bid_qty") or 0.0),
                ask_qty=float(m.get("ask_qty") or 0.0),
                depth_spread_bp=m.get("depth_spread_bp"),
                block_add_side=_merged_block,
                # [h692] 方向分数分侧倾斜(仅加仓腿;None=不倾斜)
                add_size_mult=_add_size_mult,
                # [h657] Q 速控加仓腿量乘子(影子模式恒 1.0)
                # [h690c] D 不再乘 0(否决版实测把交易掐死);D 只决定"封哪一侧"
                # (见上面 _dir_block_side → block_add_side)。
                q_size_mult=float(
                    (getattr(self, "_q_state", {}) or {}).get(s, {}).get("mult", 1.0)
                    if isinstance((getattr(self, "_q_state", {}) or {}).get(s), dict)
                    else 1.0),
            )
            from backend.services.market_maker.venue_filters import quote_ops
            _ops = quote_ops(_rate_bid, _rate_ask,
                             float(st.quote_bid or 0.0), float(st.quote_ask or 0.0))
            if _ops and not self._venue_ops.allow(_ops, now_ts):
                self._venue_429 += 1
                self._push_event("venue_429", f"订单速率超限(模拟429) {s}", now_ts)
                if not getattr(dec, "fills", None):
                    st.quote_bid, st.quote_ask = _rate_bid, _rate_ask
                    dec.bid, dec.ask = _rate_bid, _rate_ask
                else:
                    st.quote_bid = st.quote_ask = 0.0
                    dec.bid = dec.ask = 0.0
                dec.skip = "venue_429"
                dec.action = "pause"
            # [h688] D 已在 plan_tick **之前**算好并落 self._dir_score(见上),
            # 这里不再重复计算(旧影子块删除:同一 tick 算两遍纯浪费)。
            # ── [h714 阶段0a 2026-10-02] 全闸门影子反事实记录 ──────────────
            # 设计文档 §L2:闸门唯一正当理由 = 被拦腿实测是亏的。此前 h691 只记
            # 方向卡/融合的拦截,趋势/毒性/VPIN/偏斜拦截全部无影子 ⇒ 闸门"自证"
            # 没有数据。这里把 block 族 skip 全部落 `data/gate_block_shadow.jsonl`:
            # {ts, symbol, skip, side(被拦侧), mid, w(拟挂半宽), bid, ask}。
            # 离线结算:按 side 用盘口 markout(5-15s 短窗,防幸存者偏差)算
            # "若没拦这条腿会赚/亏多少"。节流:每 (symbol, skip) 120s 一条。
            _bskip = str(getattr(dec, "skip", "") or "").split("(")[0].strip()
            if _bskip in _SHADOW_BLOCK_SIDES:
                try:
                    _gsh = getattr(self, "_gate_shadow_ts", None)
                    if _gsh is None:
                        _gsh = self._gate_shadow_ts = {}
                    _gk = (s, _bskip)
                    if now_ts - float(_gsh.get(_gk, 0.0) or 0.0) >= 120.0:
                        _gsh[_gk] = now_ts
                        _mid_now2 = float(m.get("mid") or 0.0)
                        if _mid_now2 > 0:
                            import json as _json2
                            _hs2 = float(m.get("half_spread") or 0.0)
                            # m["half_spread"] 是**绝对价差/2**(非 bp)⇒ 与 live 公式
                            # 同口径转 bp(_spread_bp=hs*2/mid*1e4 ⇒ 半宽bp=hs/mid*1e4)
                            _hs2_bp = (_hs2 / _mid_now2 * 1e4) if _mid_now2 > 0 else 0.0
                            _w2 = min(max(1.8 * _hs2_bp,
                                          float(getattr(self.limits, "min_edge_frac", 1.2)
                                                or 1.2) * _hs2_bp),
                                      float(getattr(self.limits, "max_width_bp", 6.0)
                                            or 6.0) / 2.0)
                            _p2 = (Path(__file__).resolve().parents[3] / "data"
                                   / "gate_block_shadow.jsonl")
                            with open(_p2, "a", encoding="utf-8") as _f2:
                                _f2.write(_json2.dumps({
                                    "ts": float(now_ts), "symbol": s,
                                    "skip": _bskip,
                                    "side": _SHADOW_BLOCK_SIDES[_bskip],
                                    "mid": _mid_now2, "w": round(_w2, 4),
                                    "bid": round(_mid_now2 * (1 - _w2 / 1e4), 8),
                                    "ask": round(_mid_now2 * (1 + _w2 / 1e4), 8),
                                }, ensure_ascii=False) + "\n")
                except Exception:
                    pass
            # [F224] 全部决策 σ（含被拦下的）：与 `avg_sigma`（只含挂单决策）并存，
            # 否则"市场 σ_norm 2.6 却显示 0.84"的幸存者偏差会误导观测 ✗。
            self._sigma_all_sum += float(sigma or 0.0)
            self._sigma_all_n += 1
            # ── [F339 2026-09-22] 车道级暂停的**可观测性** ─────────────────────
            # 病根（实测）：`plan_tick` 把 `lane_pause` 放进 `dec` 和 `_meta`，
            # 但**没有任何代码把它聚合进状态文件** ⇒ 车道闸门（日亏/toxic_streak/
            # 车道级 σ）触发时**完全不可见**。
            # 这不是理论风险：本会话实测 status 的 `skip_counts` 里从来没有
            # 带括号的键（`vol_pause(sigma=…)` 的形状）⇒ 车道闸从未被观测到过，
            # 也正因如此 `daily_loss_stop_pct=80%` 在 12 天里 0 次触发这件事
            # **一直没被发现**（尾部零保护）。
            # 同时记 `day_pnl`（闸门的**判定输入**）：只看钳制后的小时均值看不出
            # "离触发还有多远"，而这正是调阈值唯一需要的量。
            _lp = str((_meta or {}).get("lane_pause") or "")
            if _lp:
                self.lane_pause_counts[_lp.split("(")[0]] = (
                    self.lane_pause_counts.get(_lp.split("(")[0], 0) + 1)
                self.lane_pause_last = {"reason": _lp, "ts": float(now_ts),
                                        "symbol": str(s)}
            self.day_pnl_usd = float(day_pnl or 0.0)
            self.day_pnl_limit_usd = (
                -abs(float(self.equity) * float(getattr(self.limits, "daily_loss_stop_pct", 0.0) or 0.0) / 100.0)
                if float(getattr(self.limits, "daily_loss_stop_pct", 0.0) or 0.0) > 0
                else None)
            # [F176] 维护挂单历史：记录本 tick 新挂单（依据标签 = 本 tick 的快照标签 ✓）
            try:
                _bh = self._quote_hist.setdefault(s, [])
                if dec.bid > 0 or dec.ask > 0:
                    _bh.append({"basis": float(m.get("ts_ms") or 0.0),
                                "bid": float(dec.bid or 0.0),
                                "ask": float(dec.ask or 0.0),
                                "mid": float(m.get("mid") or 0.0),
                                "ts": float(st.quote_ts or now_ts)})
                else:
                    # [F230 2026-09-15 **上限失效的根因**] 本 tick 没挂新单（闸门拦住/
                    # 波动暂停等）也必须记一条**零单标记**。否则 `_lagged_quote` 在后续
                    # tick 反复返回**同一张旧挂单**，而水位线不断前进 ⇒ 同一张单被
                    # 每个 tick 重新判定、每个新桶再成交一次 ✗✗ —— 实测 XRP/SOL/ETH
                    # 各自被同一张卖单连成 16 腿（$461 = 5.3× 单币上限），上限形同虚设。
                    # 真实语义：本 tick 旧单已被撤（state.quote_* 已清零），
                    # 其存续期之后的分片**不得**再拿它判成交 ✓。
                    _bh.append({"basis": float(m.get("ts_ms") or 0.0),
                                "bid": 0.0, "ask": 0.0,
                                "mid": float(m.get("mid") or 0.0),
                                "ts": float(now_ts)})
                if len(_bh) > 90:
                    del _bh[:-90]
            except Exception:
                pass
            # [F94] 本币新挂单计入在挂风险（供同 tick 后续币种判定）
            # [F94c] 本币新挂单按「加仓/减仓」计入在挂风险（供同 tick 后续币种判定）
            _u1, _d1, _g1 = pending_contrib(st, marks.get(s, 0.0), self.fill_notional)
            _pending["up"] += _u1
            _pending["down"] += _d1
            _pending["gross"] += _g1
            # [F95] 闸门拦截分布（进程内累计，供前端「链路健康」展示）
            _k = skip_key(dec.skip)
            if _k:
                self.skip_counts[_k] = self.skip_counts.get(_k, 0) + 1
            if dec.bid > 0 and dec.ask > 0:
                self.side_counts["both"] += 1
            elif dec.bid > 0 or dec.ask > 0:
                self.side_counts["one"] += 1
            else:
                self.side_counts["none"] += 1
            # [F98] 挂宽观测：只统计真正挂出去的那一侧（未挂侧宽度为 0，不该摊薄均值）
            # [F232] 分侧计数：bid/ask 各除各侧决策数（不再被对方稀释 ✓）
            if dec.bid > 0:
                self._w_sum["bid"] += float(dec.w_bid_bp or 0.0)
                self._w_n_side["bid"] += 1
            if dec.ask > 0:
                self._w_sum["ask"] += float(dec.w_ask_bp or 0.0)
                self._w_n_side["ask"] += 1
            if dec.bid > 0 or dec.ask > 0:
                self._w_n += 1
                self._sigma_sum += float(sigma or 0.0)
                # [F205] 分支占比与基准宽度：与挂宽均值同口径（只统计真正挂了单的决策 ✓）
                _m = dec.quote_mode or "unknown"
                self._mode_counts[_m] = self._mode_counts.get(_m, 0) + 1
                self._base_sum += float(dec.base_bp or 0.0)
            if self.live_mode:
                self._live_tick_fills(dec, st, m, now_ts)
            else:
                self._record_fills(dec)
            # [F105] 穿越→成交转化率：本侧穿越（区间价触及挂单价，且该侧有主动量——
            # 与 plan_tick 的成交条件**逐条对齐**，否则会把"无成交量的穿越"误记为异常）。
            _seg_low = float(m.get("seg_low") or 0.0)
            _seg_high = float(m.get("seg_high") or 0.0)
            _seg_sell = float(m.get("seg_sell") or 0.0)
            _seg_buy = float(m.get("seg_buy") or 0.0)
            _hit_buy = _qb0 > 0 and _seg_sell > 0 and 0 < _seg_low < _qb0
            _hit_sell = _qa0 > 0 and _seg_buy > 0 and _seg_high > _qa0
            if _hit_buy:
                self.cross_counts["cross_buy"] += 1
            if _hit_sell:
                self.cross_counts["cross_sell"] += 1
            for _f in dec.fills:
                if _f.side == "buy":
                    self.cross_counts["fill_buy"] += 1
                else:
                    self.cross_counts["fill_sell"] += 1
                # [F256 2026-09-16] 成交备注环：每笔成交的判定上下文（快盘窗口自解释）。
                # live↔model 差距 16~46×（时代窗口模型 −$1.6 vs 实盘 −$25.3），
                # 靠事后回放已无法分辨差距来源 ⇒ 下一段快盘让成交自己交代：
                # 触发原因 / 判定 σ / 挂单年龄 / 六维分量 / 是否平仓。
                self.fill_notes.append({
                    "ts": round(now_ts, 1),
                    "symbol": _f.symbol, "side": _f.side,
                    "qty": round(float(_f.qty), 6), "px": round(float(_f.px), 8),
                    "mid": round(float(_f.mid), 8),
                    "spread_bp": round(float(_f.edge_bp or 0.0), 3),
                    "price_bp": round((float(_f.price_usd or 0.0)
                                      / (abs(float(_f.qty or 0.0)) * float(_f.px or 0.0)) * 1e4)
                                     if (_f.qty and _f.px) else 0.0, 3),
                    "flatten": bool(_f.is_flatten),
                    "skip": str(dec.skip or ""), "action": str(dec.action or ""),
                    "sigma_norm": round(float(sigma or 0.0), 3),
                    "quote_age_s": round(now_ts - float(_f.ts), 1),
                })
                if len(self.fill_notes) > 60:
                    self.fill_notes = self.fill_notes[-60:]
            if (_hit_buy or _hit_sell) and not dec.fills:
                if "stale_quote" in str(dec.skip or ""):
                    self.cross_counts["nofill_stale"] += 1
                else:                    # 唯一其它可能：队列份额后的腿量低于最小名义（判定本身已通过）
                    _mid = float(m.get("mid") or 0.0)
                    _side = "buy" if _hit_buy else "sell"
                    _avail = _seg_sell if _side == "buy" else _seg_buy
                    from backend.services.market_maker.replay import QUEUE_SHARE as _QS
                    _q = min(self.fill_notional / _mid if _mid > 0 else 0.0,
                             _avail * _QS)
                    if _q * _mid < 10.0:
                        self.cross_counts["nofill_min_notional"] += 1
                    else:
                        self.cross_counts["nofill_other"] += 1
            # [F107] 判定区间的"空/非空"观测：成交桶表只有 47.5% 的 15s 网格被填充
            # （30s 轮询 + 15s flush + 空桶不落行）⇒ 约一半分片为空。
            # [F152 2026-09-14] **但它不丢成交**：实测空分片中 **90%** 的成交量落在
            # **下一个**桶（迟到桶）⇒ 会被相邻分片正常消费（良性重配对 ✓）；
            # 仅 9.9% 是真的没成交、0.2% 是真数据空洞。
            # ⇒ 这一栏是"数据节奏"读数，**不是**成交机会被吃掉；
            # 也正因如此，**不值得**为它去扩大分片/延迟判定（只会把同一批成交
            # 换个分片归属，不会新增真实成交 ✓）。
            if _qb0 > 0 or _qa0 > 0:
                if _seg_low > 0 or _seg_high > 0:
                    self.cross_counts["win_judged"] += 1
                else:
                    self.cross_counts["win_empty"] += 1
            # [F134] **地板价漏斗（引擎账内口径）**：宽度 ≤ `min_width_reduce_bp` 附近的
            # 减仓腿是最安全、最赚钱的成交（模型里占一半），但外部复算难以判定它是否被
            # 漏判（外部按墙钟配对，而引擎按**桶水位/分片**配对，两者可差 ±15~30s ✗）。
            # 这里用引擎自己的配对记账：地板报价 → 穿越？ → 成交？
            # 判据与 F105 的穿越计数逐条一致（本侧有挂单 + 该侧有主动量 + 区间价触及）。
            _fw = 0.0
            if _qb0 > 0:
                _fw = (_qm0 - _qb0) / _qm0 * 1e4 if _qm0 > 0 else 0.0
            elif _qa0 > 0:
                _fw = (_qa0 - _qm0) / _qm0 * 1e4 if _qm0 > 0 else 0.0
            # [F162 2026-09-14] 阈值必须**跟随配置**：`min_width_reduce_bp` 从 1.0 调到
            # 2.0 之后，固定的 1.5bp 阈值再也匹配不到任何地板腿 ⇒ 四个 floor_* 计数
            # **全部恒为 0**，漏斗静默失明 ✗（实测新配置时代 floor_* 全 0 ✗）。
            # 改成"减仓侧地板 + 0.5bp 余量"，并保留下限常量作为兜底 ✓。
            _floor_bp = max(FLOOR_QUOTE_BP,
                            float(getattr(self.params, "min_width_reduce_bp", 1.0) or 1.0) + 0.5)
            if 0 < _fw <= _floor_bp:
                if _hit_buy or _hit_sell:
                    if dec.fills:
                        self.cross_counts["floor_cross_fill"] += 1
                    else:
                        self.cross_counts["floor_cross_nofill"] += 1
                elif _seg_low > 0 or _seg_high > 0:
                    self.cross_counts["floor_nocross"] += 1
                else:
                    self.cross_counts["floor_win_empty"] += 1
            # [F102] 记入环形缓冲：判定用的挂单 vs 区间高低 + 成交数（审计"该成交却没成交"）
            self.recent_ticks.append({
                "ts": round(now_ts, 1), "s": s,
                "qb": round(_qb0, 8), "qa": round(_qa0, 8), "qm": round(_qm0, 8),
                "lo": round(float(m.get("seg_low") or 0.0), 8),
                "hi": round(float(m.get("seg_high") or 0.0), 8),
                # [F178] 延迟判定诊断：是否找到"那时在挂的单"、窗口上下界、过滤器时间点
                "jq": (1 if (m.get("judged_quote") and float(m["judged_quote"][0]) > 0) else 0),
                "wl": int(m.get("seg_lo_ms") or 0), "wh": int(m.get("seg_hi_ms") or 0),
                "vs": round(float(m.get("seg_sell") or 0.0), 4),
                "vb": round(float(m.get("seg_buy") or 0.0), 4),
                "nf": len(dec.fills), "skip": skip_key(dec.skip),
            })
            if len(self.recent_ticks) > 60:
                self.recent_ticks = self.recent_ticks[-60:]
            self.fills += len(dec.fills)
            self.flattens += sum(1 for f in dec.fills if f.is_flatten)
            d = dec.to_dict()
            d["data_age_sec"] = round(age, 1)
            decisions.append(d)

        # [h883 用户"全是长线,没有日内单"] 换出名单的仓位**在主动流模式下也要清**:
        # 此前 `if not _flow_on` 让 SKY/UNI 这类"已不在宇宙"的仓位永远挂着
        # (SKY 从 14:07 挂到 15:0x 未动)。用户规则:盘口死了 = "没人成交" ⇒ 允许吃单。
        if True:
            for s, st in self.orphan_states().items():
                m_o = market.get(s)
                _fills_o, _skip_o, _mid_o = plan_orphan_exit(
                    state=st, market_row=m_o, now_ts=now_ts, taker_fee_bp=TAKER_FEE_BP)
                if not _fills_o:
                    _d = {"symbol": s, "action": "pause", "orphan": True, "skip": _skip_o}
                    if m_o and m_o.get("ts_ms"):
                        _d["data_age_sec"] = round(
                            float(now_ts) - int(m_o["ts_ms"]) / 1000.0, 1)
                    decisions.append(_d)
                    continue
                dec_o = TickDecision(symbol=s, action="flatten", skip=_skip_o, mid=_mid_o)
                dec_o.exit_path = f"orphan_taker({_skip_o})"
                dec_o.fills.extend(_fills_o)
                st.qty = 0.0
                st.avg_px = st.avg_mid = st.opened_ts = 0.0
                st.last_ts = now_ts
                st.quote_bid = st.quote_ask = st.quote_ts = 0.0
                if self.live_mode:
                    _lb2 = self._live_bridge()
                    if _lb2 is not None and _lb2.enabled:
                        _lb2.market_exit(s)
                else:
                    self._record_fills(dec_o)
                    self.fills += len(dec_o.fills)
                    self.flattens += 1
                d_o = dec_o.to_dict()
                d_o["orphan"] = True
                decisions.append(d_o)
                logger.warning("[F90] 孤儿持仓强制退出 %s: qty=%.8f @ %.8f (mid=%.8f)",
                               s, float(_fills_o[0].qty), float(_fills_o[0].px), _mid_o)
        _pruned = self.prune_flat_orphans()
        if _pruned:
            logger.info("[F90] 清理已平孤儿运行态: %s", ", ".join(_pruned))

        self.last_tick_ts = now_ts
        self.ticks += 1
        _mark("decide_all")
        self.save_states()
        _mark("save_states")
        worst_age = max(data_ages) if data_ages else -1.0
        self._refresh_registry(now_ts, worst_age)
        _mark("registry")
        return {
            "ok": True, "lane_id": self.lane_id, "ts": now_ts,
            "ticks": self.ticks, "fills": self.fills, "flattens": self.flattens,
            "maker_fee_bp": self.maker_fee_bp,
            "phase_ms": _ph,
            "quoting_symbols": sum(1 for d in decisions if d.get("action") == "quote"),
            "stale_symbols": [d["symbol"] for d in decisions
                              if str(d.get("skip") or "").startswith("stale_data")],
            "worst_data_age_sec": None if worst_age < 0 else round(worst_age, 1),
            "inventory": {s: round(st.qty, 8) for s, st in self.states.items()},
            "decisions": decisions,
        }

    def _refresh_registry(self, now_ts: Optional[float] = None,
                          worst_data_age: float = -1.0) -> None:
        """把行情数据年龄与熔断原因写入车道 health（前端熔断矩阵数据源）。"""
        try:
            from backend.services import lane_registry as reg

            breaker = self.last_error or None
            if breaker is None and worst_data_age >= 0 and \
                    MAX_DATA_AGE_SEC > 0 and worst_data_age > MAX_DATA_AGE_SEC:
                breaker = f"stale_data({worst_data_age/60:.1f}min>{MAX_DATA_AGE_SEC/60:.1f}min)"
            reg.update_health(self.lane_id, {
                "data_age_sec": round(worst_data_age, 1) if worst_data_age >= 0 else None,
                "breaker": breaker,
                "note": (f"shadow ticks={self.ticks} fills={self.fills} "
                         f"flattens={self.flattens}"),
            })
        except Exception as e:
            logger.debug("[F60] refresh_registry 失败: %s", e)

    # ── 报告 ──
    def report(self, days: int = 30) -> Dict[str, Any]:
        """影子期达标报告：从 lane_ledger 汇总 + 晋级判定。"""
        from backend.services import lane_ledger, lane_registry

        attr = lane_ledger.attribution(
            days=days, lane_id=self.lane_id,
            # [2026-09-14 统计时代隔离] 影子报告同样按当前时代起点裁剪，
            # 否则「30 天」数字会把旧配置/旧账户时代的账本行混进来。
            since=((getattr(self, "meta", None) or {}).get("stats_since")),
        )
        # attribution 返回 {total, by_lane, by_symbol}；这里取 total 层
        total = attr.get("total") or {}
        per_symbol = {x["symbol"]: {
            "n": x["n"], "net_bp": x["net_bp"], "spread_bp": x["spread_bp"],
            "price_bp": x["price_bp"], "fee_bp": x["fee_bp"],
            "notional": x["notional"], "net_usd": x["net_usd"],
        } for x in (attr.get("by_symbol") or []) if x.get("symbol")}
        net_bp = float(total.get("net_bp") or 0.0)
        fills = int(total.get("n") or 0)
        _since = ((getattr(self, "meta", None) or {}).get("stats_since"))
        series = lane_ledger.daily_series(days=days, lane_id=self.lane_id, since=_since)
        # 逐日序列（美元口径）——晋级判定需要「分折」，影子期用自然日作为一折
        folds = [{"date": row.get("date"), "net_usd": row.get("net_usd"),
                  "n": row.get("n")} for row in series or []]

        # ── 两项此前「无法验证」的晋升指标，现在用真实数据算 ──
        # fill_rate_ratio = 实测成交速率 ÷ 回放建模速率（基线存在车道 meta）
        baseline = (self.meta.get("replay_baseline") or {}).get("fills_per_symbol_hour")
        frr = lane_ledger.fill_rate_ratio(self.lane_id, baseline_per_symbol_hour=baseline,
                                          days=days, since=_since)
        fr_stats = lane_ledger.fill_rate_stats(self.lane_id, days=days, since=_since)
        dd = lane_ledger.max_drawdown_pct(self.lane_id, days=days, equity=self.equity,
                                          since=_since)
        fstats = lane_ledger.flatten_stats(self.lane_id, days=days, since=_since)
        baseline_flat = ((self.meta.get("replay_baseline") or {})
                         .get("flatten_price_bp"))

        rep = {
            "lane_id": self.lane_id, "venue": self.venue, "window_days": days,
            # [F75] flattens 与 fills 必须同窗口同源（此前用进程内计数 vs 30 天账本
            # ——重启即清零，与 fills 窗口不一致，报表平仓占比长期失真）。
            "fills": fills, "flattens": int((fstats or {}).get("flattens") or 0),
            "notional": round(float(total.get("notional") or 0.0), 2),
            # 无成交时六维是「无数据」而非 0——用 0 会把「没跑」读成「跑平了」
            "spread_bp": total.get("spread_bp") if fills else None,
            "price_bp": total.get("price_bp") if fills else None,
            "fee_bp": total.get("fee_bp") if fills else None,
            "net_bp": net_bp if fills else None,
            "net_usd": total.get("net_usd") if fills else None,
            "per_symbol": per_symbol, "daily": folds,
            "maker_fee_bp": self.maker_fee_bp,
            "fill_rate_ratio": frr,
            "fill_rate_stats": fr_stats,
            "replay_baseline_per_symbol_hour": baseline,
            "flatten_stats": fstats,
            "replay_baseline_flatten_price_bp": baseline_flat,
            "max_dd_pct": dd,
            "equity": self.equity,
        }
        try:
            # 影子期只跑了几小时时，日序列不足 4 折 → folds_positive 自然不通过（fail-closed）
            rep["promotion"] = lane_registry.evaluate_promotion({
                "source": "paper_shadow",
                "net_bp": net_bp, "n": fills,
                "folds": [{"net_bp": f["net_usd"], "t": None, "n": f["n"]}
                          for f in folds if f.get("net_usd") is not None],
                "fill_rate_ratio": frr,
                "max_dd_pct": dd,
            })
        except Exception as e:
            rep["promotion"] = {"ready": False, "passed": [], "failed": ["evaluate_error"],
                                "labels": {}, "progress_pct": 0.0, "reason": str(e)}
        return rep


    def _h624_kpi_snapshot(self) -> Dict[str, Any]:
        """[h624] 面板：markout / halt / jump 探针（无重账本 IO）。"""
        from backend.services.market_maker.markout import markout_halts_adds
        wn = int(getattr(self.limits, "markout_window_n", 0) or 0)
        halt_bp = float(getattr(self.limits, "markout_halt_bp", 0.0) or 0.0)
        by_sym: Dict[str, Any] = {}
        halt_syms: List[str] = []
        jump_syms: List[str] = []
        now = float(self.last_tick_ts or time.time())
        for s, st in self.states.items():
            snap = symbol_markout_snapshot(st, min_n=max(1, wn or 10))
            halted = False
            if wn > 0:
                halted = markout_halts_adds(
                    markout_bp=float(snap["m30"]), capture_bp=float(snap["cap"]),
                    n=float(snap["n"]), min_n=wn, halt_thresh_bp=halt_bp)
            snap = dict(snap)
            snap["halt"] = bool(halted)
            by_sym[s] = snap
            if halted:
                halt_syms.append(s)
            if float(getattr(st, "jump_pause_until", 0.0) or 0.0) >= now:
                jump_syms.append(s)
        return {
            "markout_by_symbol": by_sym,
            "markout_halt_symbols": halt_syms,
            "jump_pause_symbols": jump_syms,
            "limits_echo": {
                "markout_horizon_sec": float(
                    getattr(self.limits, "markout_horizon_sec", 0.0) or 0.0),
                "markout_window_n": wn,
                "markout_halt_bp": halt_bp,
                "be_mult": float(getattr(self.limits, "be_mult", 0.0) or 0.0),
                "jump_pause_bp": float(
                    getattr(self.limits, "jump_pause_bp", 0.0) or 0.0),
                "jump_pause_sec": float(
                    getattr(self.limits, "jump_pause_sec", 0.0) or 0.0),
            },
        }

    def status(self) -> Dict[str, Any]:
        import os as _os_status
        _auth = (str(_os_status.getenv("MM_REGISTRY_AUTHORITATIVE", "0")).strip().lower()
                 in ("1", "true", "yes", "on"))
        return {
            "lane_id": self.lane_id, "venue": self.venue, "symbols": self.symbols,
            "equity": self.equity, "account_id": self.account_id,
            "strategy_type": self.strategy_type,
            "maker_fee_bp": self.maker_fee_bp, "ticks": self.ticks,
            "fills": self.fills, "flattens": self.flattens,
            "last_tick_ts": self.last_tick_ts, "last_error": self.last_error,
            # [F327 2026-09-21] 「谁在权威」必须可直接读出。
            # 事故背景：7 个白名单键上 env 赢、注册表是过时值，`k_inv`
            # 注册表 0.5 / 实盘 1.0 —— 光看注册表会得出**相反**的归因。
            # 这一项让"注册表 or env"从推断变成读数。
            "param_authority": "registry" if _auth else "env",
            # [h621 2026-09-29] 成交判定源必须可直接读出（F295 同一原则：运行态要暴露
            # 自己正在用的口径，否则"切了没生效"从 status 里看不出来）。
            "seg_source": (os.getenv("MM_SEG_SOURCE", "tick") or "tick").strip().lower(),
            # [F85] 复利/账户字段（前端「账户总览」卡片数据源）
            "compound_ratio": self.compound_ratio,
            "fill_notional": self.fill_notional,
            # [F295 2026-09-21] **实际生效的开关全量回显**。
            #
            # 事故：调参时连续两次"改了没生效"却看不出来 ——
            #   ① 改 `max_symbol_notional_ratio` 完全无效（该字段 core.py:678 明写
            #      「未接线，历史字段」），真正判定单币的是 `max_net_directional_ratio`；
            #   ② 把杠杆从 0.5 提到 3.0（单腿 $125→$754）后，`symbol_exposure`
            #      拦截数一路涨，**一笔新仓都开不出来**，但从 status 里看不到
            #      "单腿 > 单币上限"这个显然的算术矛盾。
            # 根因是**运行态不暴露自己正在用的参数**：只能靠读注册表 + 猜 env 覆盖，
            # 而 env 覆盖（`apply_env_param_overrides`）恰恰是"注册表与实盘不一致"
            # 的经典来源。这里把 `params` / `limits` / 闸门阈值直接放进 status，
            # 让"实盘到底按什么在跑"可被**直接读出来**，不必再推断。
            "params": {k: getattr(self.params, k, None)
                       for k in getattr(self.params, "__dataclass_fields__", {})},
            "limits": {k: getattr(self.limits, k, None)
                       for k in getattr(self.limits, "__dataclass_fields__", {})},
            "account_equity": self._read_account_equity() if self.account_id else None,
            "states": {s: st.to_dict() for s, st in self.states.items()},
            # [F92] 本进程产能：前端「现在跑多快」——时代速率会被早期故障期稀释
            "process_window_sec": round(
                max(0.0, (self.last_tick_ts or 0.0) - (self._first_tick_ts or 0.0)), 1),
            "fills_per_hour": (
                round(self.fills / ((self.last_tick_ts - self._first_tick_ts) / 3600.0), 2)
                if self._first_tick_ts and self.last_tick_ts > self._first_tick_ts else None),
            "spread_buckets": {s: int(getattr(st, "last_seg_ms", 0) or 0)
                               for s, st in self.states.items()},
            # [F95] 闸门拦截分布 + 双边/单边/未挂 报价比例（实盘可观测性）
            "skip_counts": dict(sorted(self.skip_counts.items(),
                                       key=lambda kv: -kv[1])[:12]),
            # [R201] 闸门探针：**不截断**（`skip_counts` 只留前 12 ⇒ 长尾闸门会"在动却看不见" ✗）
            "gate_probe_counts": dict(sorted(GATE_PROBES.items(), key=lambda kv: -kv[1])),
            # [h624] 心跳 KPI（A1+A6）
            "h624_kpi": self._h624_kpi_snapshot(),
            # [h626] 每 5 分钟的方向卡（谁的哪一边先别加仓）
            "direction_card": dict(getattr(self, "_direction_card", {}) or {}),
            # [h657] Q 速控快照(每币 q/action/mult;影子或减速侧由 q_speed_gate 决定)
            "q_speed": dict(getattr(self, "_q_snapshot", {}) or {}),
            # [h664] 方向分数影子快照(每币 d/mp/ofi;dir_score_fusion=0 只记不作用)
            # [h702] 只暴露现役币的方向分数:换币后旧币条目会残留(前端显示
            # 7 个币而宇宙只有 4 个)——用户实测反馈"对不上"。
            "dir_score": {k: v for k, v in (getattr(self, "_dir_score", {}) or {}).items()
                          if k in set(self.symbols or [])},
            # [h665] 实盘执行桥快照(纸面车道为 None,不进心跳)
            "live_bridge": (self._live.snapshot if self.live_mode and self._live
                            else None),
            # [h667] 模拟按真实交易所条件:订单速率/过滤器跳过(观测+审计)
            "venue_filters": {
                "rate_used_per_min": int(self._venue_ops.used),
                "rate_429": int(self._venue_429),
                "filter_skips": int(self._venue_filter_skips) + int(_VENUE_FILTER_SKIPS[0]),
            },
            # [h680] 自驱动宇宙雷达状态(前端条显示"上次评估/是否自驱")
            "universe_radar": dict(getattr(self, "_radar_state", {}) or {}),
            # [h672] 前端警报事件流(最近 20 条)
            "events": list(getattr(self, "_events", []) or [])[-20:],
            # [F339 2026-09-22] 车道级闸门的可见性（键 = 原因名，如 daily_loss / toxic_streak）
            "lane_pause_counts": dict(sorted(self.lane_pause_counts.items(),
                                             key=lambda kv: -kv[1])),
            "lane_pause_last": dict(self.lane_pause_last or {}),
            # 日亏闸的**判定输入**与其阈值：`day_pnl_usd` 是闸门真正读到的数
            # （受 `stats_since` 时代裁剪影响），`day_pnl_limit_usd` 是触发线。
            # 两者一起看才知道"离触发还有多远"——调阈值唯一的依据。
            "day_pnl_usd": round(float(self.day_pnl_usd or 0.0), 4),
            "day_pnl_limit_usd": (round(float(self.day_pnl_limit_usd), 4)
                                  if self.day_pnl_limit_usd is not None else None),
            "side_counts": dict(self.side_counts),
            # [F98] 挂宽/σ 观测（进程内均值）：与回放同口径对比「实盘挂多宽」
            # [F232] 分侧均值：bid/ask 各除各侧决策数（不再被对方稀释 ✓）
            "avg_width_bp": {
                "bid": round(self._w_sum["bid"] / self._w_n_side["bid"], 3)
                if self._w_n_side["bid"] else None,
                "ask": round(self._w_sum["ask"] / self._w_n_side["ask"], 3)
                if self._w_n_side["ask"] else None},
            "avg_sigma": round(self._sigma_sum / self._w_n, 3) if self._w_n else None,
            # [F224] 全部决策 σ 均值（无幸存者偏差）：观测"市场现在有多波动"用它；
            # `avg_sigma` 只反映"挂出去的单当时有多波动"，两者不相等是正常且必须可见的。
            "avg_sigma_all": (round(self._sigma_all_sum / self._sigma_all_n, 3)
                              if self._sigma_all_n else None),
            "sigma_decisions": {"all": self._sigma_all_n, "quoted": self._w_n},
            "quoted_decisions": self._w_n,
            # [F205] 报价分支观测（冻结档占比 + 基准半宽均值）：核对"参数→行为"是否一致 ✓
            "quote_modes": dict(self._mode_counts),
            "frozen_share": (round(self._mode_counts.get("frozen", 0) / self._w_n, 4)
                             if self._w_n else None),
            "avg_base_bp": round(self._base_sum / self._w_n, 3) if self._w_n else None,
            # [F102] 最近 tick 的成交判定输入（供审计漏判；60 条 ≈ 12 tick × 5 币）
            "recent_ticks": self.recent_ticks[-60:],
            # [F256 2026-09-16] 成交备注环（最近 60 笔成交的判定上下文，快盘自解释）
            "fill_notes": self.fill_notes[-60:],
            # [F105] 穿越→成交转化率累计（长窗口定位残留速率差）
            "cross_counts": dict(self.cross_counts),
            # [F279 2026-09-16] 停机断点补齐观测：断点次数 / 补回点数 / 每币最近一次。
            # 判据：`gap_splices` 应与进程重启次数同量级；若为 0 而实盘仍有
            # 重启后的 vol_pause，说明补齐没生效（或阈值不对）✗。
            "gap_repair": {
                "splices": int(self.gap_splices),
                "points": int(self.gap_splice_points),
                "min_gap_ms": int(MID_SPLICE_MIN_GAP_MS),
                "enabled": bool(getattr(self.limits, "mid_splice_on_gap", False)),
                "last": dict(self.gap_last),
            },
            # [F90 2026-09-14] 孤儿持仓可见性（正常应为空）：宇宙外仍未平掉的仓位。
            # 非空 = 有仓位既未退出也未实现盈亏，前端/巡检必须能立刻看到。
            "orphan_inventory": {s: round(float(st.qty), 8)
                                 for s, st in self.orphan_states().items()},
            # [F294 2026-09-21] 双 worker 事故的**运行时哨兵**。
            #
            # 事故：宇宙收缩到 [ASTER,SOL,XRP] 后，DOGE 仍被正常做市 4.5 分钟
            # （18 笔真实账本成交、峰值 |持仓| $303）。逐条核对 lane_ledger 后确认
            # **当时有两个 worker 进程在同时 tick 同一条车道**（DOGE 与
            # ASTER/XRP/SOL 交错出现在同一 tick 时间戳），各自持有自己的
            # ShadowRunner、互相看不见对方成交，却共用同一份 lane_runtime_state
            # ⇒ 持仓被交叉覆写成「先增后减」的乱序。根因是单实例锁的
            # `old != os.getpid()` 判据（已由 F294 修掉）。
            #
            # 为什么还要这个字段：修复只在**重启后**生效，而事故发生时
            # `status()` 里**没有任何字段**能显示"我之外还有人也在 tick"。
            # 这里把客观证据暴露出来，供前端/巡检直接判读：
            #   · `outside_universe_positions` 非空且持续 ⇒ 要么孤儿退出没生效，
            #     要么有人在用别的宇宙 tick 这条车道；
            #   · `universe` 与心跳里的 `states` 键集合不一致 ⇒ 报价集合与宇宙分叉。
            "universe": list(self.symbols),
            "universe_n": len(self.symbols),
            # [F296 2026-09-21] 超时出库改走被动后，被挡下的 taker 次数（按币）。
            # 与 `flattens` 一起读：
            #   · 该值增长而 flattens 不增长 ⇒ 出库确实改走 maker 了 ✓
            #   · 该值增长但某币 qty 长期不归零 ⇒ 有币卡住，需人工看
            "timeout_exit_blocked": {
                s: int(getattr(st, "timeout_exit_blocked", 0) or 0)
                for s, st in self.states.items()
                if int(getattr(st, "timeout_exit_blocked", 0) or 0) > 0
            },
            # [F301] 止盈触发次数（按币）。与 `flattens` 一起读：
            #   · 该值增长而 `skip_counts` 里没有大量 taker ⇒ 止盈在按设计落袋赢家
            #   · 该值增长过快（接近成交笔数）⇒ 阈值定得太低，等于全 taker 出库，必亏
            "take_profit_hits": {
                s: int(getattr(st, "take_profit_hits", 0) or 0)
                for s, st in self.states.items()
                if int(getattr(st, "take_profit_hits", 0) or 0) > 0
            },
            "outside_universe_positions": {
                s: round(float(st.qty), 8)
                for s, st in self.states.items()
                if s not in self._symbol_set and abs(float(st.qty or 0.0)) > 1e-12
            },
            "as_of": _now_iso(),
        }

    def archive_report(self, days: int = 30) -> bool:
        """把影子期报告落库（`lane_shadow_report`），形成可追溯的达标证据链。"""
        ensure_table()
        rep = self.report(days=days)
        try:
            import json

            from sqlalchemy import text

            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    db.execute(text(
                        "INSERT INTO lane_shadow_report (lane_id, window_days, fills,"
                        " flattens, notional, spread_bp, price_bp, fee_bp, net_bp,"
                        " net_usd, per_symbol, promotion, note) VALUES"
                        " (:l, :w, :f, :fl, :n, :sp, :pr, :fe, :nb, :nu,"
                        " CAST(:ps AS JSONB), CAST(:pm AS JSONB), :note)"
                    ), {
                        "l": self.lane_id, "w": int(days),
                        "f": int(rep.get("fills") or 0),
                        "fl": int(rep.get("flattens") or 0),
                        "n": float(rep.get("notional") or 0.0),
                        "sp": rep.get("spread_bp"), "pr": rep.get("price_bp"),
                        "fe": rep.get("fee_bp"), "nb": rep.get("net_bp"),
                        "nu": rep.get("net_usd"),
                        "ps": json.dumps(rep.get("per_symbol") or {}, ensure_ascii=False,
                                         default=str),
                        "pm": json.dumps(rep.get("promotion") or {}, ensure_ascii=False,
                                         default=str),
                        "note": f"maker_fee_bp={self.maker_fee_bp}; ticks={self.ticks}",
                    })
                    db.commit()
            return True
        except Exception as e:
            self.last_error = f"archive_report: {e}"
            logger.warning("[F60] archive_report 失败: %s", e)
            return False


# ═══════════════════════ 调度接线 ═══════════════════════

_SHADOW_RUNNERS: Dict[str, ShadowRunner] = {}
_SHADOW_LOCK = None


def get_runner(lane_id: str = DEFAULT_LANE_ID) -> Optional[ShadowRunner]:
    """取（或建）车道影子期驱动器；运行态从 DB 恢复。"""
    global _SHADOW_LOCK
    if _SHADOW_LOCK is None:
        import threading
        _SHADOW_LOCK = threading.Lock()
    with _SHADOW_LOCK:
        if lane_id in _SHADOW_RUNNERS:
            return _SHADOW_RUNNERS[lane_id]
        try:
            from backend.services import lane_registry as reg

            lane = reg.get_lane(lane_id)
            if not lane:
                return None
            meta = lane.get("meta") or {}
            stored = meta.get("params") or {}
            from backend.services.market_maker.core import LaneRiskLimits, QuoteParams

            params = QuoteParams(**{k: v for k, v in stored.items()
                                    if k in QuoteParams.__dataclass_fields__})
            # [F280] env 显式优先（见 apply_env_param_overrides 的说明）
            apply_env_param_overrides(params)
            limits = LaneRiskLimits(**{k: v for k, v in stored.items()
                                       if k in LaneRiskLimits.__dataclass_fields__})
            # [F77 2026-09-12] 币种宇宙由注册表 meta.symbols 控制。
            # 此前 get_runner 不传 symbols ⇒ 永远跑 DEFAULT_SYMBOLS（6 币），
            # 组合回放证实 6 币共享账本下 alt 币全部负边际，只有 BTC（及
            # 部分 ETH 组合）为正 ⇒ 币种精选无法上线。现在读注册表。
            #
            # [F249 2026-09-20] 空宇宙 ⇒ **不建 runner**（fail-closed）。
            # 此前回退到 DEFAULT_SYMBOLS（含已停采的 ZEC/TAO）⇒ 注册表未就绪时
            # 车道会去挂僵尸币，且这些状态被写回 DB 后成为"清不掉的幽灵"。
            # 空转是可观测、可告警的；挂僵尸币是静默的错误交易。
            _symbols = [str(s) for s in (meta.get("symbols") or []) if str(s)]
            if not _symbols:
                logger.warning("[F249] 车道 %s 的 meta.symbols 为空 ⇒ 不建 runner"
                               "（fail-closed，避免回退到过时的默认宇宙）", lane_id)
                return None
            _fn = float(stored.get("fill_notional") or FILL_NOTIONAL)
            r = ShadowRunner(
                lane_id=lane_id, venue=str(meta.get("venue") or DEFAULT_VENUE),
                equity=float(meta.get("shadow_equity") or 5000.0),
                account_id=meta.get("paper_account_id"),
                strategy_type=str(meta.get("strategy_type") or "MM"),
                params=params, limits=limits, symbols=_symbols,
                fill_notional=_fn,
            )
            r.meta = meta          # 供 report() 读取回放基线等
            r.load_states()
            # [F109] 冷启动补齐滑动窗口（趋势/波动/冻结闸的输入），否则重启后头一小时
            # 这些闸门用着"空窗口"运行，挂宽档位与回放分叉。
            try:
                _keep = int(getattr(params, "frozen_lookback", 240) or 240)
                r.backfill_mid_hist(keep=max(60, _keep))
            except Exception as _e:  # pragma: no cover
                logger.warning("[F109] backfill 调用失败(忽略): %s", _e)
            # [F71b] 用**回放窗口**的已实现波动做高波动基准（自适应当前盘会失去意义）
            vol_base = (meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {}
            # [F79 2026-09-12] 注册表始终权威：此前只在持久化基线 ≤0 时播种 ⇒
            # 基线在注册表更新（重锚回放窗口）后永远不会生效——持久化副本粘住旧值，
            # 实盘 sigma 口径与验证回放漂移。现在每次 runner 重建都从注册表覆写。
            for sym, st in r.states.items():
                if vol_base.get(sym):
                    st.vol_baseline_bp = float(vol_base[sym])
                else:
                    # [F96] 注册表缺该币 ⇒ 实盘会沿用持久化的陈旧基准，σ 口径与回放
                    # 漂移（实测低 15~21%，k_vol>0 时直接改变挂宽）。显式告警，
                    # 修复：`python scripts/mm_anchor_vol_baseline.py`。
                    logger.warning(
                        "[F96] 币 %s 缺少注册表波动基准（replay_baseline."
                        "vol_baseline_bp）⇒ σ 口径可能与回放漂移；"
                        "请运行 scripts/mm_anchor_vol_baseline.py", sym)
            _SHADOW_RUNNERS[lane_id] = r
            return r
        except Exception as e:
            logger.warning("[F60] get_runner(%s) 失败: %s", lane_id, e)
            return None


# ═══════════ [F253 2026-09-20] 跨进程运行态快照（看板的真值源） ═══════════

_SNAPSHOT_CACHE: Dict[str, Any] = {"mtime_ns": None, "data": None}


def read_status_snapshot(lane_id: str = DEFAULT_LANE_ID) -> Optional[Dict[str, Any]]:
    """读 worker 写出的运行态心跳 `logs/mm_lane_status.json`。

    ## 为什么需要这个函数（现场事故，用户可见）

    用户反馈：「持仓（实时）数据没有任何变化」+「成交记录和持仓（实时）对不上，
    两边不同步」。

    根因是**跨进程的陈旧内存态**：

      · 线上 `MM_LANE_TICKER=external` ⇒ 真正每 15s 推进并更新运行态的是
        `scripts/mm_lane_worker.py` 那个**独立进程**；
      · HTTP 进程里的 `get_runner()` 返回的是 `_SHADOW_RUNNERS` 缓存的实例，
        它的 `self.states` **只在实例创建时**从 DB 恢复，此后该进程从不 tick
        ⇒ 内存态永远停在 HTTP 进程启动那一刻。
      · `lane_runtime_state` 表也不可信：实测 10 个币 `qty` 全 0、`quote_mid` 全 None。

    实测症状：连续 5s 轮询 `/api/hft/board`，XRP 的 `qty / avg_mid / quote_mid`
    一字不动（只有 `hold_ms` 在涨，因为它是 `now()` 现算），而**同一个响应里**的
    深度梯来自 `asterdex_depth_snapshots`、2s 真刷新 ⇒ 一张卡片自相矛盾。

    worker 每 tick 原子重写这个 JSON（实测 15s 刷新、ticks 98→99 递增），
    所以它才是准实时的跨进程真值源。

    ## 读法

    按 `mtime_ns` 做缓存：文件没变就直接返回上次解析结果（避免每 2s 一次 JSON 解析）；
    文件变了才重读。读取失败一律返回 `None`（**绝不抛**）——看板宁可少一块数据，
    也不能因为心跳文件正在被写而整个 500。

    返回值里额外附 `_snapshot_age_ms`，供前端如实标注"运行态有多旧"，
    而不是假装它是实时的。
    """
    import json as _json

    path = Path(__file__).resolve().parents[3] / "logs" / "mm_lane_status.json"
    try:
        st = path.stat()
    except OSError:
        return None
    if _SNAPSHOT_CACHE.get("mtime_ns") == st.st_mtime_ns and _SNAPSHOT_CACHE.get("data"):
        return _SNAPSHOT_CACHE["data"]
    try:
        raw = path.read_text(encoding="utf-8")
        data = _json.loads(raw)
    except Exception:
        # 正在被写（读到半截/空文件）⇒ 返回上一次的好数据，而不是 None
        return _SNAPSHOT_CACHE.get("data")
    if not isinstance(data, dict):
        return None
    try:
        import time as _time

        data["_snapshot_age_ms"] = max(0, int((_time.time() - st.st_mtime) * 1000))
    except Exception:
        data["_snapshot_age_ms"] = None
    data["_snapshot_lane"] = lane_id
    _SNAPSHOT_CACHE["mtime_ns"] = st.st_mtime_ns
    _SNAPSHOT_CACHE["data"] = data
    return data


def shadow_tick_task(lane_id: str = DEFAULT_LANE_ID) -> Dict[str, Any]:
    """调度器任务：车道为 paper + active 时才推进影子期。

    默认 `status="stopped"`，因此**必须显式启动车道**才会开始跑——
    避免部署后自动下单。
    """
    try:
        from backend.services import lane_registry as reg

        lane = reg.get_lane(lane_id)
        if not lane:
            return {"ok": False, "reason": f"车道不存在: {lane_id}"}
        # [h665] live 车道同样由本任务驱动(独立 worker 进程,MM_LANE_WORKER_LANE
        # 指向 live 车道;runner 内部走实盘执行桥,无 Key/未激活 = no-op)。
        if lane.get("mode") not in ("paper", "live"):
            return {"ok": False, "reason": f"mode={lane.get('mode')} 非 paper/live，不跑"}
        health = lane.get("health") or {}
        if health.get("drill"):
            # 组合级熔断演练：真的停报价（由 /api/trading/risk/drill 写入）
            return {"ok": False, "reason": f"drill: {health.get('drill_reason') or '演练中'}"}
        if lane.get("status") != "active":
            return {"ok": False, "reason": f"status={lane.get('status')}，未启动"}
        runner = get_runner(lane_id)
        if runner is None:
            return {"ok": False, "reason": "runner 初始化失败"}
        res = runner.tick()
        if not res.get("ok"):
            logger.warning("[F60] shadow tick 失败 lane=%s: %s", lane_id, res.get("reason"))
        return res
    except Exception as e:
        logger.warning("[F60] shadow_tick_task 异常: %s", e)
        return {"ok": False, "reason": str(e)}


def shadow_archive_task(lane_id: str = DEFAULT_LANE_ID, days: int = 30) -> Dict[str, Any]:
    """每日归档影子期报告（无论车道是否在跑都归档一次快照）。"""
    runner = get_runner(lane_id)
    if runner is None:
        return {"ok": False, "reason": "runner 初始化失败"}
    ok = runner.archive_report(days=days)
    return {"ok": ok, "lane_id": lane_id, "days": days}


def register_shadow_task(
    *,
    lane_id: str = DEFAULT_LANE_ID,
    interval_sec: Optional[int] = None,
) -> bool:
    """把影子期 tick + 每日归档注册到全局调度器（由 main.py 启动时调用）。

    `MM_SHADOW_ENABLED=0` 可整体关闭（默认开启注册，但车道未启动时不会下单）。
    """
    if os.getenv("MM_SHADOW_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        logger.info("[F60] MM_SHADOW_ENABLED=0，跳过影子期调度注册")
        return False
    # [F283 2026-09-16] 外部 ticker 模式：把车道 tick 交给**独立 worker 进程**
    # （`scripts/mm_lane_worker.py`），进程内只保留归档/自进化等 cron 任务。
    # 动机（实测）：宿主侧有一个 ~5 分钟周期的启动器反复重启 API 后端
    # （后端寿命 4~7 分钟），车道每重启就丢一段 tick + 站开一个波动窗口 ⇒
    # 测量连续性无法成立。行情数据本身由数据中心**独立进程**持续写入（后端停机
    # 期间未断流 ✓）⇒ 把 tick 移出 API 进程即可让"链路无断点"成立。
    # 开关：env `MM_LANE_TICKER=external`（默认 inprocess = 旧行为逐字一致，可回退）。
    _ticker = (os.getenv("MM_LANE_TICKER", "inprocess") or "inprocess").strip().lower()
    _external = _ticker in ("external", "worker", "out-of-process")
    interval = int(interval_sec or os.getenv("MM_SHADOW_INTERVAL_SEC", "15"))
    try:
        from backend.services.scheduler import task_scheduler

        task_scheduler.start()
        # 注意：add_interval_task 的签名是
        # (task_func, interval_seconds, task_id, max_instances, next_run_time, *args, **kwargs)，
        # 额外参数只能作为 **kwargs 传（传 args=... 会被 apscheduler 当成非法关键字）。
        if _external:
            logger.info("[F283] MM_LANE_TICKER=external ⇒ 进程内**不**注册车道 tick，"
                        "由 scripts/mm_lane_worker.py 独立进程承担（防双 tick ✓）")
        else:
            task_scheduler.add_interval_task(
                shadow_tick_task,
                interval,
                f"mm_shadow_tick_{lane_id}",
                1,
                None,
                lane_id=lane_id,
            )
        # 每日 00:10 归档一次报告（错开 00:00 的日切任务）
        task_scheduler.add_cron_task(
            shadow_archive_task,
            f"mm_shadow_archive_{lane_id}",
            1,
            hour=0, minute=10,
            lane_id=lane_id, days=30,
        )
        logger.info("[F60] 影子期调度已注册: %s 每 %ss + 每日 00:10 归档", lane_id, interval)
        # [F88 2026-09-14] 自进化闭环：每日 03:30 一轮（先回滚检查，再有界走查搜索）。
        # 默认只出「提案」（MM_AUTO_EVOLVE=1 才允许自动改注册表），日志落
        # data/mm_evolution_journal.jsonl；见 market_maker/evolution.py。
        try:
            from backend.services.market_maker import evolution as _mm_evo

            task_scheduler.add_cron_task(
                _mm_evo.evolution_task,
                f"mm_evolution_{lane_id}",
                1,
                hour=3, minute=30,
                lane_id=lane_id,
            )
            logger.info("[F88] 做市自进化调度已注册: %s 每日 03:30（apply=%s）",
                        lane_id, _mm_evo.evolve_enabled())
        except Exception as _e:
            logger.warning("[F88] 自进化调度注册失败: %s", _e)
        return True
    except Exception as e:
        logger.warning("[F60] 影子期调度注册失败: %s", e)
        return False

