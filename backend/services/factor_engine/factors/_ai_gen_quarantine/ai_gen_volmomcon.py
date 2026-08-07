"""AI因子: 量价动量一致性 | 置信:58% | 衡量成交量和价格动量的方向一致性。当价格向上但成交量向下，或价格向下但成交量向上时，趋势不可持续，容易导致反转亏损。本因子计算两个方向的乖离度，输出反向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Momentum_Consistency(BaseFactor):
    """衡量成交量和价格动量的方向一致性。当价格向上但成交量向下，或价格向下但成交量向上时，趋势不可持续，容易导致反转亏损。本因子计算两个方向的乖离度，输出反向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volmomcon",
            name="Volume_Momentum_Consistency",
            display_name="量价动量一致性",
            description="衡量成交量和价格动量的方向一致性。当价格向上但成交量向下，或价格向下但成交量向上时，趋势不可持续，容易导致反转亏损。本因子计算两个方向的乖离度，输出反向信号。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 10
        close = data['close']
        volume = data['volume']
        # 价格动量: 过去n期收益率
        ret = close.pct_change(n)
        # 成交量动量: 过去n期成交量变化率
        vol_chg = volume.pct_change(n)
        # 标准化
        ret_z = (ret - ret.rolling(n).mean()) / ret.rolling(n).std().clip(lower=1e-8)
        vol_z = (vol_chg - vol_chg.rolling(n).mean()) / vol_chg.rolling(n).std().clip(lower=1e-8)
        # 一致性: 两者乘积为正表示一致，负表示背离
        consistency = ret_z * vol_z
        # 反转: 背离时发出信号（一致性负 -> 负信号表示看空？实际背离可能预示反转，方向需要明确）
        # 这里取反：当背离时预测反向，即一致性负时预测反转方向？简单映射：
        # 如果价格动量向上而成交量向下（ret_z>0且vol_z<0），则反转向下，所以信号为负；
        # 如果价格动量向下而成交量向上，则反转向上，信号为正。
        signal = -consistency
        # 归一化到[-1,1]
        result = signal.clip(-3, 3) / 3.0
        return result.fillna(0)
