"""AI因子: 插针密度与动量交互 | 置信:58% | 以影线相对实体的比例衡量插针密度环境（波动/情绪剧烈度），与短期动量方向交互。高插针密度环境下动量更易反转，低密度环境下动量延续。因子值高表示在剧烈插针环境中短期动量偏空（预期回落），低表示偏多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityMomentumInteraction(BaseFactor):
    """以影线相对实体的比例衡量插针密度环境（波动/情绪剧烈度），与短期动量方向交互。高插针密度环境下动量更易反转，低密度环境下动量延续。因子值高表示在剧烈插针环境中短期动量偏空（预期回落），低表示偏多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_mom_interact",
            name="Pin Density Momentum Interaction",
            display_name="插针密度与动量交互",
            description="以影线相对实体的比例衡量插针密度环境（波动/情绪剧烈度），与短期动量方向交互。高插针密度环境下动量更易反转，低密度环境下动量延续。因子值高表示在剧烈插针环境中短期动量偏空（预期回落），低表示偏多。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        pin = (data[['open','close']].max(axis=1) - data[['open','close']].min(axis=1))
        density = (np.maximum(upper, lower) / body).rolling(20).mean()
        mom = data['close'].pct_change(5)
        result = (-mom * density).clip(-1, 1)
        return result
