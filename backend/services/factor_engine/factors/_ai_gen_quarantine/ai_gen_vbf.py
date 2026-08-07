"""AI因子: 波动率带宽因子 | 置信:65% | 通过布林带宽度与价格位置识别窄幅震荡行情。当带宽极窄且价格位于中轨附近时，市场处于低波动无趋势状态，容易导致追涨杀跌亏损，因子值为负；反之带宽较宽或价格偏离中轨时，趋势明确，因子值为正。使用20日滚动窗口，将当前带宽比分位数映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Band_Factor(BaseFactor):
    """通过布林带宽度与价格位置识别窄幅震荡行情。当带宽极窄且价格位于中轨附近时，市场处于低波动无趋势状态，容易导致追涨杀跌亏损，因子值为负；反之带宽较宽或价格偏离中轨时，趋势明确，因子值为正。使用20日滚动窗口，将当前带宽比分位数映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vbf",
            name="Volatility Band Factor",
            display_name="波动率带宽因子",
            description="通过布林带宽度与价格位置识别窄幅震荡行情。当带宽极窄且价格位于中轨附近时，市场处于低波动无趋势状态，容易导致追涨杀跌亏损，因子值为负；反之带宽较宽或价格偏离中轨时，趋势明确，因子值为正。使用20日滚动窗口，将当前带宽比分位数映射到[-1,1]。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        period = 20
        # 布林带中轨
        sma = data['close'].rolling(window=period).mean()
        std = data['close'].rolling(window=period).std()
        # 带宽 = (上轨-下轨)/中轨
        bandwidth = 4 * std / sma  # 2倍标准差*2 = 4倍
        # 计算带宽的历史分位数（滚动窗口内）
        rank = bandwidth.rolling(window=period).apply(lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min() + 1e-10), raw=False)
        # 映射到[-1,1]: 分位数越高表示波动越大，因子正值
        factor = 2 * rank - 1
        # 价格位置修正：当价格接近中轨且带宽很窄时强化负值
        price_distance = np.abs(data['close'] - sma) / (std + 1e-10)
        # 若价格在0.5倍标准差内且带宽分位数<0.3，则更负
        mask = (price_distance < 0.5) & (rank < 0.3)
        factor[mask] = factor[mask].clip(upper=-0.5)  # 强制更负
        return factor.fillna(0).clip(-1, 1)
