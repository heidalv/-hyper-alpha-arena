"""AI因子: 流动性失衡反转因子 | 置信:60% | 检测价格是否逼近近期成交量密集区（流动性磁铁）并出现反转信号。通过计算成交量加权平均价格(VWAP)附近的成交量分布，当价格远离VWAP且成交缩量时，预示反转。因子值正表示看多（价格在下方缩量），负表示看空（价格在上方缩量）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityImbalanceReversal(BaseFactor):
    """检测价格是否逼近近期成交量密集区（流动性磁铁）并出现反转信号。通过计算成交量加权平均价格(VWAP)附近的成交量分布，当价格远离VWAP且成交缩量时，预示反转。因子值正表示看多（价格在下方缩量），负表示看空（价格在上方缩量）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_imbalance",
            name="Liquidity Imbalance Reversal",
            display_name="流动性失衡反转因子",
            description="检测价格是否逼近近期成交量密集区（流动性磁铁）并出现反转信号。通过计算成交量加权平均价格(VWAP)附近的成交量分布，当价格远离VWAP且成交缩量时，预示反转。因子值正表示看多（价格在下方缩量），负表示看空（价格在上方缩量）。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算VWAP
        vwap = (data['close'] * data['volume']).rolling(20).sum() / data['volume'].rolling(20).sum()
        # 价格偏离VWAP的百分比
        price_div = (data['close'] - vwap) / vwap
        # 计算近5周期成交量变化率
        vol_change = data['volume'] / data['volume'].rolling(5).mean()
        # 当价格偏离大且成交量萎缩时，反转信号强烈
        # 做多信号：价格低于VWAP且成交量小于均值
        long_signal = (price_div < -0.02) & (vol_change < 0.8)
        # 做空信号：价格高于VWAP且成交量小于均值
        short_signal = (price_div > 0.02) & (vol_change < 0.8)
        # 组合信号：多头为1，空头为-1，其他为0
        result = pd.Series(0, index=data.index)
        result[long_signal] = 1.0
        result[short_signal] = -1.0
        # 平滑处理：取3周期移动平均，避免频繁切换
        result = result.rolling(3).mean().fillna(0)
        return result
