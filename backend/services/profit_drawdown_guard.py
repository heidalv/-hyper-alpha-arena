"""
Profit Drawdown Guard — 盈利回撤保护 (D6)

当仓位从峰值利润回撤超过自适应阈值时，自动触发保护动作：
- 轻度回撤：收紧 SL 锁定剩余利润
- 中度回撤：部分减仓 + 收紧 SL
- 重度回撤（翻转为亏损）：全平

阈值按币种波动率 + trade_nature 自适应：
  BTC/ETH (低波): 40% 回撤即触发
  中型山寨 (中波): 50% 回撤触发
  微型币 (高波): 60% 回撤触发
  scalp/intraday 比 swing/trend_follow 更紧

设计原则：
- 无状态：所有峰值数据从 paper_trading_engine._peak_profit_cache 读取
- 只读+动作：不修改仓位参数，只返回保护动作建议
- 与现有 SL/TP/liq 检查正交：在它们之前执行
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 基础阈值：按波动率等级 (允许从峰值回撤的最大比例) ──
#    低值 = 保护更紧，高值 = 给更多空间
# 7日数据：profit_drawdown_partial 57次、小盈频繁落袋；阈值放宽让利润多跑
BASE_DRAWDOWN_THRESHOLD = {
    "low":  0.40,
    "mid":  0.45,
    "high": 0.55,
}

# ── trade_nature 调整：短期策略需要更紧的保护 ──
NATURE_ADJUSTMENT = {
    "scalp":        -0.10,   # 刮头皮：更紧
    "intraday":     -0.05,   # 日内：稍紧
    "swing":         0.00,   # 波段：基准
    "position":     +0.05,   # 长线：略宽
    "trend_follow": +0.10,   # 趋势跟随：最宽
}

# ── 收紧 SL 时的缓冲区（相对于 entry 的百分比偏移）──
#    确保 SL 设在 entry 之上（多）或之下（空），给正常波动留空间
PROFIT_LOCK_BUFFER = {
    "low":  0.008,   # 0.8% — BTC 日波 ~2%
    "mid":  0.015,   # 1.5%
    "high": 0.025,   # 2.5% — 小币波动大，需要更多空间
}

# ── 部分减仓比例 ──
PARTIAL_CLOSE_RATIO = 0.35   # 中度回撤时平掉 35%（原 50% 过于激进）
MIN_PEAK_PROFIT_PCT = 0.03   # 峰值浮盈须达名义 3% 才启用回撤保护


class ProfitDrawdownGuard:
    """
    盈利回撤保护 (D6)

    在每个 tick 评估仓位时调用，检查当前未实现利润是否已
    从峰值大幅回撤，如果是则返回保护动作。

    用法:
        guard = ProfitDrawdownGuard()
        action = guard.evaluate(
            symbol="BTC", side="long", nature="swing",
            entry_price=50000.0, current_price=50500.0,
            peak_profit=1000.0, current_upnl=500.0,
            current_sl=48500.0, position_size=0.1,
        )
        if action:
            # action["type"] in ("tighten_sl", "partial_close", "full_close")
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._action_log: Dict[str, list] = {}  # symbol → recent actions
        logger.info("[ProfitDrawdownGuard] 盈利回撤保护初始化完成")

    # ══════════════════════════════════════════════════════
    #  公开接口
    # ══════════════════════════════════════════════════════

    def evaluate(
        self,
        symbol: str,
        side: str,
        nature: str,
        entry_price: float,
        current_price: float,
        peak_profit: float,          # 该仓位历史最大 uPnL (含 partial)
        current_upnl: float,          # 当前 uPnL
        current_sl: Optional[float],  # 当前止损价
        position_size: float,         # 仓位大小（币数）
        tier: str = "mid",
        atr_pct: Optional[float] = None,  # 手动指定 ATR%，不传则自动获取
    ) -> Optional[Dict[str, Any]]:
        """
        评估是否需要盈利回撤保护。

        Returns:
            None: 无需动作
            Dict: {
                "type": "tighten_sl" | "partial_close" | "full_close",
                "reason": str,
                "new_sl": float | None,       # tighten_sl/partial_close 时的新 SL
                "close_ratio": float | None,   # partial_close 时的平仓比例
                "drawdown_ratio": float,       # 实际回撤比例
                "threshold_used": float,       # 使用的阈值
            }
        """
        # ── 前置条件：必须有过显著利润 (peak > 1% of position value) ──
        position_value = entry_price * position_size
        min_peak = position_value * MIN_PEAK_PROFIT_PCT
        if peak_profit < min_peak:
            return None

        # ── 计算回撤比例 ──
        # dd_ratio: 从峰值跌了多少 (0-1+)
        #   0.0 = 仍在峰值
        #   0.5 = 回撤了 50% 的峰值利润
        #   1.0 = 所有利润归零(回到 entry)
        #   >1.0 = 翻转为亏损
        if peak_profit <= 0:
            return None

        dd_ratio = (peak_profit - current_upnl) / peak_profit

        # 还在创新高，不需要保护
        if dd_ratio <= 0:
            return self._profit_stage_protection(
                symbol, side, entry_price, current_price, current_upnl,
                position_value, nature
            )

        # ── 获取自适应阈值 ──
        threshold = self._get_threshold(symbol, nature, atr_pct)

        # ── 确定动作等级 ──
        action = self._classify_action(dd_ratio, threshold, current_upnl, position_value)

        if action is None:
            return None

        # ── 计算新 SL ──
        new_sl = None
        if action in ("tighten_sl", "partial_close"):
            new_sl = self._compute_profit_lock_sl(
                symbol, side, entry_price, current_price, nature
            )

        result = {
            "type": action,
            "reason": self._build_reason(symbol, dd_ratio, threshold, peak_profit, current_upnl, action),
            "new_sl": new_sl,
            "close_ratio": PARTIAL_CLOSE_RATIO if action == "partial_close" else None,
            "drawdown_ratio": round(dd_ratio, 3),
            "threshold_used": round(threshold, 3),
        }

        self._log_action(symbol, result)
        return result

    # ══════════════════════════════════════════════════════
    #  阈值计算
    # ══════════════════════════════════════════════════════

    def _get_threshold(
        self,
        symbol: str,
        nature: str,
        atr_pct: Optional[float] = None,
    ) -> float:
        """
        计算该币种+策略的自适应回撤阈值。

        阈值 = 波动率基础值 + trade_nature 调整
        最终钳制在 [0.30, 0.70]
        """
        vol_class = self._classify_volatility(symbol, atr_pct)
        base = BASE_DRAWDOWN_THRESHOLD.get(vol_class, 0.50)
        adj = NATURE_ADJUSTMENT.get(nature, 0.0)
        threshold = base + adj
        return max(0.30, min(0.70, threshold))

    def _classify_volatility(
        self,
        symbol: str,
        atr_pct: Optional[float] = None,
    ) -> str:
        """
        分类币种波动率等级。

        优先使用传入的 atr_pct，其次从 UnifiedDataPool 实时获取，
        最后 fallback 到硬编码列表。
        """
        # 1. 手动传入的 ATR%
        if atr_pct is not None:
            if atr_pct < 0.008:
                return "low"
            elif atr_pct > 0.025:
                return "high"
            return "mid"

        # 2. 从 UnifiedDataPool 获取实时 ATR
        try:
            from backend.services.unified_data_pool import UnifiedDataPool
            snap = UnifiedDataPool().get_snapshot(max_age=120)
            if snap and symbol in snap.indicators:
                ind = snap.indicators[symbol]
                atr_val = ind.get("atr_4h", ind.get("atr", 0))
                price = ind.get("last_price", ind.get("close", 0))
                if atr_val > 0 and price > 0:
                    pct = atr_val / price
                    if pct < 0.008:
                        return "low"
                    elif pct > 0.025:
                        return "high"
                    return "mid"
        except Exception:
            pass

        # 3. Fallback: 硬编码列表
        _LOW = {"BTC", "ETH"}
        _HIGH = {"VIRTUAL", "WIF", "PEPE", "DOGE", "TIA", "SEI", "XPL", "ASTER"}
        if symbol.upper() in _LOW:
            return "low"
        if symbol.upper() in _HIGH:
            return "high"
        return "mid"

    # ══════════════════════════════════════════════════════
    #  D7: 盈利分段主动保护 — 不等回撤，盈利达标即锁利
    # ══════════════════════════════════════════════════════
    def _profit_stage_protection(
        self, symbol: str, side: str, entry_price: float,
        current_price: float, upnl: float, position_value: float, nature: str,
    ) -> Optional[Dict[str, Any]]:
        profit_pct = upnl / position_value if position_value > 0 else 0

        if profit_pct > 0.30:
            return {
                "type": "profit_stage_close",
                "reason": f"{symbol} 浮盈{profit_pct:.0%}>30% → 止盈60%锁利",
                "new_sl": self._compute_profit_lock_sl(symbol, side, entry_price, current_price, nature),
                "close_ratio": 0.60,
                "drawdown_ratio": 0.0,
                "threshold_used": 0.30,
            }
        if profit_pct > 0.15:
            return {
                "type": "breakeven_sl",
                "reason": f"{symbol} 浮盈{profit_pct:.0%}>15% → 止损移至成本价保本",
                "new_sl": entry_price,
                "close_ratio": None,
                "drawdown_ratio": 0.0,
                "threshold_used": 0.15,
            }
        if profit_pct > 0.08:
            return {
                "type": "tighten_sl",
                "reason": f"{symbol} 浮盈{profit_pct:.0%}>8% → 收紧止损跟踪",
                "new_sl": self._compute_profit_lock_sl(symbol, side, entry_price, current_price, nature),
                "close_ratio": None,
                "drawdown_ratio": 0.0,
                "threshold_used": 0.05,
            }
        return None

    # ══════════════════════════════════════════════════════
    #  动作分类
    # ══════════════════════════════════════════════════════

    def _classify_action(
        self,
        dd_ratio: float,
        threshold: float,
        current_upnl: float,
        position_value: float = 0.0,
    ) -> Optional[str]:
        """
        根据回撤严重程度返回动作类型。

        三级响应：
        L1: dd_ratio >= threshold, upnl > 0        → tighten_sl (锁利)
        L2: dd_ratio >= threshold + 0.30, upnl > 0 → partial_close (减仓锁利)
        L3: dd_ratio >= 1.0 (翻转为亏损)            → full_close (止损)
        """
        if dd_ratio >= 1.0 and current_upnl < 0:
            # 盈利翻亏：仅当亏损达名义 1% 才全平，避免 profit_drawdown_full 0% 胜率碎平
            min_flip_loss = position_value * 0.01 if position_value > 0 else 0
            if min_flip_loss > 0 and abs(current_upnl) < min_flip_loss:
                return "tighten_sl"
            return "full_close"

        if dd_ratio >= threshold + 0.30 and current_upnl > 0:
            # 严重回撤但仍盈利 → 部分减仓 + 锁利（提高门槛，减少 57次/周式碎卖）
            return "partial_close"

        if dd_ratio >= threshold and current_upnl > 0:
            # 达到回撤阈值但仍盈利 → 收紧 SL 锁利
            return "tighten_sl"

        return None

    # ══════════════════════════════════════════════════════
    #  SL 计算
    # ══════════════════════════════════════════════════════

    def _compute_profit_lock_sl(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        current_price: float,
        nature: str,
    ) -> float:
        """
        计算锁利 SL 价格。

        原则：将 SL 推到 entry + buffer 位置（盈利侧），
        确保即使被止损也是盈利出场。

        Buffer 使用波动率自适应值：
        - 低波币 (BTC/ETH): entry ± 0.8%
        - 中波币: entry ± 1.5%
        - 高波币: entry ± 2.5%
        """
        vol_class = self._classify_volatility(symbol)
        buffer = PROFIT_LOCK_BUFFER.get(vol_class, 0.015)

        if side == "long":
            # SL 拉到 entry 之上 → 保本+微利
            new_sl = entry_price * (1.0 + buffer)
            # 不应高于当前价格（否则立即触发）
            new_sl = min(new_sl, current_price * 0.995)
        else:
            # SL 拉到 entry 之下
            new_sl = entry_price * (1.0 - buffer)
            new_sl = max(new_sl, current_price * 1.005)

        return round(new_sl, 6)

    # ══════════════════════════════════════════════════════
    #  辅助
    # ══════════════════════════════════════════════════════

    def _build_reason(
        self,
        symbol: str,
        dd_ratio: float,
        threshold: float,
        peak_profit: float,
        current_upnl: float,
        action: str,
    ) -> str:
        action_cn = {
            "tighten_sl": "收紧SL锁利",
            "partial_close": "减仓50%锁利",
            "full_close": "利润翻转为亏损全平",
        }.get(action, action)

        return (
            f"[ProfitDrawdown] {symbol} {action_cn}: "
            f"峰值利润${peak_profit:.2f} → 当前${current_upnl:.2f} "
            f"(回撤{dd_ratio:.0%}, 阈值{threshold:.0%})"
        )

    def _log_action(self, symbol: str, result: Dict[str, Any]):
        """记录保护动作到内存日志"""
        key = symbol.upper()
        if key not in self._action_log:
            self._action_log[key] = []
        self._action_log[key].append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **{k: v for k, v in result.items() if k != "reason"},
        })
        # 只保留最近 50 条
        if len(self._action_log[key]) > 50:
            self._action_log[key] = self._action_log[key][-50:]

        logger.info(result["reason"])

    def get_action_history(self, symbol: Optional[str] = None) -> Dict[str, list]:
        """获取保护动作历史（用于监控/调试）"""
        if symbol:
            return {symbol.upper(): self._action_log.get(symbol.upper(), [])}
        return dict(self._action_log)

    def get_threshold_info(self, symbol: str, nature: str = "swing") -> Dict[str, Any]:
        """查询某币种的当前阈值配置（用于前端展示）"""
        vol_class = self._classify_volatility(symbol)
        threshold = self._get_threshold(symbol, nature)
        return {
            "symbol": symbol.upper(),
            "nature": nature,
            "volatility_class": vol_class,
            "base_threshold": BASE_DRAWDOWN_THRESHOLD.get(vol_class, 0.50),
            "nature_adjustment": NATURE_ADJUSTMENT.get(nature, 0.0),
            "effective_threshold": round(threshold, 3),
            "profit_lock_buffer": PROFIT_LOCK_BUFFER.get(vol_class, 0.015),
        }


# ── 全局单例 ──
_profit_drawdown_guard: Optional[ProfitDrawdownGuard] = None


def get_profit_drawdown_guard() -> ProfitDrawdownGuard:
    global _profit_drawdown_guard
    if _profit_drawdown_guard is None:
        _profit_drawdown_guard = ProfitDrawdownGuard()
    return _profit_drawdown_guard
