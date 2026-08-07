"""AI因子: 趋势置信度因子 | 置信:65% | 结合价格与多条移动平均的排列顺序以及动能方向，判断当前趋势的可靠程度。亏损案例中许多在regime=unknown下逆势操作，高置信度趋势信号可避免在无趋势时交易。值接近+1表示强趋势(顺向机会)，接近-1表示强反趋势或震荡(风险)。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendConfidenceScore(BaseFactor):
    """结合价格与多条移动平均的排列顺序以及动能方向，判断当前趋势的可靠程度。亏损案例中许多在regime=unknown下逆势操作，高置信度趋势信号可避免在无趋势时交易。值接近+1表示强趋势(顺向机会)，接近-1表示强反趋势或震荡(风险)。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendconf",
            name="Trend Confidence Score",
            display_name="趋势置信度因子",
            description="结合价格与多条移动平均的排列顺序以及动能方向，判断当前趋势的可靠程度。亏损案例中许多在regime=unknown下逆势操作，高置信度趋势信号可避免在无趋势时交易。值接近+1表示强趋势(顺向机会)，接近-1表示强反趋势或震荡(风险)。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        ma20 = close.rolling(20).mean()
        ma50 = close.rolling(50).mean()
        ma100 = close.rolling(100).mean()
        # 趋势排列得分：多头排列+1，空头排列-1
        align = np.sign((close - ma20) + (ma20 - ma50) + (ma50 - ma100))
        align = align.replace(0, np.nan)
        # 动能一致性：比较短期回报与中期回报方向
        ret_short = close.pct_change(5)
        ret_mid = close.pct_change(20)
        momentum_alignment = np.sign(ret_short) * np.sign(ret_mid)
        momentum_alignment = momentum_alignment.replace(0, 0.5)  # 中性处理
        # 结合，使用加权平均
        result = align * 0.6 + momentum_alignment * 0.4
        return result.fillna(0).clip(-1, 1)
