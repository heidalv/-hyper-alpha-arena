"""AI因子: 流动性弱势信号 | 置信:65% | 当成交量相对于过去20日均量萎缩且价格同时创近期(5日)新高/新低时，表明突破缺乏流动性支持，易反转。计算成交量比和价格极值，当放量创新高时为正，缩量创新高时为负，缩量创新低时为正（下跌耗尽），放量创新低时为负。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Liquidity_Weakness_Signal(BaseFactor):
    """当成交量相对于过去20日均量萎缩且价格同时创近期(5日)新高/新低时，表明突破缺乏流动性支持，易反转。计算成交量比和价格极值，当放量创新高时为正，缩量创新高时为负，缩量创新低时为正（下跌耗尽），放量创新低时为负。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_w",
            name="Liquidity_Weakness_Signal",
            display_name="流动性弱势信号",
            description="当成交量相对于过去20日均量萎缩且价格同时创近期(5日)新高/新低时，表明突破缺乏流动性支持，易反转。计算成交量比和价格极值，当放量创新高时为正，缩量创新高时为负，缩量创新低时为正（下跌耗尽），放量创新低时为负。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 成交量相对20日均值
        vol_ma = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / vol_ma.replace(0, np.nan)
        # 价格相对5日高低点
        high_5 = data['high'].rolling(5).max()
        low_5 = data['low'].rolling(5).min()
        # 创近期新高
        new_high = data['close'] >= high_5.shift(1)
        new_low = data['close'] <= low_5.shift(1)
        # 信号
        signal = np.zeros(len(data))
        # 缩量创新高 -> 假突破 (-1)
        cond_fake_high = new_high & (vol_ratio < 0.8)
        signal[cond_fake_high] = -1
        # 放量创新高 -> 真突破 (+1)
        cond_real_high = new_high & (vol_ratio > 1.5)
        signal[cond_real_high] = 1
        # 缩量创新低 -> 下跌衰竭 (+1)
        cond_fake_low = new_low & (vol_ratio < 0.8)
        signal[cond_fake_low] = 1
        # 放量创新低 -> 真破位 (-1)
        cond_real_low = new_low & (vol_ratio > 1.5)
        signal[cond_real_low] = -1
        # 平滑
        result = pd.Series(signal, index=data.index).rolling(2).mean().fillna(0)
        return result.clip(-1, 1)
