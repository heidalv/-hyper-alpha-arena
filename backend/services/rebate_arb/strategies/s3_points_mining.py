"""
S3: 积分挖矿策略 (Hyperliquid Points → HYPE空投)

核心逻辑:
- 在 Hyperliquid 上活跃交易积累 Points（Season 3 进行中）
- Maker 挂单量权重更优，做市轮转模式
- 积分按赛季分配 HYPE 空投，但 Season 3 兑换比例官方无承诺
  → EV 必须打投机性折扣（points_speculative_discount）
- hype_price 从行情实时读取，写死价格仅作回退
"""

import logging
from typing import Any, Dict, Optional

from ..models import RebateStrategyType, StrategyEvaluation

logger = logging.getLogger(__name__)


class S3PointsMiningStrategy:
    """S3: 积分挖矿 (Hyperliquid Points → HYPE空投)"""

    # Hyperliquid 现行基础费率（Season 3 对齐：旧值 maker 0.02% 已过期）
    HL_MAKER = 0.00015         # 0.015% Maker（基础档）
    HL_TAKER = 0.00045         # 0.045% Taker（基础档）
    DEFAULT_POINTS_PER_DAY = 50  # 小资金保守估计
    HYPE_PRICE_FALLBACK = 30.0   # 行情不可用时的回退价
    SEASON_DURATION_DAYS = 90
    # Season 3: Maker 量计分权重更优
    MAKER_VOLUME_WEIGHT = 2.0
    # 积分兑换无官方承诺 → EV 投机性折扣
    POINTS_SPECULATIVE_DISCOUNT = 0.5

    # 最低启动资金
    MIN_EQUITY = 100

    def __init__(self, config: Dict = None):
        """Initialize with optional config overrides."""
        if config:
            self.HL_MAKER = config.get("hl_maker", self.HL_MAKER)
            self.HL_TAKER = config.get("hl_taker", self.HL_TAKER)
            self.HYPE_PRICE_FALLBACK = config.get(
                "hype_price_fallback",
                config.get("hype_price", self.HYPE_PRICE_FALLBACK),
            )
            self.MAKER_VOLUME_WEIGHT = config.get("maker_volume_weight", self.MAKER_VOLUME_WEIGHT)
            self.POINTS_SPECULATIVE_DISCOUNT = config.get(
                "points_speculative_discount", self.POINTS_SPECULATIVE_DISCOUNT
            )

    def _fetch_hype_price(self) -> Optional[float]:
        """从行情读取 HYPE 现价（不可用时返回 None，由调用方回退）。"""
        try:
            from backend.services.rebate_arb.rebate_paper_market import resolve_paper_market

            quote = resolve_paper_market("HYPE/USDT", "hyperliquid")
            if quote is not None and quote.mid > 0:
                return float(quote.mid)
        except Exception as exc:
            logger.debug("[S3] HYPE 行情读取失败，使用回退价: %s", exc)
        return None

    def update_params(self, params: Dict[str, Any]) -> None:
        """运行时更新策略参数"""
        for key, value in params.items():
            upper_key = key.upper()
            if hasattr(self, upper_key):
                setattr(self, upper_key, value)
            elif hasattr(self, key):
                setattr(self, key, value)
        # Recompute derived values
        self._recompute_derived()

    def _recompute_derived(self) -> None:
        """重算派生参数（子类可覆盖）"""
        pass

    def evaluate(self, incentive_data: Dict, account_equity: float) -> StrategyEvaluation:
        """评估S3策略可行性"""
        hl_data = incentive_data.get("hyperliquid", {})
        daily_points = hl_data.get("daily_points_rate", self.DEFAULT_POINTS_PER_DAY)
        points_balance = hl_data.get("points_balance", 0.0)

        # hype_price 优先级：激励数据 > 实时行情 > 回退价
        if hl_data.get("hype_price"):
            hype_price = float(hl_data["hype_price"])
            hype_price_source = "incentive"
        else:
            market_price = self._fetch_hype_price()
            if market_price:
                hype_price = market_price
                hype_price_source = "market"
            else:
                hype_price = self.HYPE_PRICE_FALLBACK
                hype_price_source = "fallback"

        # 积分估值：每积分 ≈ $0.005 × HYPE价格 × 投机性折扣
        # （Season 3 兑换比例官方无承诺，估值基于 Season 1 历史数据外推）
        points_value_rate = 0.005 * hype_price * self.POINTS_SPECULATIVE_DISCOUNT

        # 月积分价值（Maker 量权重更优，做市轮转按 Maker 计）
        monthly_points = daily_points * 30 * self.MAKER_VOLUME_WEIGHT / 2.0
        monthly_value = monthly_points * points_value_rate

        # 交易成本：小资金用合约杠杆放大交易量
        # 300U × 5x = 1500U 名义价值，做Maker单
        leverage_mult = min(10, max(5, 50000 / max(account_equity, 1)))  # 资金越小杠杆越高
        daily_volume = account_equity * leverage_mult * 2  # 开+平=2轮
        monthly_volume = daily_volume * 30
        trading_cost = monthly_volume * self.HL_MAKER  # 用Maker单

        net_value = monthly_value - trading_cost

        is_viable = (
            account_equity >= self.MIN_EQUITY
            and net_value > 0
        )

        return StrategyEvaluation(
            strategy_type=RebateStrategyType.S3_POINTS_MINING,
            is_viable=is_viable,
            expected_monthly_value=round(net_value, 2),
            required_volume_usd=monthly_volume,
            risk_score=0.3,
            confidence=0.6,
            details={
                "daily_points": daily_points,
                "points_balance": points_balance,
                "hype_price": hype_price,
                "hype_price_source": hype_price_source,
                "points_value_rate": points_value_rate,
                "points_speculative_discount": self.POINTS_SPECULATIVE_DISCOUNT,
                "maker_volume_weight": self.MAKER_VOLUME_WEIGHT,
                "valuation_speculative": True,
                "monthly_trading_cost": trading_cost,
                "leverage_mult": leverage_mult,
                "daily_volume": daily_volume,
                "source_exchange": "hyperliquid",
                "min_equity": self.MIN_EQUITY,
                "api_automatable": True,
            },
        )

    def build_execution_plan(
        self, size_usd: float, symbol: str = "ETH/USDT:USDT", paper_mode: bool = True
    ) -> Dict[str, Any]:
        """构建积分挖矿执行计划 — 做市模式（挂单+平仓）"""
        return {
            "strategy": "S3",
            "side_a": {
                "exchange": "hyperliquid",
                "symbol": symbol,
                "side": "buy",
                "type": "limit",   # Maker单赚积分
                "size_usd": size_usd,
            },
            "side_b": None,  # 单腿做市，后续定时平仓
            "close_plan": {
                "exchange": "hyperliquid",
                "symbol": symbol,
                "side": "sell",
                "type": "limit",
                "size_usd": size_usd,
            },
            "hold_phase": {
                "total_seconds": 3600,
                "reason": "s3_maker_roundtrip",
            },
            "duration_days": self.SEASON_DURATION_DAYS,
            "paper_mode": paper_mode,
        }
