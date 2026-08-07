"""AI因子: 多头风险评分因子 | 置信:50% | 综合市场波动率、趋势强度、成交量萎缩程度和价格位置，评估当前做多风险。当波动率上升但趋势不明、成交量萎缩且价格处于近期高位时，做多易触发止损。输出负值表示高风险，正值表示低风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Long_Position_Risk_Score(BaseFactor):
    """综合市场波动率、趋势强度、成交量萎缩程度和价格位置，评估当前做多风险。当波动率上升但趋势不明、成交量萎缩且价格处于近期高位时，做多易触发止损。输出负值表示高风险，正值表示低风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_long_risk",
            name="Long Position Risk Score",
            display_name="多头风险评分因子",
            description="综合市场波动率、趋势强度、成交量萎缩程度和价格位置，评估当前做多风险。当波动率上升但趋势不明、成交量萎缩且价格处于近期高位时，做多易触发止损。输出负值表示高风险，正值表示低风险。",
            category="composite",
            subcategory="risk",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        # 价格位置：当前close相对于20日高低的百分位
        low_20 = low.rolling(20).min()
        high_20 = high.rolling(20).max()
        position = (close - low_20) / (high_20 - low_20 + 1e-10)
        # 波动率：20日年化波动率
        ret = close.pct_change()
        vol_20 = ret.rolling(20).std() * np.sqrt(20)
        # 趋势强度：使用ADX简化版（用线性回归斜率代替）
        def trend_strength(series):
            x = np.arange(len(series))
            slope, _ = np.polyfit(x, series, 1)
            return slope
        slope_10 = close.rolling(10).apply(lambda s: trend_strength(s.values), raw=False)
        trend_abs = abs(slope_10)
        # 成交量萎缩：当前量相对于20日均量的比例，低于0.8为萎缩
        vol_ma20 = volume.rolling(20).mean()
        vol_ratio = volume / (vol_ma20 + 1e-10)
        # 风险评分：位置高(>0.8) + 波动率大(>0.05) + 趋势弱(trend_abs<0.5) + 量萎
        high_pos = (position > 0.8).astype(float)
        high_vol = (vol_20 > 0.05).astype(float)
        weak_trend = (trend_abs < 0.5).astype(float)
        low_vol = (vol_ratio < 0.8).astype(float)
        # 加权综合
        risk = high_pos * 0.3 + high_vol * 0.3 + weak_trend * 0.2 + low_vol * 0.2
        # 映射到-1~1，高风险负值
        result = 1 - 2 * risk.clip(0, 1)
        return result
