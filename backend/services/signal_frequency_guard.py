#!/usr/bin/env python3
"""
SignalFrequencyGuard — 最低信号频率保障器

监控各 tier 的信号产生频率，当连续 X 小时无信号时自动降低阈值，
并将"最有潜力"的标的强制注入 LLM 分析。

设计目标：确保 short tier 每天 >=3 个信号，避免连续零信号。
"""

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# 日最低信号目标
MIN_DAILY_SIGNALS = {"short": 5, "mid": 2, "long": 1}

# 频率保障触发小时数
SIGNAL_FREQUENCY_GUARD_HOURS = 2

# 阈值降幅梯度
THRESHOLD_REDUCTION_STEPS = [
    # (无信号小时数, 阈值降幅)
    (1, 0),
    (2, 5),
    (4, 10),
    (6, 15),
]

# 阈值下限（百分比）
THRESHOLD_FLOOR = {"short": 30, "mid": 35, "long": 35}


@dataclass
class FrequencyState:
    """单个 tier 的频率状态"""
    tier: str
    last_signal_time: float = 0.0
    signal_count_today: int = 0
    last_reset_date: str = ""
    threshold_reduction: float = 0.0


class SignalFrequencyGuard:
    """最低信号频率保障器（内存单例，线程安全）"""

    def __init__(self):
        self._states: Dict[str, FrequencyState] = {}
        self._lock = threading.Lock()
        # 从环境变量读取配置（延迟导入避免循环依赖）
        self._min_daily = dict(MIN_DAILY_SIGNALS)
        self._guard_hours = SIGNAL_FREQUENCY_GUARD_HOURS

    def _get_state(self, tier: str) -> FrequencyState:
        if tier not in self._states:
            self._states[tier] = FrequencyState(tier=tier)
        return self._states[tier]

    def _check_date_reset(self, state: FrequencyState):
        """跨日重置计数"""
        import datetime
        today = datetime.date.today().isoformat()
        if state.last_reset_date != today:
            state.signal_count_today = 0
            state.last_reset_date = today

    def record_signal(self, tier: str):
        """记录一次信号产生事件"""
        with self._lock:
            state = self._get_state(tier)
            self._check_date_reset(state)
            state.last_signal_time = time.time()
            state.signal_count_today += 1
            state.threshold_reduction = 0.0  # 有信号则重置降幅

    def get_threshold_adjustment(self, tier: str) -> int:
        """[已废弃 2026-06-13] 历史假开关：从无任何消费端调用，且 record_signal
        从未被触发使其逻辑失真。无信号降阈值的职责已由 maturity_controller 的
        warmup 松紧系数统一承担（数据驱动、双向、可解释）。

        保留方法签名仅为兼容，恒返回 0，避免与成熟度松紧重复降阈值。"""
        return 0

    def get_guaranteed_symbols(
        self,
        tier: str,
        symbols: List[str],
        market_summary: Dict[str, Dict],
    ) -> List[str]:
        """
        如果当日信号数远低于目标，返回最有潜力的标的强制进入 LLM

        选择逻辑：RSI 离 50 最远的、价格变化最大的
        """
        with self._lock:
            state = self._get_state(tier)
            self._check_date_reset(state)

            min_target = self._min_daily.get(tier, 1)
            if state.signal_count_today >= min_target * 0.3:
                return []

            # 需要 2-3 个保障标的
            needed = max(1, min(3, min_target - state.signal_count_today))

        # 按"潜力"排序：RSI 离 50 最远 + 价格变化最大
        scored: List[tuple] = []
        for symbol in symbols:
            sym_data = market_summary.get(symbol, {})
            score = 0.0

            # 利用已有价格数据计算简单评分
            price = sym_data.get("current_price", 0)
            # 兼容两种字段名
            change_pct = (
                sym_data.get("change_24h_pct")
                or sym_data.get("price_change_24h_pct")
                or sym_data.get("price_change_1h_pct")
                or 0
            )
            if change_pct:
                score += abs(float(change_pct))

            # 如果有 RSI 数据
            rsi = sym_data.get("rsi")
            if rsi is not None:
                score += abs(float(rsi) - 50) / 10  # RSI 离50越远越好

            if score > 0:
                scored.append((score, symbol))

        scored.sort(reverse=True)
        guaranteed = [s[1] for s in scored[:needed]]

        if guaranteed:
            logger.info(
                f"[SignalFrequencyGuard] {tier} tier: 频率保障注入 {guaranteed} "
                f"(今日仅 {state.signal_count_today}/{min_target})"
            )

        return guaranteed

    def get_status(self) -> Dict[str, Any]:
        """获取各 tier 频率状态（用于调试/日志）"""
        with self._lock:
            return {
                tier: {
                    "last_signal_time": state.last_signal_time,
                    "signal_count_today": state.signal_count_today,
                    "threshold_reduction": state.threshold_reduction,
                }
                for tier, state in self._states.items()
            }


# ── 单例 ──────────────────────────────────────────────────

_process_start_time = time.time()
_instance: Optional[SignalFrequencyGuard] = None


def get_signal_frequency_guard() -> SignalFrequencyGuard:
    global _instance
    if _instance is None:
        _instance = SignalFrequencyGuard()
    return _instance
