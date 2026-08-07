"""AI因子: 波动状态差异 | 置信:60% | 通过短期波动率与长期波动率的比值，结合近期价格位置，检测市场是否为低效未知状态。当短期波动率显著低于长期且价格窄幅震荡时，风险较高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Regime_Disparity(BaseFactor):
    """通过短期波动率与长期波动率的比值，结合近期价格位置，检测市场是否为低效未知状态。当短期波动率显著低于长期且价格窄幅震荡时，风险较高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknown_vol",
            name="Volatility Regime Disparity",
            display_name="波动状态差异",
            description="通过短期波动率与长期波动率的比值，结合近期价格位置，检测市场是否为低效未知状态。当短期波动率显著低于长期且价格窄幅震荡时，风险较高。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: DataFrame with columns open, high, low, close, volume
        import pandas as pd
        import numpy as np
        # 计算短期和长期真实波幅
        high = data['high']
        low = data['low']
        close = data['close']
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        short_tr = tr.rolling(window=5).mean()
        long_tr = tr.rolling(window=20).mean()
        # 波动率比值，当短期远小于长期时比值小，代表市场沉寂
        ratio = short_tr / (long_tr + 1e-10)
        # 同时价格位置处于近期中位附近（无方向）
        rolling_high = high.rolling(20).max()
        rolling_low = low.rolling(20).min()
        price_position = (close - rolling_low) / (rolling_high - rolling_low + 1e-10)
        # 当ratio<0.5且price_position在0.4-0.6之间时，视为未知状态风险高
        risk = ((ratio < 0.5) & (price_position.between(0.4, 0.6))).astype(float)
        risk = risk * 2 - 1  # 映射到[-1,1]，1表示高风险
        return risk.fillna(0)
