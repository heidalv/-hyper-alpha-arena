"""AI因子: 趋势背离因子 | 置信:60% | 计算短期均线（5日）与长期均线（20日）的方向一致性。当两者方向相反时，市场缺乏明确趋势，容易产生无效信号和亏损，因子输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Divergence_Factor(BaseFactor):
    """计算短期均线（5日）与长期均线（20日）的方向一致性。当两者方向相反时，市场缺乏明确趋势，容易产生无效信号和亏损，因子输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_div",
            name="Trend Divergence Factor",
            display_name="趋势背离因子",
            description="计算短期均线（5日）与长期均线（20日）的方向一致性。当两者方向相反时，市场缺乏明确趋势，容易产生无效信号和亏损，因子输出负值。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        length = len(data)
        if length < 20:
            return pd.Series(np.nan, index=data.index)
        ma_short = data['close'].rolling(5).mean()
        ma_long = data['close'].rolling(20).mean()
        short_dir = ma_short.diff().apply(np.sign)
        long_dir = ma_long.diff().apply(np.sign)
        divergence = (short_dir != long_dir).astype(float) * -1
        # 利用短期均线斜率大小调整强度
        slope = (ma_short - ma_short.shift(1)) / ma_short.shift(1)
        slope = slope.clip(-0.05, 0.05) / 0.05
        result = divergence * (1 - np.abs(slope))
        result = result.clip(-1, 1)
        return result.fillna(0)
