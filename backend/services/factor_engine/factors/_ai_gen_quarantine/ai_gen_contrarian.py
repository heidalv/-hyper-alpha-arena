"""AI因子: 均值回归不确定性因子 | 置信:70% | 通过价格相对20日均线的偏离度和近5日K线实体大小判断市场是否处于方向不明确的震荡状态。当价格偏离度小于0.5%且平均实体比例（实体/振幅）低于0.4时，视为无方向震荡，给出负信号；反之若偏离度大且实体显著，则给出正向强趋势信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Mean_Reversion_Uncertainty(BaseFactor):
    """通过价格相对20日均线的偏离度和近5日K线实体大小判断市场是否处于方向不明确的震荡状态。当价格偏离度小于0.5%且平均实体比例（实体/振幅）低于0.4时，视为无方向震荡，给出负信号；反之若偏离度大且实体显著，则给出正向强趋势信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_contrarian",
            name="Mean Reversion Uncertainty",
            display_name="均值回归不确定性因子",
            description="通过价格相对20日均线的偏离度和近5日K线实体大小判断市场是否处于方向不明确的震荡状态。当价格偏离度小于0.5%且平均实体比例（实体/振幅）低于0.4时，视为无方向震荡，给出负信号；反之若偏离度大且实体显著，则给出正向强趋势信号。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        df = data.copy()
        close = df['close']
        ma20 = close.rolling(20).mean()
        deviation = (close - ma20) / ma20  # 百分比偏离
        # 计算实体比例（|close-open|/(high-low)），取5日均值
        body = np.abs(df['close'] - df['open'])
        range = df['high'] - df['low'] + 1e-10
        body_ratio = body / range
        avg_body_ratio = body_ratio.rolling(5).mean()
        # 震荡条件：价格偏离小且实体比例低
        condition = (deviation.abs() < 0.005) & (avg_body_ratio < 0.4)
        # 趋势条件：偏离度大于1.5%且实体比例高于0.6
        trend_up = (deviation > 0.015) & (avg_body_ratio > 0.6)
        trend_down = (deviation < -0.015) & (avg_body_ratio > 0.6)
        signal = np.where(condition, -1.0,
                          np.where(trend_up, 1.0,
                                   np.where(trend_down, -0.5, 0.0)))
        result = pd.Series(signal, index=df.index).fillna(0)
        return result.clip(-1,1)
