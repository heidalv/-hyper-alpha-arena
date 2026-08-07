"""AI因子: 反向套利陷阱识别 | 置信:60% | 检测价格在短期快速反转（例如从上涨转为下跌）且成交量无法支撑方向延续的形态，模拟reverse_netting亏损场景。通过比较当前收盘价相对开盘价的涨幅与成交量与过去N周期均值的背离程度，当涨幅呈假突破时输出负分。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReverseNettingTrap(BaseFactor):
    """检测价格在短期快速反转（例如从上涨转为下跌）且成交量无法支撑方向延续的形态，模拟reverse_netting亏损场景。通过比较当前收盘价相对开盘价的涨幅与成交量与过去N周期均值的背离程度，当涨幅呈假突破时输出负分。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rvn",
            name="Reverse Netting Trap",
            display_name="反向套利陷阱识别",
            description="检测价格在短期快速反转（例如从上涨转为下跌）且成交量无法支撑方向延续的形态，模拟reverse_netting亏损场景。通过比较当前收盘价相对开盘价的涨幅与成交量与过去N周期均值的背离程度，当涨幅呈假突破时输出负分。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 日内涨跌幅
        ret = (data['close'] - data['open']) / data['open'].replace(0, 1e-9)
        # 成交量相对过去10期均值
        vol_ma10 = data['volume'].rolling(10).mean().replace(0, 1e-9)
        vol_ratio = data['volume'] / vol_ma10
        # 当价格反转（收盘弱于开盘）但成交量仍较大时，陷阱信号
        # 反转强度: 若收盘<开盘,取负值加强
        rev_strength = -ret.clip(upper=0)  # 仅关注下跌部分
        # 成交量的反常放大
        vol_anomaly = (vol_ratio - 1).clip(lower=0)
        # 组合: 下跌+成交量放大 => 陷阱可能性大
        score = -np.clip(rev_strength * vol_anomaly * 5, -1, 1)
        return score.fillna(0)
