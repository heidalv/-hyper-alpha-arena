"""AI因子: 流动性恶化 | 置信:60% | 捕捉流动性突然下降的迹象，基于Amihud非流动性指标（当日绝对收益率/成交额）的近期异常升高。使用滚动20日的均值与当前值之比，比值大于1.5时预示流动性风险增大，类似'liq_magnet_reversal'亏损模式。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityDeterioration(BaseFactor):
    """捕捉流动性突然下降的迹象，基于Amihud非流动性指标（当日绝对收益率/成交额）的近期异常升高。使用滚动20日的均值与当前值之比，比值大于1.5时预示流动性风险增大，类似'liq_magnet_reversal'亏损模式。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liqdrop",
            name="Liquidity Deterioration",
            display_name="流动性恶化",
            description="捕捉流动性突然下降的迹象，基于Amihud非流动性指标（当日绝对收益率/成交额）的近期异常升高。使用滚动20日的均值与当前值之比，比值大于1.5时预示流动性风险增大，类似'liq_magnet_reversal'亏损模式。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        dollar_vol = data['close'] * data['volume']
        amihud = (data['close'].pct_change().abs()) / (dollar_vol + 1e-10)
        avg_amihud = amihud.rolling(20).mean()
        ratio = amihud / (avg_amihud + 1e-10)
        factor = -1.0 * (ratio - 1.5) / 0.5
        factor = factor.clip(-1.0, 1.0)
        return factor
