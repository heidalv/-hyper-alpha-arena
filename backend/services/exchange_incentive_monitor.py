"""
ExchangeIncentiveMonitor — 交易所费率/VIP等级/激励政策监控

战略规划 §4.4 要求:
1. 手续费阶梯与VIP等级追踪: 自动评估VIP升级收益
2. Maker/Taker 比例优化: 统计实际 maker/taker 比例，引导限价单
3. 费率成本汇总: 定期统计实际手续费支出

数据源:
- Hyperliquid: 固定费率 (maker 0.02%, taker 0.05%)
- 内部交易记录: PaperOrder 统计 maker/taker 分布

集成位置: evolution_scheduler 每日任务 / API 查询
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class FeeProfile:
    """交易所费率档案"""
    exchange: str
    tier: str = "default"
    maker_rate: float = 0.0002    # 0.02%
    taker_rate: float = 0.0005    # 0.05%
    volume_30d_usd: float = 0.0
    next_tier_threshold: float = 0.0
    savings_at_next_tier: float = 0.0


@dataclass
class FeeReport:
    """费率分析报告"""
    period_days: int = 30
    total_fee_usd: float = 0.0
    maker_pct: float = 0.0        # maker 订单占比
    taker_pct: float = 0.0
    total_volume_usd: float = 0.0
    avg_fee_rate: float = 0.0
    potential_savings_usd: float = 0.0
    recommendations: List[str] = field(default_factory=list)


# Hyperliquid 费率阶梯
HYPERLIQUID_TIERS = [
    {"tier": "default", "maker": 0.0002, "taker": 0.0005, "volume_min": 0},
    {"tier": "vip1", "maker": 0.00016, "taker": 0.00045, "volume_min": 5_000_000},
    {"tier": "vip2", "maker": 0.00012, "taker": 0.00040, "volume_min": 25_000_000},
    {"tier": "vip3", "maker": 0.00008, "taker": 0.00035, "volume_min": 100_000_000},
    {"tier": "mm", "maker": 0.0, "taker": 0.00020, "volume_min": 0},  # Market Maker
]


class ExchangeIncentiveMonitor:
    """交易所激励与费率监控"""

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._last_report_ts: float = 0
        self._report_cooldown = 3600  # 1h

    # ── Public API ───────────────────────────────

    def get_fee_profile(self, exchange: str = "hyperliquid", volume_30d: float = 0.0) -> FeeProfile:
        """
        获取当前费率档案。

        Args:
            exchange: 交易所名称
            volume_30d: 30天交易量 (USD)

        Returns:
            FeeProfile
        """
        if exchange == "hyperliquid":
            return self._hyperliquid_profile(volume_30d)

        return FeeProfile(exchange=exchange)

    def analyze_fee_efficiency(self, db=None, account_id: str = "", days: int = 30) -> FeeReport:
        """
        分析手续费效率。

        Args:
            db: DB session
            account_id: 账户ID
            days: 分析天数

        Returns:
            FeeReport
        """
        report = FeeReport(period_days=days)

        if db is None:
            return report

        try:
            from backend.database.models import PaperOrder
            from datetime import datetime, timedelta, timezone
            from sqlalchemy import func

            cutoff = datetime.now(timezone.utc) - timedelta(days=days)

            query = db.query(PaperOrder).filter(
                PaperOrder.created_at >= cutoff,
                PaperOrder.status == "filled",
            )
            if account_id not in (None, ""):
                try:
                    query = query.filter(PaperOrder.account_id == int(account_id))
                except (TypeError, ValueError):
                    logger.warning(f"[IncentiveMonitor] 无效 account_id: {account_id!r}")

            orders = query.all()

            if not orders:
                report.recommendations.append("无交易记录，无法分析")
                return report

            total_volume = 0.0
            total_fee = 0.0
            maker_count = 0
            taker_count = 0

            for order in orders:
                fill_price = float(order.filled_price or order.price or 0)
                qty = float(order.filled_quantity or order.quantity or 0)
                notional = qty * fill_price
                total_volume += notional

                # Determine maker/taker from order type
                is_maker = getattr(order, "order_type", "market") == "limit"
                fee_rate = 0.0002 if is_maker else 0.0005
                fee = float(order.fee or 0) or notional * fee_rate
                total_fee += fee

                if is_maker:
                    maker_count += 1
                else:
                    taker_count += 1

            total_orders = maker_count + taker_count
            report.total_volume_usd = total_volume
            report.total_fee_usd = total_fee
            report.maker_pct = maker_count / max(total_orders, 1)
            report.taker_pct = taker_count / max(total_orders, 1)
            report.avg_fee_rate = total_fee / max(total_volume, 1)

            # 计算如果全部转限价单的节省
            if report.taker_pct > 0.5:
                all_maker_fee = total_volume * 0.0002
                report.potential_savings_usd = total_fee - all_maker_fee
                report.recommendations.append(
                    f"当前Taker占比{report.taker_pct:.0%}, "
                    f"转为限价单可节省约${report.potential_savings_usd:.2f}"
                )

            # VIP 升级建议
            profile = self._hyperliquid_profile(total_volume)
            if profile.savings_at_next_tier > 0:
                report.recommendations.append(
                    f"距下一VIP等级差${profile.next_tier_threshold - total_volume:,.0f}交易量, "
                    f"升级后每月可节省约${profile.savings_at_next_tier:.2f}"
                )

            if report.avg_fee_rate > 0.0004:
                report.recommendations.append(
                    "平均费率偏高, 建议增加限价单使用比例"
                )

        except Exception as e:
            logger.warning(f"[IncentiveMonitor] 费率分析失败: {e}")
            report.recommendations.append(f"分析失败: {e}")

        return report

    def get_optimization_tips(self) -> List[Dict[str, str]]:
        """返回通用优化建议。"""
        return [
            {
                "category": "maker_priority",
                "tip": "非紧急开仓(trend_follow/swing)优先使用限价单, 享受maker费率0.02% vs taker 0.05%",
                "impact": "high",
            },
            {
                "category": "batch_orders",
                "tip": "分批建仓时合并为单笔大单, 减少交易次数和总手续费",
                "impact": "medium",
            },
            {
                "category": "fee_tracking",
                "tip": "每周检查费率报告, 确保maker比例持续 > 60%",
                "impact": "medium",
            },
            {
                "category": "volume_target",
                "tip": "如接近VIP阈值, 可适当增加正常交易频率以达标升级",
                "impact": "low",
            },
        ]

    # ── Internal ─────────────────────────────────

    def _hyperliquid_profile(self, volume_30d: float) -> FeeProfile:
        current_tier = HYPERLIQUID_TIERS[0]
        next_tier = None

        for i, tier in enumerate(HYPERLIQUID_TIERS[:-1]):  # exclude MM tier
            if volume_30d >= tier["volume_min"]:
                current_tier = tier
                if i + 1 < len(HYPERLIQUID_TIERS) - 1:
                    next_tier = HYPERLIQUID_TIERS[i + 1]

        savings = 0.0
        next_threshold = 0.0
        if next_tier and volume_30d < next_tier["volume_min"]:
            next_threshold = next_tier["volume_min"]
            # Monthly savings estimate at next tier
            fee_diff = current_tier["taker"] - next_tier["taker"]
            savings = volume_30d * fee_diff  # approximate monthly savings

        return FeeProfile(
            exchange="hyperliquid",
            tier=current_tier["tier"],
            maker_rate=current_tier["maker"],
            taker_rate=current_tier["taker"],
            volume_30d_usd=volume_30d,
            next_tier_threshold=next_threshold,
            savings_at_next_tier=savings,
        )


# Global singleton
_monitor: Optional[ExchangeIncentiveMonitor] = None


def get_incentive_monitor() -> ExchangeIncentiveMonitor:
    global _monitor
    if _monitor is None:
        _monitor = ExchangeIncentiveMonitor()
    return _monitor
