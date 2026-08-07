"""AI因子: 价量背离指数 | 置信:60% | 检测价格处于区间高位但成交量萎缩（多头衰竭）或价格低位放量（空头衰竭），预示短期反转，避免在趋势不明时追涨杀跌导致止损。通过计算收盘价在近N日高低位置与成交量相对均量的偏离程度，输出-1（强烈空头背离）到+1（强烈多头背离）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceVolumeDivergence(BaseFactor):
    """检测价格处于区间高位但成交量萎缩（多头衰竭）或价格低位放量（空头衰竭），预示短期反转，避免在趋势不明时追涨杀跌导致止损。通过计算收盘价在近N日高低位置与成交量相对均量的偏离程度，输出-1（强烈空头背离）到+1（强烈多头背离）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pvd",
            name="Price Volume Divergence",
            display_name="价量背离指数",
            description="检测价格处于区间高位但成交量萎缩（多头衰竭）或价格低位放量（空头衰竭），预示短期反转，避免在趋势不明时追涨杀跌导致止损。通过计算收盘价在近N日高低位置与成交量相对均量的偏离程度，输出-1（强烈空头背离）到+1（强烈多头背离）。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        n = 14
        high = data['high']
        low = data['low']
        close = data['close']
        vol = data['volume']
        # 价格位置
        hh = high.rolling(n).max()
        ll = low.rolling(n).min()
        pos = (close - ll) / (hh - ll + 1e-10)
        # 成交量相对均值
        vol_ma = vol.rolling(n).mean()
        vol_ratio = vol / (vol_ma + 1e-10)
        # 背离信号：价格高位但成交量萎缩（pos>0.8且vol_ratio<0.6）=>空头背离，负值；价格低位且放量 =>多头背离，正值
        bear_div = (pos > 0.8) & (vol_ratio < 0.6)
        bull_div = (pos < 0.2) & (vol_ratio > 1.5)
        raw = np.where(bull_div, 1, np.where(bear_div, -1, 0))
        # 平滑处理，用pos的偏离度加权
        score = raw * (1 - np.abs(pos - 0.5)) * 2
        result = pd.Series(score, index=close.index).fillna(0).clip(-1, 1)
        return result
