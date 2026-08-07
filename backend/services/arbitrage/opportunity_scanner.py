"""
资金费率套利机会扫描器

扫描所有交易对的资金费率，识别套利机会。
从 UnifiedSnapshot 读取市场数据，维护资金费率历史，
计算滚动平均值和年化收益率，输出 ArbitrageOpportunity 列表。

Phase 2 仅扫描，不执行实际交易。
"""

import logging
import math
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .models import ArbitrageOpportunity, ArbitrageStatus, FundingRateSnapshot

logger = logging.getLogger(__name__)


class OpportunityScanner:
    """
    资金费率套利机会扫描器

    扫描给定交易对的资金费率，当年化收益率超过阈值时生成套利机会。
    维护资金费率历史用于计算统计指标。
    """

    # ── 套利参数 ──
    MIN_ANNUAL_YIELD = 0.15         # 最低年化15%才开仓
    MIN_HISTORY_PERIODS = 24        # 至少24期历史数据
    MAX_POSITION_PCT = 0.20         # 单个套利最多用20%资金
    FUNDING_REVERSAL_THRESHOLD = 3  # 连续3期反转则标记

    # ── 历史记录上限 ──
    MAX_HISTORY_PER_SYMBOL = 1000

    def __init__(self):
        self._funding_history: Dict[str, List[Tuple[float, float]]] = {}
        self._cached_opportunities: List[ArbitrageOpportunity] = []
        self._lock = threading.Lock()
        self._scan_count: int = 0

    def scan_opportunities(
        self,
        symbols: List[str],
        snapshot: Any,
    ) -> List[ArbitrageOpportunity]:
        """
        扫描所有交易对的资金费率套利机会

        Args:
            symbols: 要扫描的交易对列表
            snapshot: UnifiedSnapshot 实例，包含 markets 和 derivatives_snapshot

        Returns:
            按年化收益率降序排列的套利机会列表
        """
        if not symbols or snapshot is None:
            return []

        opportunities = []
        current_ts = time.time()

        for symbol in symbols:
            try:
                opp = self._scan_symbol(symbol, snapshot, current_ts)
                if opp is not None:
                    opportunities.append(opp)
            except Exception as e:
                logger.debug(f"[套利扫描] {symbol} 扫描异常: {e}")
                continue

        # 按年化收益降序排列
        opportunities.sort(key=lambda x: x.expected_annual_yield, reverse=True)

        with self._lock:
            self._cached_opportunities = opportunities
            self._scan_count += 1

        if opportunities:
            top_sym = opportunities[0].symbol
            top_yield = opportunities[0].expected_annual_yield
            logger.info(
                f"[套利扫描] 发现 {len(opportunities)} 个机会, "
                f"最高: {top_sym} 年化={top_yield:.1%}"
            )

        return opportunities

    def get_active_opportunities(self) -> List[ArbitrageOpportunity]:
        """获取当前缓存的机会列表"""
        with self._lock:
            return list(self._cached_opportunities)

    def get_funding_history(self, symbol: str) -> List[float]:
        """获取某个 symbol 的资金费率历史"""
        with self._lock:
            return [rate for _, rate in self._funding_history.get(symbol, [])]

    @property
    def scan_count(self) -> int:
        return self._scan_count

    # ── 内部方法 ──

    def _scan_symbol(
        self, symbol: str, snapshot: Any, current_ts: float,
    ) -> Optional[ArbitrageOpportunity]:
        """扫描单个交易对"""
        # 1. 读取资金费率
        rate = self._get_funding_rate(symbol, snapshot)
        if rate is None:
            return None

        # 2. 获取辅助数据
        oi_total = self._get_oi(symbol, snapshot)
        volume_24h = self._get_volume(symbol, snapshot)
        predicted_rate = self._get_predicted_rate(symbol, snapshot)

        # 3. 更新历史
        self._append_history(symbol, current_ts, rate)
        history = self._get_rate_history(symbol)

        # 4. 历史数据不足则跳过
        if len(history) < self.MIN_HISTORY_PERIODS:
            return None

        # 5. 计算滚动统计
        avg_8h = self._rolling_avg(history, 3)    # 最近3期 ≈ 8h
        avg_24h = self._rolling_avg(history, 9)   # 最近9期 ≈ 24h

        # 6. 年化收益 = |费率| * 3次/天 * 365天
        annual_yield = abs(avg_24h) * 3 * 365

        if annual_yield < self.MIN_ANNUAL_YIELD:
            return None

        # 7. 策略方向
        strategy = "funding_short" if avg_24h > 0 else "funding_long"

        # 8. 风险评分
        risk_score = self._compute_risk_score(history)
        confidence = max(0.0, min(1.0, 1.0 - risk_score))

        # 9. 构建结果
        fr_snapshot = FundingRateSnapshot(
            symbol=symbol,
            current_rate=rate,
            predicted_rate=predicted_rate,
            rate_8h_avg=avg_8h,
            rate_24h_avg=avg_24h,
            annual_yield=annual_yield,
            oi_total=oi_total,
            volume_24h=volume_24h,
            timestamp=current_ts,
        )

        opp_id = f"arb_{symbol}_{int(current_ts)}"

        return ArbitrageOpportunity(
            opportunity_id=opp_id,
            symbol=symbol,
            strategy=strategy,
            expected_annual_yield=annual_yield,
            funding_snapshot=fr_snapshot,
            recommended_size=0.0,  # Phase 2 不计算实际仓位
            risk_score=risk_score,
            confidence=confidence,
            timestamp=current_ts,
        )

    def _get_funding_rate(self, symbol: str, snapshot: Any) -> Optional[float]:
        """从 snapshot 获取资金费率"""
        # 优先从 markets 获取
        market = getattr(snapshot, 'markets', {}).get(symbol)
        if market is not None:
            rate = getattr(market, 'funding_rate', None)
            if rate is not None and rate != 0.0:
                return float(rate)

        # 备选从 derivatives_snapshot 获取
        deriv = getattr(snapshot, 'derivatives_snapshot', {}).get(symbol, {})
        if isinstance(deriv, dict):
            rate = deriv.get('funding_rate', 0.0)
            if rate != 0.0:
                return float(rate)

        return None

    def _get_oi(self, symbol: str, snapshot: Any) -> float:
        """获取持仓量"""
        deriv = getattr(snapshot, 'derivatives_snapshot', {}).get(symbol, {})
        if isinstance(deriv, dict):
            return float(deriv.get('oi_total', 0.0))
        return 0.0

    def _get_volume(self, symbol: str, snapshot: Any) -> float:
        """获取24h成交量"""
        market = getattr(snapshot, 'markets', {}).get(symbol)
        if market is not None:
            return float(getattr(market, 'volume_24h', 0.0))
        return 0.0

    def _get_predicted_rate(self, symbol: str, snapshot: Any) -> float:
        """获取预测费率"""
        deriv = getattr(snapshot, 'derivatives_snapshot', {}).get(symbol, {})
        if isinstance(deriv, dict):
            return float(deriv.get('predicted_funding_rate', 0.0))
        return 0.0

    def _append_history(self, symbol: str, ts: float, rate: float):
        """追加资金费率历史"""
        with self._lock:
            if symbol not in self._funding_history:
                self._funding_history[symbol] = []
            hist = self._funding_history[symbol]
            hist.append((ts, rate))
            # 裁剪到上限
            if len(hist) > self.MAX_HISTORY_PER_SYMBOL:
                self._funding_history[symbol] = hist[-self.MAX_HISTORY_PER_SYMBOL:]

    def _get_rate_history(self, symbol: str) -> List[float]:
        """获取纯费率列表"""
        with self._lock:
            return [r for _, r in self._funding_history.get(symbol, [])]

    @staticmethod
    def _rolling_avg(history: List[float], periods: int) -> float:
        """计算滚动平均值"""
        if len(history) < periods:
            periods = len(history)
        if periods == 0:
            return 0.0
        return float(np.mean(history[-periods:]))

    def _compute_risk_score(self, history: List[float]) -> float:
        """
        计算风险评分 (0~1)

        因子：
        - 费率波动率: 标准差越大风险越高
        - 方向反转: 当前费率方向与7d平均不同则增加风险
        """
        if len(history) < 24:
            return 0.5

        recent = history[-24:]
        rate_vol = float(np.std(recent)) if len(recent) > 1 else 0.0

        # 反转风险：当前方向 vs 7d平均方向
        current_sign = 1 if history[-1] >= 0 else -1
        avg_7d_sign = 1 if np.mean(history[-63:]) >= 0 else -1 if len(history) >= 63 else current_sign
        reversal_risk = 1.0 if current_sign != avg_7d_sign else 0.0

        risk = min(rate_vol * 100 + reversal_risk * 0.3, 1.0)
        return max(0.0, risk)


# ── 模块级单例 ──
opportunity_scanner = OpportunityScanner()
