"""AI因子: 动量衰减因子 | 置信:50% | 衡量短期动量衰减速度。计算过去短周期（如3）与长周期（如10）收益率之差，结合成交量加权。正衰减表示上涨动能减弱可能反转，负衰减表示下跌动能减弱。针对master_running_close和sl模式下的趋势衰竭。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumDecay(BaseFactor):
    """衡量短期动量衰减速度。计算过去短周期（如3）与长周期（如10）收益率之差，结合成交量加权。正衰减表示上涨动能减弱可能反转，负衰减表示下跌动能减弱。针对master_running_close和sl模式下的趋势衰竭。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momdecay",
            name="Momentum Decay",
            display_name="动量衰减因子",
            description="衡量短期动量衰减速度。计算过去短周期（如3）与长周期（如10）收益率之差，结合成交量加权。正衰减表示上涨动能减弱可能反转，负衰减表示下跌动能减弱。针对master_running_close和sl模式下的趋势衰竭。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: DataFrame with columns open, high, low, close, volume
        short_ret = data['close'].pct_change(3)
        long_ret = data['close'].pct_change(10)
        # 动量差
        mom_diff = short_ret - long_ret
        # 成交量调整：用volume的短期变化加强信号
        vol_short = data['volume'].pct_change(3)
        vol_long = data['volume'].pct_change(10)
        vol_factor = np.sign(vol_short - vol_long)  # 成交量加速上升给正向权重
        raw = mom_diff * vol_factor
        # 标准化到[-1,1]
        result = np.clip(raw * 10, -1, 1)  # 乘10使常见范围在[-1,1]内
        return result
