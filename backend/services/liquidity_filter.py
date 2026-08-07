"""
流动性过滤器 — LiquidityFilter

只允许策略交易满足以下条件的币种（方案§8.2 (4)）：
  - 24h 成交量 >= 500 万美元
  - 订单簿深度（±2%范围）>= 单笔交易额的 5 倍
  - 单笔交易额 <= 日成交量的 0.5%（防止市场冲击过大）

不满足条件的币种直接跳过，不产生信号。
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class LiquidityCheckResult:
    """流动性检查结果"""
    symbol: str
    passed: bool
    reason: str
    volume_24h_usd: float = 0.0
    order_size_usd: float = 0.0
    depth_usd: float = 0.0
    impact_pct: float = 0.0          # 订单占日成交量比例（%）


class LiquidityFilter:
    """
    流动性过滤器。

    使用：
        lf = LiquidityFilter()
        ok, result = lf.check(symbol="BTC", order_size_usd=5000, volume_24h_usd=1_000_000_000)
    """

    # 最低日成交量（美元）
    MIN_VOLUME_24H_USD: float = 5_000_000        # 500 万
    # 订单簿深度要求：订单额的倍数
    MIN_DEPTH_MULTIPLIER: float = 5.0
    # 订单占日成交量的最大比例
    MAX_VOLUME_IMPACT_PCT: float = 0.5           # 0.5%

    def check(
        self,
        symbol: str,
        order_size_usd: float,
        volume_24h_usd: float,
        depth_usd: Optional[float] = None,       # ±2% 订单簿深度（可选，无则仅检查成交量）
    ) -> Tuple[bool, LiquidityCheckResult]:
        """
        检查某个币种的流动性是否满足交易条件。

        Returns:
            (passed: bool, result: LiquidityCheckResult)
        """
        # 1. 日成交量检查
        if volume_24h_usd < self.MIN_VOLUME_24H_USD:
            return False, LiquidityCheckResult(
                symbol=symbol,
                passed=False,
                reason=f"日成交量 ${volume_24h_usd:,.0f} < ${self.MIN_VOLUME_24H_USD:,.0f}（最低500万）",
                volume_24h_usd=volume_24h_usd,
                order_size_usd=order_size_usd,
            )

        # 2. 订单占日成交量比例检查
        impact_pct = (order_size_usd / volume_24h_usd * 100) if volume_24h_usd > 0 else 100
        if impact_pct > self.MAX_VOLUME_IMPACT_PCT:
            return False, LiquidityCheckResult(
                symbol=symbol,
                passed=False,
                reason=(
                    f"订单 ${order_size_usd:,.0f} 占日成交量 {impact_pct:.3f}% "
                    f"> {self.MAX_VOLUME_IMPACT_PCT}%（市场冲击过大）"
                ),
                volume_24h_usd=volume_24h_usd,
                order_size_usd=order_size_usd,
                impact_pct=impact_pct,
            )

        # 3. 订单簿深度检查（可选）
        if depth_usd is not None and depth_usd > 0:
            required_depth = order_size_usd * self.MIN_DEPTH_MULTIPLIER
            if depth_usd < required_depth:
                return False, LiquidityCheckResult(
                    symbol=symbol,
                    passed=False,
                    reason=(
                        f"±2% 订单簿深度 ${depth_usd:,.0f} < 订单额 {self.MIN_DEPTH_MULTIPLIER}x "
                        f"= ${required_depth:,.0f}"
                    ),
                    volume_24h_usd=volume_24h_usd,
                    order_size_usd=order_size_usd,
                    depth_usd=depth_usd,
                    impact_pct=impact_pct,
                )

        return True, LiquidityCheckResult(
            symbol=symbol,
            passed=True,
            reason="流动性检查通过",
            volume_24h_usd=volume_24h_usd,
            order_size_usd=order_size_usd,
            depth_usd=depth_usd or 0,
            impact_pct=impact_pct,
        )

    def filter_symbols(
        self,
        symbols: List[str],
        order_size_usd: float,
        volume_map: Dict[str, float],     # {symbol: volume_24h_usd}
        depth_map: Optional[Dict[str, float]] = None,  # {symbol: depth_usd}
    ) -> Tuple[List[str], List[LiquidityCheckResult]]:
        """
        批量过滤符合流动性条件的交易对。

        Returns:
            (passed_symbols, all_results)
        """
        passed = []
        results = []
        for sym in symbols:
            vol = volume_map.get(sym, 0)
            depth = depth_map.get(sym) if depth_map else None
            ok, result = self.check(sym, order_size_usd, vol, depth)
            results.append(result)
            if ok:
                passed.append(sym)
            else:
                logger.debug(f"[LiquidityFilter] {sym} 过滤掉: {result.reason}")
        return passed, results

    def estimate_slippage(
        self,
        order_size_usd: float,
        volume_24h_usd: float,
    ) -> float:
        """
        基于订单大小与日成交量比例的滑点估算（方案§8.2 (2)）。

        Returns:
            estimated_slippage_pct（小数，如 0.0005 表示 0.05%）
        """
        BASE_SLIPPAGE = 0.0003      # 基础滑点 0.03%
        IMPACT_FACTOR = 0.1         # 市场冲击系数

        volume_ratio = order_size_usd / volume_24h_usd if volume_24h_usd > 0 else 1.0
        market_impact = volume_ratio * IMPACT_FACTOR

        return BASE_SLIPPAGE + market_impact


# 模块级单例
liquidity_filter = LiquidityFilter()
