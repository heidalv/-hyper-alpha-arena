"""AI因子: 市场混沌指数 | 置信:65% | 通过比较三个不同时间尺度移动平均线的斜率方向，判断市场是否处于方向不明的混沌状态。当三条均线斜率方向不一致（如多空分歧）时，因子值为负，预示容易发生意外亏损；当方向一致时因子值为正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeChaosIndex(BaseFactor):
    """通过比较三个不同时间尺度移动平均线的斜率方向，判断市场是否处于方向不明的混沌状态。当三条均线斜率方向不一致（如多空分歧）时，因子值为负，预示容易发生意外亏损；当方向一致时因子值为正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_chaos",
            name="Regime Chaos Index",
            display_name="市场混沌指数",
            description="通过比较三个不同时间尺度移动平均线的斜率方向，判断市场是否处于方向不明的混沌状态。当三条均线斜率方向不一致（如多空分歧）时，因子值为负，预示容易发生意外亏损；当方向一致时因子值为正。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
    
        # 计算三个周期的移动平均线
        ma_short = close.rolling(window=5, min_periods=3).mean()
        ma_mid = close.rolling(window=20, min_periods=10).mean()
        ma_long = close.rolling(window=60, min_periods=30).mean()
    
        # 计算斜率（一阶差分，表示方向）
        slope_short = ma_short.diff(1)  # >0 向上
        slope_mid = ma_mid.diff(1)
        slope_long = ma_long.diff(1)
    
        # 方向符号
        sign_short = np.sign(slope_short)
        sign_mid = np.sign(slope_mid)
        sign_long = np.sign(slope_long)
    
        # 统计一致数量：三个符号相同则为3，两个相同则为2，全不同则为1
        # 用绝对值求和，但注意可能有0，将0视为无方向，做特殊处理
        # 简单方法：三个符号的和的绝对值，值越大表示方向越一致
        sum_sign = sign_short + sign_mid + sign_long  # 范围-3~3
        # 当sum_sign为+3或-3时一致，+1/-1时混乱，0时也混乱
        # 映射到[-1,1]: 用 (|sum_sign|/3 - 1) 的负值？更直观：一致时正，混乱时负
        # 例如 sum_sign=3 -> 1, sum_sign=1 -> -0.33? 我们想要一致时+1，不一致时-1
        # 使用：1 - 2*(1 - |sum_sign|/3) 是错误的。改用：2*(|sum_sign|/3) - 1  范围[-1,1]
        raw = 2.0 * (np.abs(sum_sign) / 3.0) - 1.0
    
        # 若有0方向（即某斜率为0），可修正：若存在0则风险更大，降低raw
        zero_mask = (sign_short == 0) | (sign_mid == 0) | (sign_long == 0)
        raw = np.where(zero_mask, raw - 0.2, raw)  # 额外惩罚
    
        # 用ATR调整：高波动时惩罚更重
        atr = data['high'].rolling(14).max() - data['low'].rolling(14).min()
        atr_norm = atr / close.rolling(14).mean()
        atr_factor = 1 - atr_norm * 5  # 当ATR大时因子变小
        raw = raw * np.clip(atr_factor, 0.5, 1.0)  # 限制幅度
    
        result = np.clip(raw, -1, 1)
        result = result.fillna(0)
        return result
