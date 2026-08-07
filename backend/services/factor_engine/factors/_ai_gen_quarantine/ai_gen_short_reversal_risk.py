"""AI因子: 空头反转风险因子 | 置信:60% | 基于价格急速下跌后的成交量异常放大，识别流动性磁铁反转风险。当价格在短期内大幅下跌且成交量激增时，空头容易遭受反转打击。因子值越高，反转风险越大。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Shortreversalrisk(BaseFactor):
    """基于价格急速下跌后的成交量异常放大，识别流动性磁铁反转风险。当价格在短期内大幅下跌且成交量激增时，空头容易遭受反转打击。因子值越高，反转风险越大。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_reversal_risk",
            name="ShortReversalRisk",
            display_name="空头反转风险因子",
            description="基于价格急速下跌后的成交量异常放大，识别流动性磁铁反转风险。当价格在短期内大幅下跌且成交量激增时，空头容易遭受反转打击。因子值越高，反转风险越大。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: pd.DataFrame with columns open, high, low, close, volume
        # 计算短期价格变化率（3周期）
        short_ret = data['close'].pct_change(3)
        # 计算成交量相对均值变化（3周期均值比当前）
        vol_ratio = data['volume'] / data['volume'].rolling(3).mean()
        # 识别快速下跌（short_ret < -0.02）且成交量放大（vol_ratio > 1.5）
        condition = (short_ret < -0.02) & (vol_ratio > 1.5)
        # 信号强度：下跌幅度 * 成交量放大倍数，归一化到[-1,1]
        raw = -short_ret * (vol_ratio - 1) * 100
        raw = raw.clip(-1, 1)
        # 仅在条件成立时输出，否则0
        result = raw.where(condition, 0.0)
        return result.fillna(0.0)
