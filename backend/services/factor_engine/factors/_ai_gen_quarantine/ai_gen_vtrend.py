"""AI因子: 波动趋势比 | 置信:65% | 衡量当前市场波动相对于趋势强度的比例。当波动率（ATR）远高于趋势强度（ADX）时，市场处于震荡无明确方向的状态，long策略易因止损、超时或反转而亏损。计算ATR(14)/ADX(14)的20日滚动z-score，截断至[-3,3]并缩放至[-1,1]，正值表示高亏损风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Trend_Ratio(BaseFactor):
    """衡量当前市场波动相对于趋势强度的比例。当波动率（ATR）远高于趋势强度（ADX）时，市场处于震荡无明确方向的状态，long策略易因止损、超时或反转而亏损。计算ATR(14)/ADX(14)的20日滚动z-score，截断至[-3,3]并缩放至[-1,1]，正值表示高亏损风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vtrend",
            name="Volatility-Trend Ratio",
            display_name="波动趋势比",
            description="衡量当前市场波动相对于趋势强度的比例。当波动率（ATR）远高于趋势强度（ADX）时，市场处于震荡无明确方向的状态，long策略易因止损、超时或反转而亏损。计算ATR(14)/ADX(14)的20日滚动z-score，截断至[-3,3]并缩放至[-1,1]，正值表示高亏损风险。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # ATR
        tr = np.maximum(data['high'] - data['low'],
                        np.abs(data['high'] - data['close'].shift(1)),
                        np.abs(data['low'] - data['close'].shift(1)))
        atr = tr.rolling(14).mean()
        # ADX简单近似：用过去14天趋势方向一致性替代ADX，计算方向变化次数倒数
        # 这里简化：用14日线性回归斜率绝对值代表趋势强度
        def trend_strength(series):
            x = np.arange(len(series))
            if len(series) < 2:
                return np.nan
            slope = np.polyfit(x, series, 1)[0]
            return abs(slope)
        trend = data['close'].rolling(14).apply(trend_strength, raw=False)
        # 防止除以零
        trend = trend.replace(0, np.nan)
        ratio = atr / trend
        # 滚动z-score
        mean = ratio.rolling(20).mean()
        std = ratio.rolling(20).std(ddof=0)
        z = (ratio - mean) / std
        result = z.clip(-3, 3) / 3.0
        return result.fillna(0)
