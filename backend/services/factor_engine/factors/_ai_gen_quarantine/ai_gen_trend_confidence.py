"""AI因子: 趋势信心因子 | 置信:70% | 结合价格动量与波动率，衡量当前趋势的可靠程度。强趋势且波动率适中时因子接近+1；弱趋势或高波动时接近-1。旨在避免在无趋势或震荡行情中持仓过久（如max_hold_timeout）或被迫止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend Confidence Factor(BaseFactor):
    """结合价格动量与波动率，衡量当前趋势的可靠程度。强趋势且波动率适中时因子接近+1；弱趋势或高波动时接近-1。旨在避免在无趋势或震荡行情中持仓过久（如max_hold_timeout）或被迫止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_confidence",
            name="Trend Confidence Factor",
            display_name="趋势信心因子",
            description="结合价格动量与波动率，衡量当前趋势的可靠程度。强趋势且波动率适中时因子接近+1；弱趋势或高波动时接近-1。旨在避免在无趋势或震荡行情中持仓过久（如max_hold_timeout）或被迫止损。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            N = 14
            close = data['close']
            # 动量：过去N日收益率
            momentum = close.pct_change(N)
            # 波动率：过去N日年化波动率（日收益率标准差）
            daily_ret = close.pct_change()
            volatility = daily_ret.rolling(N).std() * np.sqrt(252)
            # 标准化动量（用滚动均值与标准差）
            mom_mean = momentum.rolling(60).mean()
            mom_std = momentum.rolling(60).std() + 1e-10
            mom_z = (momentum - mom_mean) / mom_std
            # 波动率调节：高波动压制信心。波动率分位数
            vol_rank = volatility.rolling(60).rank(pct=True)
            # 信心分数：动量z-score * (1 - vol_rank) ，再映射到[-1,1]
            confidence = mom_z * (1 - vol_rank)
            result = np.clip(confidence, -1, 1)
            return result
