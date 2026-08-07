"""AI因子: 波动率调整均值回复因子 | 置信:70% | 捕捉价格短期内偏离移动平均后回归的倾向，同时考虑近期波动率过滤噪音。使用收盘价与20日简单移动平均的偏离度除以历史波动率，输出[-1,1]正值表示过度偏离后应反向（做空/平多），负值表示趋势延续。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MeanReversionVoladj(BaseFactor):
    """捕捉价格短期内偏离移动平均后回归的倾向，同时考虑近期波动率过滤噪音。使用收盘价与20日简单移动平均的偏离度除以历史波动率，输出[-1,1]正值表示过度偏离后应反向（做空/平多），负值表示趋势延续。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_meanrev",
            name="Mean_Reversion_VolAdj",
            display_name="波动率调整均值回复因子",
            description="捕捉价格短期内偏离移动平均后回归的倾向，同时考虑近期波动率过滤噪音。使用收盘价与20日简单移动平均的偏离度除以历史波动率，输出[-1,1]正值表示过度偏离后应反向（做空/平多），负值表示趋势延续。",
            category="mean_reversion",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算20日均线
        ma20 = data['close'].rolling(20, min_periods=20).mean()
        # 偏离度
        deviation = (data['close'] - ma20) / ma20
        # 历史波动率（20日标准差）
        vol = data['close'].pct_change().rolling(20, min_periods=20).std()
        # 调整：偏离度 / 波动率
        adj_dev = deviation / (vol + 1e-10)
        # 截断并映射到[-1,1]
        adj_dev = adj_dev.clip(-3, 3) / 3
        return adj_dev.fillna(0)
