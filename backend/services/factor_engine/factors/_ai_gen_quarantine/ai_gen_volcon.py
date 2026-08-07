"""AI因子: 波动一致性因子 | 置信:65% | 量化价格波动与历史波动率的偏离程度，结合方向一致性。当价格变动方向与波动率扩张方向不一致时，视为异常，因子值负向；否则正向。旨在识别regime=unknown下的无序波动。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityConsistencyFactor(BaseFactor):
    """量化价格波动与历史波动率的偏离程度，结合方向一致性。当价格变动方向与波动率扩张方向不一致时，视为异常，因子值负向；否则正向。旨在识别regime=unknown下的无序波动。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volcon",
            name="Volatility Consistency Factor",
            display_name="波动一致性因子",
            description="量化价格波动与历史波动率的偏离程度，结合方向一致性。当价格变动方向与波动率扩张方向不一致时，视为异常，因子值负向；否则正向。旨在识别regime=unknown下的无序波动。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算20日滚动波动率（使用收盘价对数收益率）
        ret = np.log(data['close'] / data['close'].shift(1))
        hist_vol = ret.rolling(20).std()
        # 最近5日波动率变化
        curr_vol = ret.rolling(5).std()
        vol_change = (curr_vol - hist_vol) / (hist_vol + 1e-10)
        # 价格方向：最近5日累计收益率
        price_dir = (data['close'] / data['close'].shift(5) - 1).fillna(0)
        # 一致性：如果波动扩张与价格同方向，则+1；否则-1。并用幅度调整
        consistency = np.sign(vol_change * price_dir + 1e-10)
        factor = consistency * (np.abs(vol_change).clip(0, 1))
        # 平滑并归一化到[-1,1]
        factor = factor.rolling(3).mean().fillna(0).clip(-1, 1)
        return factor
