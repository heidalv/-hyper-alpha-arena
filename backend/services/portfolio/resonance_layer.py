"""
M8 周期共振层 PRL（设计骨架）

对应《短期因子策略全链路详细技术设计.md》§7。
本模块定义信号总线、共振评分与组合管理接口；默认 PRL_ENABLED=false，
此时 evaluate/allocate 为直通（与现状行为一致），不接任何执行链路。

纯函数（可直接单测）：
- score_per_signal : 单信号贡献分
- resonance_score  : 聚合共振分
- resolve_verdict  : aligned/neutral/conflict/no_data
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Any, Deque, Dict, List, Optional, Tuple

PRL_ENABLED = os.getenv("PRL_ENABLED", "false").lower() in ("1", "true", "yes", "on")

PRL_W_SHORT = float(os.getenv("PRL_W_SHORT", "0.35"))
PRL_W_MID = float(os.getenv("PRL_W_MID", "0.40"))
PRL_W_LONG = float(os.getenv("PRL_W_LONG", "0.25"))

PRL_ALIGN_OPEN_MIN = float(os.getenv("PRL_ALIGN_OPEN_MIN", "0.30"))
PRL_ALIGN_BOOST_MIN = float(os.getenv("PRL_ALIGN_BOOST_MIN", "0.15"))
PRL_CONFLICT_BLOCK = os.getenv("PRL_CONFLICT_BLOCK", "true").lower() in (
    "1", "true", "yes", "on",
)

PRL_BUDGET_SHORT = float(os.getenv("PRL_BUDGET_SHORT", "0.30"))
PRL_BUDGET_MID = float(os.getenv("PRL_BUDGET_MID", "0.40"))
PRL_BUDGET_LONG = float(os.getenv("PRL_BUDGET_LONG", "0.30"))

# 各周期信号有效窗口（秒）
TIER_WINDOW_SEC = {"short": 600, "mid": 1800, "long": 3600}
TIER_WEIGHTS = {"short": PRL_W_SHORT, "mid": PRL_W_MID, "long": PRL_W_LONG}
TIER_BUDGETS = {"short": PRL_BUDGET_SHORT, "mid": PRL_BUDGET_MID, "long": PRL_BUDGET_LONG}


@dataclass
class PeriodSignal:
    """周期信号总线消息（设计文档 §7.1）。"""
    symbol: str
    tier: str                       # short/mid/long
    direction: str                  # long/short/neutral
    confidence: float = 0.0         # 0~100
    ts: float = 0.0
    horizon_min: int = 0
    source: str = ""

    def __post_init__(self):
        self.symbol = str(self.symbol or "").upper()
        self.tier = str(self.tier or "mid").lower()
        self.direction = str(self.direction or "neutral").lower()
        if self.ts <= 0:
            self.ts = time.time()
        try:
            self.confidence = min(100.0, max(0.0, float(self.confidence)))
        except (TypeError, ValueError):
            self.confidence = 0.0

    def direction_sign(self) -> float:
        if self.direction == "long":
            return 1.0
        if self.direction == "short":
            return -1.0
        return 0.0


def score_per_signal(sig: PeriodSignal, weights: Optional[Dict[str, float]] = None) -> float:
    """单信号贡献 = 方向 × 置信 × 周期权重。"""
    w = (weights or TIER_WEIGHTS).get(sig.tier, 0.0)
    return sig.direction_sign() * (sig.confidence / 100.0) * w


def resonance_score(
    signals: List[PeriodSignal],
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """聚合共振分 = Σ 单信号贡献。"""
    return round(sum(score_per_signal(s, weights) for s in signals), 6)


def resolve_verdict(score: float, n_tiers_with_data: int) -> str:
    """按共振分与数据覆盖判定：aligned/neutral/conflict/no_data。"""
    if n_tiers_with_data == 0:
        return "no_data"
    if score >= PRL_ALIGN_OPEN_MIN:
        return "aligned"
    if score <= -PRL_ALIGN_OPEN_MIN:
        return "conflict"
    return "neutral"


class ResonanceLayer:
    """周期共振层（骨架）。默认关闭时 evaluate/allocate 直通。"""

    _instance: Optional["ResonanceLayer"] = None
    _inst_lock = Lock()

    def __init__(self):
        self._ring: Deque[PeriodSignal] = deque(maxlen=4096)
        self._lock = Lock()

    @classmethod
    def get_instance(cls) -> "ResonanceLayer":
        if cls._instance is None:
            with cls._inst_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------

    def publish(self, sig: PeriodSignal) -> None:
        """信号总线写入（挂接点：scalp_loop / MLTO / MasterController 产出处）。"""
        if not PRL_ENABLED:
            return
        with self._lock:
            self._ring.append(sig)

    def recent_signals(self, symbol: str, now: Optional[float] = None) -> List[PeriodSignal]:
        now = now if now is not None else time.time()
        with self._lock:
            out = [
                s for s in self._ring
                if s.symbol == symbol.upper()
                and now - s.ts <= TIER_WINDOW_SEC.get(s.tier, 600)
            ]
        return out

    def resonance(self, symbol: str) -> Dict[str, Any]:
        """返回 {score, per_tier, verdict}。"""
        sigs = self.recent_signals(symbol)
        per_tier: Dict[str, Dict[str, Any]] = {}
        for s in sigs:
            per_tier[s.tier] = {"direction": s.direction, "confidence": s.confidence}
        score = resonance_score(sigs)
        verdict = resolve_verdict(score, len(per_tier))
        return {
            "score": score,
            "per_tier": per_tier,
            "verdict": verdict,
            "enabled": PRL_ENABLED,
        }

    def evaluate(self, proposal: Any) -> Any:
        """执行前修正 TradeProposal（position_pct/hold）。
        关闭时原样返回（直通）。"""
        if not PRL_ENABLED:
            return proposal
        # TODO(M8): 启用后按共振评分修正 position_pct / hold_hours，
        # 并在 proposal.meta 写入 resonance 块。
        return proposal

    def allocate(
        self,
        symbol: str,
        tier: str,
        requested_margin_usd: float,
        equity: float,
        open_positions: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[float, str]:
        """按周期预算分配保证金；关闭时原样放行。
        返回 (allocated_usd, reason)。"""
        if not PRL_ENABLED:
            return float(requested_margin_usd), ""
        budget = TIER_BUDGETS.get(tier, 0.0)
        used = sum(float(p.get("margin", 0) or 0) for p in (open_positions or []))
        cap = budget * float(equity or 0)
        if used + float(requested_margin_usd) > cap:
            return 0.0, "prl_budget_exceeded"
        return float(requested_margin_usd), ""

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": PRL_ENABLED,
            "ring_size": len(self._ring),
            "weights": dict(TIER_WEIGHTS),
            "budgets": dict(TIER_BUDGETS),
            "align_open_min": PRL_ALIGN_OPEN_MIN,
            "conflict_block": PRL_CONFLICT_BLOCK,
        }


resonance_layer = ResonanceLayer.get_instance()
