"""AI因子: 趋势Z分数 | 置信:70% | 计算收盘价相对于20周期移动平均的Z分数（标准差倍数），用于衡量价格偏离均值的程度。正值表示价格高于均值（潜在上升趋势），负值表示低于均值（潜在下降趋势），接近0表示震荡。通过tanh压缩至[-1,1]区间，避免极端值影响。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendZScore(BaseFactor):
    """计算收盘价相对于20周期移动平均的Z分数（标准差倍数），用于衡量价格偏离均值的程度。正值表示价格高于均值（潜在上升趋势），负值表示低于均值（潜在下降趋势），接近0表示震荡。通过tanh压缩至[-1,1]区间，避免极端值影响。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_zscore",
            name="Trend Z-Score",
            display_name="趋势Z分数",
            description="计算收盘价相对于20周期移动平均的Z分数（标准差倍数），用于衡量价格偏离均值的程度。正值表示价格高于均值（潜在上升趋势），负值表示低于均值（潜在下降趋势），接近0表示震荡。通过tanh压缩至[-1,1]区间，避免极端值影响。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        ma = close.rolling(20, min_periods=20).mean()
        std = close.rolling(20, min_periods=20).std()
        zscore = (close - ma) / std
        # 使用tanh将zscore映射到[-1,1]，限制极端值影响
        result = np.tanh(zscore)
        return result
