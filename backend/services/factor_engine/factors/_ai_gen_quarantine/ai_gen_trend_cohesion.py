"""AI因子: 价格趋势一致性 | 置信:55% | 量化日内价格运动的线性度。通过计算收盘价相对于日内高低点的位置与日内涨跌幅的关系，判断趋势是否一致。当趋势一致时，收盘价趋向于极值方向；当价格来回震荡时，收盘价在中间。输出[-1,1]，正值表示趋势一致适合做多，负值表示震荡市应避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Cohesion(BaseFactor):
    """量化日内价格运动的线性度。通过计算收盘价相对于日内高低点的位置与日内涨跌幅的关系，判断趋势是否一致。当趋势一致时，收盘价趋向于极值方向；当价格来回震荡时，收盘价在中间。输出[-1,1]，正值表示趋势一致适合做多，负值表示震荡市应避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_cohesion",
            name="Trend Cohesion",
            display_name="价格趋势一致性",
            description="量化日内价格运动的线性度。通过计算收盘价相对于日内高低点的位置与日内涨跌幅的关系，判断趋势是否一致。当趋势一致时，收盘价趋向于极值方向；当价格来回震荡时，收盘价在中间。输出[-1,1]，正值表示趋势一致适合做多，负值表示震荡市应避免做多。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 日内价格位置：收盘价在高低区间内的相对位置 (0~1)
        hl_range = data['high'] - data['low'] + 1e-10
        position = (data['close'] - data['low']) / hl_range
        # 日内涨跌幅
        ret = data['close'].pct_change()
        # 一致性：如果上涨且收盘在高位，或下跌且收盘在低位，则一致
        cohesion = np.where(
            (ret > 0) & (position > 0.7),
            1.0,
            np.where(
                (ret < 0) & (position < 0.3),
                1.0,
                -1.0
            )
        )
        # 平滑处理，使用滚动均值
        result = pd.Series(cohesion).rolling(5).mean().fillna(0)
        return result * 0.5  # 缩放以控制幅度
