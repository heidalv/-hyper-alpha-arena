"""AI因子: 流动性枯竭指标 | 置信:65% | 通过成交量萎缩和价格振幅异常放大来度量流动性枯竭程度。当成交量低且价格波动大时，因子接近+1（流动性枯竭高风险），反之接近-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityDrynessIndicator(BaseFactor):
    """通过成交量萎缩和价格振幅异常放大来度量流动性枯竭程度。当成交量低且价格波动大时，因子接近+1（流动性枯竭高风险），反之接近-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liquidity_dry",
            name="Liquidity Dryness Indicator",
            display_name="流动性枯竭指标",
            description="通过成交量萎缩和价格振幅异常放大来度量流动性枯竭程度。当成交量低且价格波动大时，因子接近+1（流动性枯竭高风险），反之接近-1。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算价格振幅
        amplitude = (data['high'] - data['low']) / data['close']
        # 成交量相对20日均量的比率
        vol_ma20 = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / vol_ma20
        # 逆向指标：振幅大且成交量低 => 流动性差
        liquidity_score = amplitude / amplitude.rolling(20).mean() - vol_ratio
        # 标准化
        liquidity_score = liquidity_score / liquidity_score.abs().rolling(20).max().replace(0, np.nan)
        liquidity_score = liquidity_score.clip(-1, 1)
        return liquidity_score.fillna(0)
