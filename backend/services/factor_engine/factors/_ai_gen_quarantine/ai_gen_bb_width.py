"""AI因子: 布林带宽度状态因子 | 置信:60% | 基于布林带带宽（上轨-下轨）/中轨的标准化值，判断市场处于趋势还是震荡。带宽扩大表示高波动趋势环境，带宽收窄表示震荡环境。通过将带宽的Z-score压缩到[-1,1]，正值为趋势适宜，负值为震荡风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class BollingerBandWidthRegime(BaseFactor):
    """基于布林带带宽（上轨-下轨）/中轨的标准化值，判断市场处于趋势还是震荡。带宽扩大表示高波动趋势环境，带宽收窄表示震荡环境。通过将带宽的Z-score压缩到[-1,1]，正值为趋势适宜，负值为震荡风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bb_width",
            name="Bollinger Band Width Regime",
            display_name="布林带宽度状态因子",
            description="基于布林带带宽（上轨-下轨）/中轨的标准化值，判断市场处于趋势还是震荡。带宽扩大表示高波动趋势环境，带宽收窄表示震荡环境。通过将带宽的Z-score压缩到[-1,1]，正值为趋势适宜，负值为震荡风险。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算布林带 (20日, 2倍标准差)
        ma = data['close'].rolling(window=20, min_periods=1).mean()
        std = data['close'].rolling(window=20, min_periods=1).std()
        upper = ma + 2 * std
        lower = ma - 2 * std
        # 带宽 = (upper - lower) / ma
        bandwidth = (upper - lower) / ma
        # 计算带宽的Z-score (相对于过去60期)
        mean_bw = bandwidth.rolling(window=60, min_periods=1).mean()
        std_bw = bandwidth.rolling(window=60, min_periods=1).std()
        zscore = (bandwidth - mean_bw) / std_bw
        # 用tanh压缩到[-1,1]
        result = np.tanh(zscore * 0.5)
        # 处理前期NaN
        result = result.fillna(method='bfill')
        return pd.Series(result, index=data.index)
