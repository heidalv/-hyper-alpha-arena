"""AI因子: 均值回归失败因子 | 置信:58% | 价格显著偏离20日均线（超过2倍标准差）且布林带带宽收窄，缺乏回归动力，此时做多容易因趋势延续或震荡超时而亏损。因子基于布林带和带宽变化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Mean_Reversion_Confidence_Failure(BaseFactor):
    """价格显著偏离20日均线（超过2倍标准差）且布林带带宽收窄，缺乏回归动力，此时做多容易因趋势延续或震荡超时而亏损。因子基于布林带和带宽变化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mrconf",
            name="Mean Reversion Confidence Failure",
            display_name="均值回归失败因子",
            description="价格显著偏离20日均线（超过2倍标准差）且布林带带宽收窄，缺乏回归动力，此时做多容易因趋势延续或震荡超时而亏损。因子基于布林带和带宽变化。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        ma20 = close.rolling(20, min_periods=20).mean()
        std20 = close.rolling(20, min_periods=20).std()
        zscore = (close - ma20) / (std20 + 1e-10)
        bandwidth = std20 / ma20
        bandwidth_ratio = bandwidth / bandwidth.shift(5)
        # 价格在2倍标准差外且带宽缩小，回归动力弱 -> 负信号
        condition1 = np.abs(zscore) > 2.0
        condition2 = bandwidth_ratio < 0.9
        factor = np.where(condition1 & condition2, -np.clip(np.abs(zscore) - 2, 0, 1), 0)
        return pd.Series(factor, index=data.index)
