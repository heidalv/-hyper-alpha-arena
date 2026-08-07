"""AI因子: 趋势强度波动率比 | 置信:65% | 计算ADX与ATR的比率，衡量趋势的明确性。当趋势强度（ADX）相对于波动率（ATR）较小时，市场处于混沌状态（regime=unknown），容易发生亏损。因子值负向指示此类风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStrengthVolatilityRatio(BaseFactor):
    """计算ADX与ATR的比率，衡量趋势的明确性。当趋势强度（ADX）相对于波动率（ATR）较小时，市场处于混沌状态（regime=unknown），容易发生亏损。因子值负向指示此类风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trndvol",
            name="TrendStrengthVolatilityRatio",
            display_name="趋势强度波动率比",
            description="计算ADX与ATR的比率，衡量趋势的明确性。当趋势强度（ADX）相对于波动率（ATR）较小时，市场处于混沌状态（regime=unknown），容易发生亏损。因子值负向指示此类风险。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            import numpy as np
            high, low, close = data['high'], data['low'], data['close']
            # ATR (14)
            tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
            atr = tr.rolling(14).mean()
            # ADX (14)
            up = high - high.shift(1)
            dn = low.shift(1) - low
            plus_dm = np.where((up > dn) & (up > 0), up, 0)
            minus_dm = np.where((dn > up) & (dn > 0), dn, 0)
            tr_smooth = tr.rolling(14).mean()
            plus_di = 100 * plus_dm.rolling(14).mean() / tr_smooth
            minus_di = 100 * minus_dm.rolling(14).mean() / tr_smooth
            dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
            adx = dx.rolling(14).mean()
            # 标准化比率，避免除零
            ratio = adx / (atr / close * 100 + 1e-10)
            # 映射到[-1,1]，比率越高趋势越强，输出正；反之输出负
            norm = (ratio - ratio.rolling(100).mean()) / (ratio.rolling(100).std() + 1e-10)
            result = np.clip(norm, -3, 3) / 3.0
            return result
