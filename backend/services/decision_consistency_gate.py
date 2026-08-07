"""
Decision Consistency Gate — 决策一致性门控 (D2)

检测并阻止以下破坏性决策模式：
1. 方向翻转 (Flip-Flop): 同一 symbol 30min 内 BUY→SELL 或 SELL→BUY
2. 置信度波动 (Confidence Volatility): 连续 3+ 决策置信度标准差 > 0.3
3. 震荡市过度交易: ranging 市场 + 频繁交易 → 延长冷却

设计原则：
- 单例模式，跨决策持久化内存状态
- 拦截 = 强制 HOLD，记录到日志
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 门控阈值
FLIP_FLOP_WINDOW_SEC = 1800        # 30 分钟
FLIP_FLOP_MIN_INTERVAL_SEC = 180   # 方向翻转最小间隔 3 分钟（原 5 分钟，混合模式降低以提高信号频率）
CONFIDENCE_VOLATILITY_WINDOW = 5   # 最近 N 次决策
CONFIDENCE_VOLATILITY_MAX_STD = 0.3
RANGING_COOLDOWN_EXTEND_FACTOR = 1.5  # 震荡市冷却时间 ×1.5（原 ×2，混合模式放宽）
RANGING_OVERTRADE_LIMIT = 6        # 成熟期 1h 内非 HOLD 决策上限
RANGING_OVERTRADE_WARMUP_LIMIT = 12  # warmup/growth 期放宽上限（鼓励累积数据）


@dataclass
class ConsistencyCheckResult:
    """一致性检查结果"""
    passed: bool = True
    reason: str = ""
    check_name: str = "consistency_gate"
    details: Dict[str, Any] = field(default_factory=dict)


class DecisionConsistencyGate:
    """
    决策一致性门控 (D2)

    追踪最近的 AI/规则引擎决策，检测并阻止不一致的决策模式。
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

        # 决策历史: key = "account_id:symbol" → list of (timestamp, action, direction, confidence)
        self._decision_history: Dict[str, List[Tuple[float, str, int, float]]] = (
            defaultdict(list)
        )
        # 翻转计数: 用于趋势分析
        self._flip_flop_counters: Dict[str, int] = defaultdict(int)
        # 上次拦截时间
        self._last_block_time: Dict[str, float] = {}

        # 最大保留历史条数
        self._max_history_per_key = 20

        logger.info("[ConsistencyGate] 决策一致性门控初始化完成")

    def check(
        self,
        account_id: int,
        symbol: str,
        action: str,
        confidence: float,
        market_regime: str = "unknown",
    ) -> ConsistencyCheckResult:
        """检查决策一致性。

        Args:
            account_id: 账户 ID
            symbol: 交易品种
            action: BUY / SELL / HOLD
            confidence: 置信度 0-1
            market_regime: 市场状态 (trending / ranging / crash 等)

        Returns:
            ConsistencyCheckResult
        """
        action_upper = action.upper() if action else "HOLD"
        if action_upper == "HOLD":
            # HOLD 不需要一致性检查
            self._record_decision(account_id, symbol, action_upper, 0, confidence)
            return ConsistencyCheckResult(passed=True)

        direction = 1 if action_upper == "BUY" else -1
        key = f"{account_id}:{symbol}"

        # 1. 方向翻转检测 (Flip-Flop)
        flip_result = self._check_flip_flop(key, direction, confidence)
        if not flip_result.passed:
            self._last_block_time[key] = time.time()
            return flip_result

        # 2. 置信度波动检测
        conf_vol_result = self._check_confidence_volatility(key, confidence)
        if not conf_vol_result.passed:
            self._last_block_time[key] = time.time()
            return conf_vol_result

        # 3. 震荡市过度交易检测
        if market_regime in ("ranging", "low_volatility"):
            ranging_result = self._check_ranging_overtrade(key)
            if not ranging_result.passed:
                return ranging_result

        # 记录本次决策
        self._record_decision(account_id, symbol, action_upper, direction, confidence)
        return ConsistencyCheckResult(passed=True)

    def _check_flip_flop(
        self, key: str, direction: int, confidence: float
    ) -> ConsistencyCheckResult:
        """检测方向翻转"""
        history = self._decision_history.get(key, [])
        if len(history) < 1:
            return ConsistencyCheckResult(passed=True)

        now = time.time()
        recent = [
            (ts, act, d, conf)
            for ts, act, d, conf in history
            if now - ts < FLIP_FLOP_WINDOW_SEC
        ]

        if not recent:
            return ConsistencyCheckResult(passed=True)

        last_ts, last_action, last_dir, last_conf = recent[-1]

        # 方向相反且间隔 < 最小翻转间隔
        if last_dir != 0 and last_dir != direction:
            interval = now - last_ts
            if interval < FLIP_FLOP_MIN_INTERVAL_SEC:
                self._flip_flop_counters[key] += 1
                return ConsistencyCheckResult(
                    passed=False,
                    check_name="flip_flop_detection",
                    reason=(
                        f"{key} 方向翻转: {last_action}→{'BUY' if direction > 0 else 'SELL'} "
                        f"(间隔 {interval:.0f}s < {FLIP_FLOP_MIN_INTERVAL_SEC}s), "
                        f"翻转计数={self._flip_flop_counters[key]}"
                    ),
                    details={
                        "interval_s": interval,
                        "flip_count": self._flip_flop_counters[key],
                        "last_action": last_action,
                        "last_confidence": last_conf,
                    },
                )

        return ConsistencyCheckResult(passed=True)

    def _check_confidence_volatility(
        self, key: str, confidence: float
    ) -> ConsistencyCheckResult:
        """检测置信度剧烈波动"""
        history = self._decision_history.get(key, [])
        now = time.time()
        recent_confs = [
            conf
            for ts, act, d, conf in history
            if now - ts < FLIP_FLOP_WINDOW_SEC and act != "HOLD"
        ]

        if len(recent_confs) < 3:
            return ConsistencyCheckResult(passed=True)

        recent_confs.append(confidence)
        recent_confs = recent_confs[-CONFIDENCE_VOLATILITY_WINDOW:]

        if len(recent_confs) < 3:
            return ConsistencyCheckResult(passed=True)

        mean_c = sum(recent_confs) / len(recent_confs)
        variance = sum((c - mean_c) ** 2 for c in recent_confs) / len(recent_confs)
        std_c = variance ** 0.5

        if std_c > CONFIDENCE_VOLATILITY_MAX_STD:
            return ConsistencyCheckResult(
                passed=False,
                check_name="confidence_volatility",
                reason=(
                    f"{key} 置信度波动过大: std={std_c:.3f} > {CONFIDENCE_VOLATILITY_MAX_STD} "
                    f"(最近{len(recent_confs)}次: {[round(c, 2) for c in recent_confs]})"
                ),
                details={
                    "confidence_std": std_c,
                    "recent_confidences": [round(c, 2) for c in recent_confs],
                    "max_allowed_std": CONFIDENCE_VOLATILITY_MAX_STD,
                },
            )

        return ConsistencyCheckResult(passed=True)

    def _check_ranging_overtrade(
        self, key: str
    ) -> ConsistencyCheckResult:
        """检测震荡市过度交易"""
        history = self._decision_history.get(key, [])
        now = time.time()
        recent_trades = [
            (ts, act) for ts, act, d, conf in history
            if now - ts < 3600 and act != "HOLD"
        ]

        # 1h 内非 HOLD 决策上限：warmup/growth 期放宽（鼓励累积数据），
        # 成熟期收紧到基准。live 恒 mature。
        _limit = RANGING_OVERTRADE_LIMIT
        try:
            from backend.services.maturity_controller import get_global_stage
            if get_global_stage("paper") in ("warmup", "growth"):
                _limit = RANGING_OVERTRADE_WARMUP_LIMIT
        except Exception:
            pass

        if len(recent_trades) >= _limit:
            return ConsistencyCheckResult(
                passed=False,
                check_name="ranging_overtrade",
                reason=(
                    f"{key} 震荡市过度交易: "
                    f"1h内{len(recent_trades)}笔决策≥{_limit}, 建议冷却"
                ),
                details={
                    "trades_in_1h": len(recent_trades),
                    "limit": _limit,
                    "regime": "ranging",
                },
            )

        return ConsistencyCheckResult(passed=True)

    def _record_decision(
        self,
        account_id: int,
        symbol: str,
        action: str,
        direction: int,
        confidence: float,
    ):
        """记录决策到历史"""
        key = f"{account_id}:{symbol}"
        history = self._decision_history[key]
        history.append((time.time(), action, direction, confidence))

        # 裁剪旧记录
        if len(history) > self._max_history_per_key:
            cutoff = time.time() - FLIP_FLOP_WINDOW_SEC * 4
            self._decision_history[key] = [
                h for h in history if h[0] > cutoff
            ][-self._max_history_per_key:]

    def get_flip_flop_stats(self) -> Dict[str, Any]:
        """获取翻转统计（用于监控）"""
        return {
            "total_flip_flops": sum(self._flip_flop_counters.values()),
            "worst_symbols": sorted(
                self._flip_flop_counters.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:5],
            "active_blocks": {
                k: time.time() - v
                for k, v in self._last_block_time.items()
                if time.time() - v < FLIP_FLOP_WINDOW_SEC
            },
        }

    def reset_symbol(self, account_id: int, symbol: str):
        """重置单个 symbol 的状态（平仓时调用）"""
        key = f"{account_id}:{symbol}"
        self._decision_history.pop(key, None)
        self._flip_flop_counters.pop(key, None)
        self._last_block_time.pop(key, None)


# 全局单例
_consistency_gate: Optional[DecisionConsistencyGate] = None


def get_consistency_gate() -> DecisionConsistencyGate:
    global _consistency_gate
    if _consistency_gate is None:
        _consistency_gate = DecisionConsistencyGate()
    return _consistency_gate
