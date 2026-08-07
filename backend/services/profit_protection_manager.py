"""
利润保护管理器 v2 — 基于 TP 进度的分层利润保护

所有阈值基于**持仓保证金**的百分比，自动适配任何账户和仓位大小：
- $100 账户、$5 保证金的仓位 → 阈值自动缩小
- $100K 账户、$10K 保证金的仓位 → 阈值自动放大

核心流程：
1. 利润不到 TP 距离 30% → 不做任何保护，让利润跑
2. 利润 ≥ TP×30% → 保本止损（SL 推到入场价附近）
3. 利润 ≥ TP×50% → 分批锁利 25%
4. 利润 ≥ TP×70% → 再锁利 25%
5. 利润 ≥ TP×90% → 再锁利 25% + 紧追踪
6. 回撤保护：峰值利润回撤超 40% → 全平

重要：当 AI 没有设 TP 目标时，保护系统采用极保守策略（30% 价格波动 = 100% TP），
不会对短期波动做出反应。
"""

import logging
from dataclasses import dataclass
from typing import Optional

from backend.config.settings import (
    PROFIT_PROTECTION_DRAWDOWN,
    PROFIT_PROTECTION_EMERGENCY,
)

logger = logging.getLogger(__name__)

_EPSILON = 0.005  # 浮点容差

# ── 基于【持仓保证金】百分比的激活阈值 ──
# 所有仓位自动适配：小仓 $5 保证金、大仓 $10K 保证金都适用

# 回撤保护激活：浮盈达到保证金的 100% 才启动
# 旧值 0.50 → $10 保证金仅 $5 浮盈就激活，导致利润还没跑到 TP 就被保护平仓
_DRAWDOWN_ACTIVATION_MARGIN_PCT = 1.00

# 保本止损激活：浮盈达到保证金的 50%
# [2026-07-30 crypto-native] 35% 太低，5m crypto 微利就推保本→SL 太紧→被波动击穿
# → breakeven_tp 100% 微利出场。提升到 50% 延迟激活。
_BREAKEVEN_ACTIVATION_MARGIN_PCT = 0.50

# 分批锁利最低浮盈：达到保证金的 X% 才执行
_LOCK_MIN_PROFIT_MARGIN_PCT = {
    1: 0.25,   # L1: 浮盈 ≥ 保证金 × 25%
    2: 0.40,   # L2: 浮盈 ≥ 保证金 × 40%
}

# 分批锁利规则
_GRADUATED_LOCK_RULES = [
    {"tp_progress": 0.55, "close_pct": 0.35, "sl_to_progress": 0.35, "reason": "profit_lock_1", "level": 1},
    {"tp_progress": 0.85, "close_pct": 0.35, "sl_to_progress": 0.65, "reason": "profit_lock_2", "level": 2},
]

# 保本 SL 缓冲
# [2026-07-30 crypto-native] 0.3% 是传统股市参数，加密 5m 噪音带 0.5-1%，
# 0.3% buffer 被正常波动轻松击穿→breakeven_tp 微利出场。提升到 0.8%。
_BREAKEVEN_BUFFER = 0.008

# ── 无 TP 时的进度回退 ──
# 当 AI 没设 TP 目标时，假设 X% 价格波动 = 100% TP 进度
# 关键：这个值必须足够大，否则短期波动会被误判为高进度触发保护
# 旧值 0.10 = 10% 价格波动 = 100% TP，导致 3% 波动就被当成 30% 进度
# 新值 0.30 = 30% 价格波动 = 100% TP，需要 9% 波动才到 30% 进度
_NO_TP_PROGRESS_BASELINE = 0.40


@dataclass
class ProtectionResult:
    """保护动作结果"""
    action: str  # "none" | "breakeven" | "partial_close" | "close"
    sl_price: Optional[float] = None
    close_pct: Optional[float] = None
    reason: str = ""


