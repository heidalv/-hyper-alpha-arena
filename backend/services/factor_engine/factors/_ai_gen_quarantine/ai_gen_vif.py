"""AI因子: 成交量不平衡因子 | 置信:70% | 基于OBV（能量潮）与价格趋势的背离。计算过去20日OBV的线性斜率与价格斜率，若价格上升但OBV下降（负背离），则因子为负，表明上涨缺乏成交量支撑，容易反转；反之正背离为正。使用标准化后的斜率差映射到[-1,1]。适用于识别假突破和流动性陷阱。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Imbalance_Factor(BaseFactor):
    """基于OBV（能量潮）与价格趋势的背离。计算过去20日OBV的线性斜率与价格斜率，若价格上升但OBV下降（负背离），则因子为负，表明上涨缺乏成交量支撑，容易反转；反之正背离为正。使用标准化后的斜率差映射到[-1,1]。适用于识别假突破和流动性陷阱。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vif",
            name="Volume Imbalance Factor",
            display_name="成交量不平衡因子",
            description="基于OBV（能量潮）与价格趋势的背离。计算过去20日OBV的线性斜率与价格斜率，若价格上升但OBV下降（负背离），则因子为负，表明上涨缺乏成交量支撑，容易反转；反之正背离为正。使用标准化后的斜率差映射到[-1,1]。适用于识别假突破和流动性陷阱。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        period = 20
        # 计算OBV
        close = data['close']
        volume = data['volume']
        delta = close.diff()
        direction = np.sign(delta)
        direction[delta == 0] = 0
        obv = (volume * direction).cumsum()
        # 滚动线性斜率（使用最小二乘法）
        def slope(series):
            if len(series) < 2:
                return 0
            x = np.arange(len(series))
            y = series.values
            with np.errstate(divide='ignore', invalid='ignore'):
                A = np.vstack([x, np.ones(len(x))]).T
                m, _ = np.linalg.lstsq(A, y, rcond=None)[0]
            return m
        # 价格斜率
        price_slope = close.rolling(window=period).apply(slope, raw=False)
        obv_slope = obv.rolling(window=period).apply(slope, raw=False)
        # 计算斜率差（价格斜率 - OBV斜率），再归一化
        diff = price_slope - obv_slope
        # 滚动标准化
        mean = diff.rolling(window=period*2).mean()
        std = diff.rolling(window=period*2).std()
        z = (diff - mean) / (std + 1e-10)
        # 用tanh映射到[-1,1]
        factor = np.tanh(z)
        return factor.fillna(0).clip(-1, 1)
