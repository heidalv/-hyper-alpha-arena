"""
套利仓位持续监控

6项实时监控：
1. Delta 漂移跟踪
2. 资金费率趋势监控
3. 价差 Z-Score 跟踪
4. 清算距离监控
5. P&L 跟踪
6. 持仓老化监控

每个 tick 运行，输出 ArbitragePositionMetrics 列表和操作建议。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from .unified_models import (
    ArbHedgePosition,
    ArbitragePositionMetrics,
    ArbitrageStatus,
    StrategyType,
)

logger = logging.getLogger(__name__)


class PositionMetricsTracker:
    """仓位指标跟踪器"""

    # 再平衡阈值
    DELTA_REBALANCE_THRESHOLD: float = 0.016   # 0.8 * 2%
    # 清算距离阈值
    LIQUIDATION_WARNING_PCT: float = 0.15
    LIQUIDATION_FORCE_CLOSE_PCT: float = 0.08
    # 最大持仓时间
    MAX_HOLD_HOURS = {
        StrategyType.FUNDING_RATE: 72,
        StrategyType.CROSS_EXCHANGE_SPREAD: 24,
        StrategyType.SPOT_PERP_BASIS: 48,
    }
    # 边际衰减阈值
    EDGE_DECAY_CLOSE_PCT: float = 0.80

    def monitor_all(
        self,
        positions: List[ArbHedgePosition],
        current_prices: Optional[Dict[str, float]] = None,
        current_funding_rates: Optional[Dict[str, float]] = None,
        current_z_scores: Optional[Dict[str, float]] = None,
    ) -> Tuple[List[ArbitragePositionMetrics], List[Dict[str, Any]]]:
        """
        监控所有活跃仓位

        Returns:
            (metrics_list, actions_list)
            actions_list 包含需要执行的操作（rebalance, close, alert）
        """
        metrics_list = []
        actions_list = []

        for pos in positions:
            if pos.status != ArbitrageStatus.ACTIVE:
                continue

            metrics = self._compute_metrics(
                pos, current_prices, current_funding_rates, current_z_scores
            )
            metrics_list.append(metrics)

            # 检查各项阈值
            pos_actions = self._check_thresholds(pos, metrics)
            actions_list.extend(pos_actions)

        return metrics_list, actions_list

    def _compute_metrics(
        self,
        pos: ArbHedgePosition,
        prices: Optional[Dict[str, float]],
        funding_rates: Optional[Dict[str, float]],
        z_scores: Optional[Dict[str, float]],
    ) -> ArbitragePositionMetrics:
        """计算单个仓位的指标"""
        now = time.time()
        age_hours = (now - pos.entry_time) / 3600 if pos.entry_time > 0 else 0

        # Delta 计算
        price = (prices or {}).get(pos.symbol, 0)
        if price > 0:
            long_value = pos.long_size * price
            short_value = pos.short_size * price
            current_delta = long_value - short_value
            max_side = max(long_value, short_value, 1e-10)
            delta_pct = abs(current_delta) / max_side
        else:
            current_delta = pos.delta
            delta_pct = 0.0

        # Unrealized PnL
        unrealized = 0.0
        if price > 0:
            long_pnl = (price - pos.long_entry_price) * pos.long_size
            short_pnl = (pos.short_entry_price - price) * pos.short_size
            unrealized = long_pnl + short_pnl

        # 资金费率趋势
        funding_trend = "stable"
        if funding_rates and pos.symbol in funding_rates:
            # 简化：基于当前费率方向与策略方向对比
            current_rate = funding_rates[pos.symbol]
            if pos.strategy == StrategyType.FUNDING_RATE:
                # 做空收资金费 → 费率应为正
                # 做多收资金费 → 费率应为负
                if current_rate * pos.delta < 0:
                    funding_trend = "deteriorating"
                else:
                    funding_trend = "improving"

        # Z-Score
        z_score = (z_scores or {}).get(pos.symbol, 0.0)

        # 清算距离（简化估算）
        liq_distance = 100.0  # 默认100%
        if pos.liquidation_price_long > 0 and price > 0:
            liq_distance = min(liq_distance, abs(price - pos.liquidation_price_long) / price)
        if pos.liquidation_price_short > 0 and price > 0:
            liq_distance = min(liq_distance, abs(price - pos.liquidation_price_short) / price)

        # 边际衰减
        edge_decay = 0.0
        if pos.entry_edge > 0:
            # 简化：用 age 估算衰减
            max_hold = self.MAX_HOLD_HOURS.get(pos.strategy, 48)
            edge_decay = min(age_hours / max_hold, 1.0)

        return ArbitragePositionMetrics(
            position_id=pos.position_id,
            current_delta=current_delta,
            delta_pct=delta_pct,
            unrealized_pnl=unrealized,
            accumulated_funding=pos.accumulated_funding,
            funding_trend=funding_trend,
            z_score_current=z_score,
            liquidation_distance_pct=liq_distance,
            age_hours=age_hours,
            edge_decay_pct=edge_decay,
            entry_edge=pos.entry_edge if hasattr(pos, 'entry_edge') else 0,
        )

    def _check_thresholds(
        self,
        pos: ArbHedgePosition,
        metrics: ArbitragePositionMetrics,
    ) -> List[Dict[str, Any]]:
        """检查各项阈值，返回操作列表"""
        actions = []

        # 1. Delta 漂移
        if metrics.delta_pct > self.DELTA_REBALANCE_THRESHOLD:
            actions.append({
                "type": "rebalance",
                "position_id": pos.position_id,
                "reason": f"delta_drift={metrics.delta_pct:.2%}",
                "priority": "medium",
            })

        # 2. 资金费率恶化
        if metrics.funding_trend == "deteriorating":
            actions.append({
                "type": "alert",
                "position_id": pos.position_id,
                "reason": "funding_trend_deteriorating",
                "priority": "high",
            })

        # 3. 清算距离
        if metrics.liquidation_distance_pct < self.LIQUIDATION_FORCE_CLOSE_PCT:
            actions.append({
                "type": "force_close",
                "position_id": pos.position_id,
                "reason": f"liquidation_distance={metrics.liquidation_distance_pct:.1%}",
                "priority": "critical",
            })
        elif metrics.liquidation_distance_pct < self.LIQUIDATION_WARNING_PCT:
            actions.append({
                "type": "alert",
                "position_id": pos.position_id,
                "reason": f"liquidation_warning={metrics.liquidation_distance_pct:.1%}",
                "priority": "high",
            })

        # 4. 持仓老化
        max_hold = self.MAX_HOLD_HOURS.get(pos.strategy, 48)
        if metrics.age_hours > max_hold:
            actions.append({
                "type": "close",
                "position_id": pos.position_id,
                "reason": f"max_hold_exceeded={metrics.age_hours:.1f}h>{max_hold}h",
                "priority": "medium",
            })

        # 5. 边际衰减
        if metrics.edge_decay_pct > self.EDGE_DECAY_CLOSE_PCT:
            actions.append({
                "type": "close",
                "position_id": pos.position_id,
                "reason": f"edge_decay={metrics.edge_decay_pct:.0%}",
                "priority": "medium",
            })

        return actions


# ── 模块级单例 ──
metrics_tracker = PositionMetricsTracker()