class ProfitProtectionManager:
    """利润保护管理器 — 所有阈值基于保证金百分比，自动适配仓位大小"""

    def calc_profit_progress(
        self, entry: float, current: float, tp: Optional[float], side: str
    ) -> float:
        """计算当前利润相对于 TP 目标的进度 (0.0 ~ 1.0+)

        当 AI 设了 TP 时：progress = 实际利润% / TP距离%
        当 AI 没设 TP 时：progress = 实际利润% / 30%（极保守，避免短期波动误触发）
        """
        if entry <= 0:
            return 0.0

        if side == "long":
            profit_pct = (current - entry) / entry
        else:
            profit_pct = (entry - current) / entry

        if profit_pct <= 0:
            return 0.0

        if tp and tp > 0:
            if side == "long":
                tp_distance = (tp - entry) / entry
            else:
                tp_distance = (entry - tp) / entry

            if tp_distance > 0.001:
                result = profit_pct / tp_distance
                for threshold in [0.30, 0.50, 0.70, 0.90, 1.00]:
                    if abs(result - threshold) < _EPSILON:
                        result = threshold
                return max(0.0, result)

        # 无 TP 时采用极保守回退：30% 价格波动 = 100% TP
        # 需要 9% 价格波动才到 30% 进度（保本止损区）
        # 需要 15% 价格波动才到 50% 进度（分批锁利区）
        return profit_pct / _NO_TP_PROGRESS_BASELINE

    def calc_current_profit_usd(
        self, entry: float, current: float, size: float, side: str
    ) -> float:
        """计算当前浮盈美元值"""
        if side == "long":
            return (current - entry) * size
        else:
            return (entry - current) * size

    def check_drawdown_protection(
        self, peak_profit: float, current_profit: float, margin: float,
    ) -> str:
        """检查回撤保护 — 激活阈值 = 保证金 × 50%（向后兼容，mid tier 默认）"""
        return self.check_drawdown_protection_tier(
            peak_profit, current_profit, margin,
            _tier_params={
                "drawdown_activate": _DRAWDOWN_ACTIVATION_MARGIN_PCT,
                "drawdown_emergency": PROFIT_PROTECTION_EMERGENCY,
                "drawdown_protect": PROFIT_PROTECTION_DRAWDOWN,
            }
        )

    def check_drawdown_protection_tier(
        self, peak_profit: float, current_profit: float, margin: float,
        _tier_params: dict = None,
    ) -> str:
        """Tier-aware 回撤保护

        Args:
            _tier_params: 从 TIER_PROTECTION_PARAMS 加载的参数字典
        """
        _tp = _tier_params or {
            "drawdown_activate": _DRAWDOWN_ACTIVATION_MARGIN_PCT,
            "drawdown_emergency": PROFIT_PROTECTION_EMERGENCY,
            "drawdown_protect": PROFIT_PROTECTION_DRAWDOWN,
        }

        if margin <= 0 or peak_profit <= 0:
            return "none"
        if current_profit <= 0:
            return "none"

        activation = margin * _tp["drawdown_activate"]
        if peak_profit < activation:
            return "none"

        drawdown = (peak_profit - current_profit) / peak_profit

        if drawdown >= _tp["drawdown_emergency"]:
            return "emergency"
        if drawdown >= _tp["drawdown_protect"]:
            return "drawdown"

        return "none"

    def _get_breakeven_sl(self, entry: float, side: str) -> float:
        if side == "long":
            return round(entry * (1 + _BREAKEVEN_BUFFER), 6)
        else:
            return round(entry * (1 - _BREAKEVEN_BUFFER), 6)

    def _calc_lock_sl_price(
        self, entry: float, tp: Optional[float], side: str, sl_to_progress: float
    ) -> Optional[float]:
        if not tp or entry <= 0:
            return None
        if side == "long":
            lock_price = entry + (tp - entry) * sl_to_progress
        else:
            lock_price = entry - (entry - tp) * sl_to_progress
        return round(lock_price, 6)

    def get_protection_action(
        self,
        entry: float,
        current: float,
        tp: Optional[float],
        sl: Optional[float],
        side: str,
        size: float,
        peak_profit: float,
        level_reached: int,
        account_equity: float,
        margin: float = 0,
        tier: Optional[str] = None,
    ) -> ProtectionResult:
        """综合计算当前应执行的保护动作

        Args:
            margin: 当前持仓保证金（关键！所有阈值基于此计算）
            tier: 交易周期 tier ("short"/"mid"/"long")，None 时 fallback 到 "mid"
        """
        # ── Tier-aware parameters ──
        from backend.config.settings import TIER_PROTECTION_PARAMS
        _tier = tier or "mid"
        _tp = TIER_PROTECTION_PARAMS.get(_tier, TIER_PROTECTION_PARAMS["mid"])

        progress = self.calc_profit_progress(entry, current, tp, side)
        current_profit = self.calc_current_profit_usd(entry, current, size, side)

        if progress <= 0 or current_profit <= 0:
            return ProtectionResult(action="none")

        # 保证金为 0 时回退用名義值 / 杠杆估算
        if margin <= 0:
            margin = (entry * size) / max(1, account_equity / (entry * size)) if account_equity > 0 else entry * size * 0.1

        # 1. 回撤保护（最高优先级）— tier 感知阈值
        drawdown_status = self.check_drawdown_protection_tier(
            peak_profit, current_profit, margin, _tp
        )
        if drawdown_status == "emergency":
            return ProtectionResult(action="close", reason="emergency_drawdown")
        if drawdown_status == "drawdown":
            return ProtectionResult(action="close", reason="drawdown_protection")

        # 2. 达到 TP → 止盈
        if progress >= 1.0:
            return ProtectionResult(action="close", reason="tp_target")

        # 3. 进度 < breakeven 阈值 → 不保护
        _be_progress = _tp["breakeven_tp_progress"]
        if progress < _be_progress:
            return ProtectionResult(action="none")

        # 4. 进度在 breakeven 区间 → 保本止损
        _lock_first_progress = _tp["lock_tp_progress"][0] if _tp["lock_tp_progress"] else 0.50
        if progress < _lock_first_progress:
            if current_profit >= margin * _BREAKEVEN_ACTIVATION_MARGIN_PCT:
                breakeven_sl = self._get_breakeven_sl(entry, side)
                if self._is_better_sl(breakeven_sl, sl, side):
                    return ProtectionResult(action="breakeven", sl_price=breakeven_sl, reason="breakeven_push")
            return ProtectionResult(action="none")

        # 5. 动态构建 tier 锁利规则
        _tier_lock_rules = []
        for i in range(_tp["lock_stages"]):
            _tier_lock_rules.append({
                "tp_progress":     _tp["lock_tp_progress"][i],
                "close_pct":       _tp["lock_close_pct"][i],
                "sl_to_progress":  _tp["lock_sl_to_progress"][i],
                "reason":          f"profit_lock_{i + 1}",
                "level":           i + 1,
            })
        _tier_lock_margin_pct = _tp["lock_min_margin_pct"]

        # 5a. 分批锁利
        lock_triggered = False
        for rule in _tier_lock_rules:
            rule_level = rule["level"]
            if rule_level <= level_reached:
                continue
            if progress < rule["tp_progress"] - _EPSILON:
                continue

            min_profit = margin * _tier_lock_margin_pct[rule_level - 1]
            if current_profit < min_profit:
                continue

            lock_sl = self._calc_lock_sl_price(entry, tp, side, rule["sl_to_progress"])
            logger.info(
                f"[ProfitProtection] 锁利 L{rule_level} tier={_tier}: "
                f"progress={progress:.0%} profit=${current_profit:.2f} "
                f"margin=${margin:.2f} close={rule['close_pct']:.0%}"
            )
            lock_triggered = True
            return ProtectionResult(
                action="partial_close", sl_price=lock_sl,
                close_pct=rule["close_pct"], reason=rule["reason"],
            )

        # 5b. 进度 ≥ tight_trail_start → 紧追踪
        if progress >= _tp["tight_trail_start"] and progress < 1.0:
            breakeven_sl = self._get_breakeven_sl(entry, side)
            return ProtectionResult(action="breakeven", sl_price=breakeven_sl, reason="tight_trail")

        # 5c. 进度 ≥ 锁利区 但没锁利触发 → 至少保本
        if not lock_triggered and progress >= _lock_first_progress:
            breakeven_sl = self._get_breakeven_sl(entry, side)
            if self._is_better_sl(breakeven_sl, sl, side):
                return ProtectionResult(action="breakeven", sl_price=breakeven_sl, reason="breakeven_fallback")

        return ProtectionResult(action="none")

    @staticmethod
    def _is_better_sl(new_sl: float, current_sl: Optional[float], side: str) -> bool:
        if current_sl is None or current_sl <= 0:
            return True
        if side == "long":
            return new_sl > current_sl
        else:
            return new_sl < current_sl


profit_manager = ProfitProtectionManager()
