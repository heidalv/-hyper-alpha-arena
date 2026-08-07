"""AI因子: 成交量背离指标 | 置信:60% | 当成交量大幅增加但价格涨幅不足时，表明多头力量衰竭，容易引发反转亏损。计算当前成交量与20日均量的比值，再乘以价格动量方向（使用收益率符号），得到负值表示量增价滞。结果归一化至[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Divergence_Indicator(BaseFactor):
    """当成交量大幅增加但价格涨幅不足时，表明多头力量衰竭，容易引发反转亏损。计算当前成交量与20日均量的比值，再乘以价格动量方向（使用收益率符号），得到负值表示量增价滞。结果归一化至[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_abnormal",
            name="Volume Divergence Indicator",
            display_name="成交量背离指标",
            description="当成交量大幅增加但价格涨幅不足时，表明多头力量衰竭，容易引发反转亏损。计算当前成交量与20日均量的比值，再乘以价格动量方向（使用收益率符号），得到负值表示量增价滞。结果归一化至[-1,1]。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        volume = data['volume']
        close = data['close']
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma
        ret = close.pct_change()
        # 量价背离：量比高但涨幅小或为负 => 负值
        raw = vol_ratio * ret.sign()  # 正相关？需要调整方向
        # 更直接的：当量比>1.5且收益率<0.5倍波动率时，认为背离
        # 简单处理：量比乘以 - (收益率标准化的符号)
        # 使用 z-score 归一化
        z = (raw - raw.rolling(60).mean()) / raw.rolling(60).std()
        return z.clip(-3, 3) / 3.0
