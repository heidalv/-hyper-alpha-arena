"""AI因子: 波动率状态比 | 置信:55% | 通过比较近期波动率与历史波动率的比率，识别当前市场处于高波动还是低波动状态。高波动往往伴随趋势行情，低波动则容易产生假突破和损耗。当波动率比率处于适中水平（1附近）时信号偏正，过大或过小则信号偏负，以规避震荡或极端噪声。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Regime_Ratio(BaseFactor):
    """通过比较近期波动率与历史波动率的比率，识别当前市场处于高波动还是低波动状态。高波动往往伴随趋势行情，低波动则容易产生假突破和损耗。当波动率比率处于适中水平（1附近）时信号偏正，过大或过小则信号偏负，以规避震荡或极端噪声。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volr",
            name="Volatility Regime Ratio",
            display_name="波动率状态比",
            description="通过比较近期波动率与历史波动率的比率，识别当前市场处于高波动还是低波动状态。高波动往往伴随趋势行情，低波动则容易产生假突破和损耗。当波动率比率处于适中水平（1附近）时信号偏正，过大或过小则信号偏负，以规避震荡或极端噪声。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        # 计算每日收益率
        ret = close.pct_change()
        # 近期波动率：20日滚动标准差
        vol_short = ret.rolling(20).std()
        # 长期波动率：60日滚动标准差
        vol_long = ret.rolling(60).std()
        # 波动率比率，加小值防止除零
        ratio = vol_short / (vol_long + 1e-10)
        # 理想区间[0.8,1.2]，映射到[-1,1]: 1.0对应0，偏离越远越负
        # 使用钟形函数: exp(-((ratio-1)/sigma)^2) 映射到[0,1]再线性到[-1,1]
        sigma = 0.3
        score = np.exp(-((ratio - 1) / sigma) ** 2)
        result = pd.Series(2 * score - 1, index=close.index)
        return result.fillna(0.0)
