"""AI因子: 资金流强度因子 | 置信:70% | 基于资金流指标（MFI）的变体，结合收盘价位置与成交量方向。计算典型价格相对于近期高低的相对位置，并用成交量加权，最后归一化到[-1,1]。正值表示资金流入（潜在上涨趋势），负值表示流出，帮助识别主力动向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Weighted_Flow_Strength(BaseFactor):
    """基于资金流指标（MFI）的变体，结合收盘价位置与成交量方向。计算典型价格相对于近期高低的相对位置，并用成交量加权，最后归一化到[-1,1]。正值表示资金流入（潜在上涨趋势），负值表示流出，帮助识别主力动向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_money_flow",
            name="Volume-Weighted Flow Strength",
            display_name="资金流强度因子",
            description="基于资金流指标（MFI）的变体，结合收盘价位置与成交量方向。计算典型价格相对于近期高低的相对位置，并用成交量加权，最后归一化到[-1,1]。正值表示资金流入（潜在上涨趋势），负值表示流出，帮助识别主力动向。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        period = 14
        # 典型价格
        typ_price = (high + low + close) / 3
        # 资金流因子：比较当前典型价格与之前
        money_flow = typ_price * volume
        # 方向：当日典型价格高于前一日为正流，反之为负流
        direction = np.sign(typ_price - typ_price.shift(1))
        # 计算正/负资金流之和
        pos_flow = (money_flow * (direction > 0)).rolling(period).sum()
        neg_flow = (money_flow * (direction < 0)).rolling(period).sum()
        # 防止除零
        total_flow = pos_flow + neg_flow + 1e-10
        mfi = 100 * pos_flow / total_flow
        # 将MFI（0-100）映射到[-1,1]
        result = (mfi - 50) / 50
        result = result.clip(-1, 1)
        return result
