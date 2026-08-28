"""AI因子: 做空超时成交量交互 | 置信:55% | 针对做空单max_hold_timeout亏损模式：当价格处于短期均线下方且成交量萎缩时，做空持仓容易因时间耗尽而亏损。该因子捕捉价格弱势但成交量不足的区间，值越接近+1表示越适合做空（但实际应反向使用），越接近-1表示越不适合做空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortTimeoutVolumeInteraction(BaseFactor):
    """针对做空单max_hold_timeout亏损模式：当价格处于短期均线下方且成交量萎缩时，做空持仓容易因时间耗尽而亏损。该因子捕捉价格弱势但成交量不足的区间，值越接近+1表示越适合做空（但实际应反向使用），越接近-1表示越不适合做空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_timeout_vol",
            name="Short_Timeout_Volume_Interaction",
            display_name="做空超时成交量交互",
            description="针对做空单max_hold_timeout亏损模式：当价格处于短期均线下方且成交量萎缩时，做空持仓容易因时间耗尽而亏损。该因子捕捉价格弱势但成交量不足的区间，值越接近+1表示越适合做空（但实际应反向使用），越接近-1表示越不适合做空。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        price_pos = (data['close'] - data['close'].rolling(20).mean()) / (data['close'].rolling(20).std() + 1e-9)
        result = (ret * -1 + price_pos * 0.5 - (vol_ratio - 1) * 0.5).clip(-1, 1)
        return result
