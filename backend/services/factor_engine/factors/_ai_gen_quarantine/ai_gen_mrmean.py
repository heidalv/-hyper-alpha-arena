"""AI因子: 均值回归偏离度 | 置信:60% | 计算收盘价相对于其长短期移动平均线的标准化偏离程度，并利用布林带宽度调整。当价格大幅偏离均值且波动率较小时，可能出现假突破风险，适合反向操作。该因子旨在识别regime=unknown中的超买/超卖状态，值域[-1,1]，负值表示超买（可能下跌），正值表示超卖（可能上涨）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Mean_Reversion_Distance(BaseFactor):
    """计算收盘价相对于其长短期移动平均线的标准化偏离程度，并利用布林带宽度调整。当价格大幅偏离均值且波动率较小时，可能出现假突破风险，适合反向操作。该因子旨在识别regime=unknown中的超买/超卖状态，值域[-1,1]，负值表示超买（可能下跌），正值表示超卖（可能上涨）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mrmean",
            name="Mean Reversion Distance",
            display_name="均值回归偏离度",
            description="计算收盘价相对于其长短期移动平均线的标准化偏离程度，并利用布林带宽度调整。当价格大幅偏离均值且波动率较小时，可能出现假突破风险，适合反向操作。该因子旨在识别regime=unknown中的超买/超卖状态，值域[-1,1]，负值表示超买（可能下跌），正值表示超卖（可能上涨）。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        short_ma = close.rolling(10).mean()
        long_ma = close.rolling(30).mean()
        std = close.rolling(20).std()
        # 标准化偏离： (close - long_ma) / std
        z_score = (close - long_ma) / std.replace(0, 1e-10)
        # 同时考虑短期相对于长期的偏离
        short_z = (short_ma - long_ma) / std.replace(0, 1e-10)
        # 综合信号：短期和长期偏离的均值
        combined = (z_score * 0.5 + short_z * 0.5)
        # 使用sigmoid压缩到[-1,1]
        raw = 2 / (1 + np.exp(-combined)) - 1
        # 当波动率极低时，减弱信号（避免盘整中的假信号）
        vol_ratio = std / close.mean() * 100
        weight = 1 - np.exp(-vol_ratio * 5)  # vol_ratio大则权重高
        result = raw * weight
        return result.clip(-1, 1)
