"""AI因子: 波动率不对称性 | 置信:55% | 分别计算过去N日上涨日和下跌日的平均波动幅度，若上涨波动显著大于下跌波动且价格处于高位，表明存在诱多陷阱；反之若下跌波动更大则空头主导。在不对称性高时趋势不健康。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Asymmetry(BaseFactor):
    """分别计算过去N日上涨日和下跌日的平均波动幅度，若上涨波动显著大于下跌波动且价格处于高位，表明存在诱多陷阱；反之若下跌波动更大则空头主导。在不对称性高时趋势不健康。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_asym",
            name="Volatility Asymmetry",
            display_name="波动率不对称性",
            description="分别计算过去N日上涨日和下跌日的平均波动幅度，若上涨波动显著大于下跌波动且价格处于高位，表明存在诱多陷阱；反之若下跌波动更大则空头主导。在不对称性高时趋势不健康。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 14
        # 判断每日涨跌
        up = (data['close'] > data['close'].shift(1)).astype(bool)
        # 日内波动幅度：high-low
        amp = data['high'] - data['low']
        # 分别计算上涨日和下跌日的平均振幅
        up_amp = amp.where(up).rolling(n).mean()
        down_amp = amp.where(~up).rolling(n).mean()
        # 避免除零
        diff = (up_amp - down_amp) / (up_amp + down_amp + 1e-10)  # 范围[-1,1]
        # 结合价格位置：若价格在近期高位且上涨波动大，则为负值（警告做多风险）
        price_position = (data['close'] - data['close'].rolling(n).min()) / (data['close'].rolling(n).max() - data['close'].rolling(n).min() + 1e-10)
        # 综合：波动不对称性乘以价格位置，当价格高且上涨波动大时给出强烈负信号
        result = diff * (1 - 2 * price_position)  # 价格在0-1，乘后拉向负值
        result = result.clip(-1,1)
        return result.fillna(0)
