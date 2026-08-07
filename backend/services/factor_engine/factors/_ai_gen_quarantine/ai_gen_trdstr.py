"""AI因子: 趋势强度指标 | 置信:65% | 基于价格相对于长期均线的偏离度与ADX的乘积，归一化至[-1,1]。正值表示强上升趋势，负值表示强下降趋势，接近0表示震荡或弱趋势。用于过滤无效突破止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trendstrength(BaseFactor):
    """基于价格相对于长期均线的偏离度与ADX的乘积，归一化至[-1,1]。正值表示强上升趋势，负值表示强下降趋势，接近0表示震荡或弱趋势。用于过滤无效突破止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trdstr",
            name="TrendStrength",
            display_name="趋势强度指标",
            description="基于价格相对于长期均线的偏离度与ADX的乘积，归一化至[-1,1]。正值表示强上升趋势，负值表示强下降趋势，接近0表示震荡或弱趋势。用于过滤无效突破止损。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import numpy as np
        import pandas as pd
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算14周期均线
        ma = close.rolling(14).mean()
        # 计算ADX
        tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        plus_dm = np.where((high - high.shift(1)) > (low.shift(1) - low), high - high.shift(1), 0)
        minus_dm = np.where((low.shift(1) - low) > (high - high.shift(1)), low.shift(1) - low, 0)
        plus_di = 100 * pd.Series(plus_dm).rolling(14).mean() / atr
        minus_di = 100 * pd.Series(minus_dm).rolling(14).mean() / atr
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(14).mean() / 100.0  # 归一化到0-1
        # 价格相对于均线的偏离度，标准化
        z = (close - ma) / (close.rolling(14).std() + 1e-10)
        z_norm = np.clip(z / 3.0, -1, 1)  # 将3倍标准差外的截断
        # 趋势强度 = 方向 * ADX权重
        result = z_norm * adx
        # 填充NaN
        result = result.fillna(0).clip(-1, 1)
        return result
