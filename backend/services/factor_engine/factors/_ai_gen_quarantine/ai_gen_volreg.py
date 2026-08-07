"""AI因子: 波动率状态偏移 | 置信:55% | 计算短期波动率与长期波动率的比值，识别市场从低波动突然扩张或高波动收缩的过渡期，这类时期反转信号易失效。输出正表示短期波动率扩张，负表示收缩，绝对值大表示异常状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatilityregimeshift(BaseFactor):
    """计算短期波动率与长期波动率的比值，识别市场从低波动突然扩张或高波动收缩的过渡期，这类时期反转信号易失效。输出正表示短期波动率扩张，负表示收缩，绝对值大表示异常状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volreg",
            name="VolatilityRegimeShift",
            display_name="波动率状态偏移",
            description="计算短期波动率与长期波动率的比值，识别市场从低波动突然扩张或高波动收缩的过渡期，这类时期反转信号易失效。输出正表示短期波动率扩张，负表示收缩，绝对值大表示异常状态。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 使用日内高开低收计算波动率：以对数收益率代替
        returns = np.log(close / close.shift(1))
        # 短期波动率（5日标准差）
        short_vol = returns.rolling(window=5, min_periods=3).std()
        # 长期波动率（30日标准差）
        long_vol = returns.rolling(window=30, min_periods=10).std()
        # 比值，大于1表示短期波动高于长期，小于1表示低于
        ratio = short_vol / (long_vol + 1e-10)
        # 映射到[-1,1]：log变换后clip
        log_ratio = np.log(ratio)
        result = np.clip(log_ratio / 2.0, -1, 1)  # 假设log_ratio通常在[-2,2]之间
        result = pd.Series(result).fillna(0).values
        return result
