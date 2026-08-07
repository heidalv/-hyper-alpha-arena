"""AI因子: 资金流净额反转因子 | 置信:60% | 模拟成交方向的不平衡，基于收盘价在当日区间内的位置与成交量的关系。当收盘价接近最高价但成交量偏低时，暗示多头力量不足（看空）；反之接近最低价且成交量低时看多。结合reverse_netting逻辑。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class NetFlowReversal(BaseFactor):
    """模拟成交方向的不平衡，基于收盘价在当日区间内的位置与成交量的关系。当收盘价接近最高价但成交量偏低时，暗示多头力量不足（看空）；反之接近最低价且成交量低时看多。结合reverse_netting逻辑。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_netflow",
            name="Net Flow Reversal",
            display_name="资金流净额反转因子",
            description="模拟成交方向的不平衡，基于收盘价在当日区间内的位置与成交量的关系。当收盘价接近最高价但成交量偏低时，暗示多头力量不足（看空）；反之接近最低价且成交量低时看多。结合reverse_netting逻辑。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算日内位置
        hl = data['high'] - data['low']
        pos = (data['close'] - data['low']) / hl.replace(0, 1)
        # 成交量相对前日变化
        vol_ratio = data['volume'] / data['volume'].shift(1).replace(0, 1)
        # 当位置极端(>0.8或<0.2)且成交量萎缩时，反转信号
        factor = np.where(pos > 0.8, -vol_ratio, np.where(pos < 0.2, vol_ratio, 0))
        # 归一化
        max_abs = np.abs(factor).max()
        if max_abs > 0:
            factor = factor / max_abs
        return pd.Series(factor, index=data.index).clip(-1, 1)
