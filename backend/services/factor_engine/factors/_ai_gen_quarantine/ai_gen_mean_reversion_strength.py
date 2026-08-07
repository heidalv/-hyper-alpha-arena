"""AI因子: 均值回归强度 | 置信:50% | 衡量当前价格相对近期均值的偏离程度，结合超额成交量，识别短期过度延伸后的反转机会。偏离越大、成交量越大，因子越趋向+1（做多）或-1（做空），反之趋向0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MeanReversionStrength(BaseFactor):
    """衡量当前价格相对近期均值的偏离程度，结合超额成交量，识别短期过度延伸后的反转机会。偏离越大、成交量越大，因子越趋向+1（做多）或-1（做空），反之趋向0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mean_reversion_strength",
            name="Mean Reversion Strength",
            display_name="均值回归强度",
            description="衡量当前价格相对近期均值的偏离程度，结合超额成交量，识别短期过度延伸后的反转机会。偏离越大、成交量越大，因子越趋向+1（做多）或-1（做空），反之趋向0。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: DataFrame with columns ['open','high','low','close','volume']
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 计算10日简单均线
        ma10 = close.rolling(10).mean()
        std10 = close.rolling(10).std()
        # Z-score
        z = (close - ma10) / std10.replace(0, np.nan)
        # 成交量放大因子 (当前量/过去10日均量)
        vol_ma10 = volume.rolling(10).mean()
        vol_ratio = volume / vol_ma10.replace(0, np.nan)
        # 均值回归信号: 当|z|>2且vol_ratio>1.5时信号强
        signal = np.sign(z) * (np.abs(z) - 2).clip(0, 4) / 4 * (vol_ratio.clip(0, 3) / 3)
        # 归一化到[-1,1]
        result = signal.clip(-1, 1)
        return result.fillna(0)
