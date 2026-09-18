"""
统一离场框架 — 数据类型定义。

所有离场触发源统一提交 ExitRequest，状态机仲裁后输出 ExitDecision。
PositionContext 是状态机做决策所需的持仓上下文快照。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ExitAction(str, Enum):
    CLOSE = "close"           # 全平
    REDUCE = "reduce"          # 部分减仓
    TIGHTEN_SL = "tighten_sl"  # 收紧止损（不平仓）
    HOLD = "hold"              # 持有（不动作）
    DEFER = "defer"            # 延迟（等下一轮仲裁）


class ExitUrgency(str, Enum):
    CRITICAL = "CRITICAL"   # SL/TP/爆仓 → 立即执行
    HIGH = "HIGH"           # 回撤保护/bias 强反向
    NORMAL = "NORMAL"       # 分批 TP/trailing/time_decay
    LOW = "LOW"             # AI reduce/master_running


class ExitSource(str, Enum):
    # 硬事实层（直通）
    STOP_LOSS = "sl"
    TAKE_PROFIT = "tp"
    LIQUIDATION = "liquidation"
    EMERGENCY_DRAWDOWN = "emergency_drawdown"
    # 动态离场层
    TRAILING = "trailing"
    STAGED_TP = "staged_tp"
    TIME_DECAY = "time_decay"
    BIAS_REVERSAL = "bias_reversal"
    NO_PROGRESS = "no_progress"
    PROFIT_DRAWDOWN = "profit_drawdown"
    BREAKEVEN = "breakeven"
    REGIME_FLIP = "regime_flip"
    # AI 决策层
    MASTER_REDUCE = "master_reduce"
    MASTER_CLOSE = "master_close"
    HOLD_REVIEW = "hold_review"
    DEFENSIVE = "defensive"
    # 其他
    MANUAL = "manual"
    LIQ_MAGNET = "liq_magnet"


# 硬事实来源（直通执行，不经保护层/动态层/AI 层门控）
HARD_FACT_SOURCES: frozenset[str] = frozenset({
    ExitSource.STOP_LOSS.value,
    ExitSource.TAKE_PROFIT.value,
    ExitSource.LIQUIDATION.value,
    ExitSource.EMERGENCY_DRAWDOWN.value,
    ExitSource.MANUAL.value,
})

# AI 决策来源（需过最严门控）
AI_SOURCES: frozenset[str] = frozenset({
    ExitSource.MASTER_REDUCE.value,
    ExitSource.MASTER_CLOSE.value,
    ExitSource.HOLD_REVIEW.value,
    ExitSource.DEFENSIVE.value,
})


@dataclass(frozen=True)
class PositionContext:
    """状态机做决策所需的持仓上下文（只读快照）。"""
    position_id: int
    symbol: str
    tier: str               # short / mid / long
    side: str               # long / short
    entry_price: float
    current_price: float
    quantity: float
    leverage: float = 1.0
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    unrealized_pnl_pct: float = 0.0     # 浮盈亏百分比（正=盈）
    peak_pnl_pct: float = 0.0           # 历史最高浮盈百分比
    hold_seconds: int = 0               # 已持仓秒数
    atr_pct: float = 0.0                # 当前 ATR 百分比
    # 趋势状态（用于跨周期协同）
    trend_4h_aligned: bool = True       # 4h EMA 方向是否与持仓一致
    trend_1d_aligned: bool = True       # 1d EMA 方向是否与持仓一致
    # 同品种跨 tier 信息
    same_symbol_positions: list[dict] = field(default_factory=list)
    # ── S2-4 新增（lifecycle 状态追踪，对应 04 综合方案 §3.4）──
    tp_level_reached: int = 0           # 已触发的 TP 档位（0=未触发, 1=TP1, 2=TP2, 3=TP3）
    tp_stages: list[dict] = field(default_factory=list)  # LLM 的 exit_plan.tp_stages
    expected_hold_hours: float = 0.0    # LLM 建议持仓时长（用于 time_stop）
    invalidation_condition: str = ""    # LLM 的论点失效条件（用于 invalidation 退出）
    # 行情快照（可选；短线动态出场 / 行情翻脸用）
    regime: str = ""                    # trend / ranging / extreme / unknown
    funding_rate: float = 0.0
    oi_delta: float = 0.0


@dataclass(frozen=True)
class ExitRequest:
    """所有离场触发源统一提交此结构。"""
    position_id: int
    symbol: str
    tier: str
    source: str                         # ExitSource 的 value
    proposed_action: str                # ExitAction 的 value
    proposed_qty_ratio: float = 1.0     # 1.0=全平, 0.5=减半
    urgency: str = "NORMAL"             # ExitUrgency 的 value
    reason_detail: str = ""
    ts_ns: int = 0


@dataclass
class ExitDecision:
    """状态机仲裁后的最终决策。"""
    position_id: int
    action: str             # ExitAction 的 value
    qty_ratio: float = 1.0
    reason: str = ""
    source: str = ""        # 最终生效的来源
    overridden_sources: list[str] = field(default_factory=list)  # 被覆盖的来源
    new_sl_price: Optional[float] = None   # tighten_sl 时的新 SL 价
    new_tp_price: Optional[float] = None   # 可选的新 TP 价
    ts_ns: int = 0


def make_hold(position_id: int, reason: str = "") -> ExitDecision:
    """便捷构造 hold 决策。"""
    return ExitDecision(position_id=position_id, action=ExitAction.HOLD.value, reason=reason)


# ─────────────────────────── 平仓结果判定 ───────────────────────────

#: 视为「确实成交」的状态词（小写）。live 侧 LiveExecutor、paper 侧引擎各用一套叫法，
#: 这里做并集；**不在集合里的一律算未确认成交**（宁可不记成功，也不虚报）。
CLOSE_OK_STATUSES = frozenset({
    "filled", "closed", "ok", "success", "partial", "partially_filled",
})


def close_result_succeeded(result) -> bool:
    """平仓/减仓调用是否**确认成交**。

    ## 为什么需要统一判定（轮74 修）

    `_close_position_live_aware` 返回的是 **dict**，而 dict 恒为真 ——
    调用方原先的 `bool(res)`（`scalp_position_review.py:235`）与
    `res.get("closed_fully", False)`（`defensive_cycle.py:331`）
    会把 `status="error"` / `"blocked"` / `"rejected"` 也读成平仓成功，
    于是：实盘平仓失败被记成成功事件、冷却闸门挡住诚实重试、PnL 归因被污染。

    判定顺序（从严）：
      1. 显式 `success` 字段（`_close_position_live_aware` 已给出）优先；
      2. 否则看 `status` 是否属于 :data:`CLOSE_OK_STATUSES`；
      3. 两者都缺（老 paper 路径的 dict / None）→ 按「有 pnl 或 closed_fully 字段」
         视为 paper 引擎的成功返回；`None`/`{}` 视为未确认。
    """
    if result is None:
        return False
    if not isinstance(result, dict):
        # 非 dict（对象/真值）：交给真值判断，保持旧路径兼容
        return bool(result)

    if "success" in result:
        return bool(result.get("success"))
    status = str(result.get("status") or "").strip().lower()
    if status:
        return status in CLOSE_OK_STATUSES
    # 老 paper 路径：没有 status 也没有 success，但带回执字段
    return any(k in result for k in ("pnl", "closed_fully", "position_id", "id"))
