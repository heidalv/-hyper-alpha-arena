"""AI因子: 量价背离因子 | 置信:60% | 识别价格下跌时成交量放大的背离信号，通常预示多头陷阱。当日价格变化率与成交量变化率方向相反且强度超过阈值时，输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Divergence_Pattern(BaseFactor):
    """识别价格下跌时成交量放大的背离信号，通常预示多头陷阱。当日价格变化率与成交量变化率方向相反且强度超过阈值时，输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vdp",
            name="Volume Divergence Pattern",
            display_name="量价背离因子",
            description="识别价格下跌时成交量放大的背离信号，通常预示多头陷阱。当日价格变化率与成交量变化率方向相反且强度超过阈值时，输出负值。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        volume = data['volume']
        ret = close.pct_change()
        vol_change = volume.pct_change()
        # 价格下跌且成交量放大：负值增强
        condition = (ret < -0.01) & (vol_change > 0.1)
        signal = -condition.astype(float) * 1.0
        # 平滑处理，避免突变
        signal = signal.rolling(3).mean().fillna(0)
        return signal.clip(-1, 1)
