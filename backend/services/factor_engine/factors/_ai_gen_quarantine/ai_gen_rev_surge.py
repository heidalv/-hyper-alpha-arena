"""AI因子: 反转冲击因子 | 置信:70% | 捕捉价格在短期内剧烈反转的风险，通过计算最近N根K线的价格加速度与成交量激增的联合异常，当价格快速反向运动且成交量放大时发出强风险信号。适用于检测类似ai_reverse和reverse_netting的亏损模式。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalSurge(BaseFactor):
    """捕捉价格在短期内剧烈反转的风险，通过计算最近N根K线的价格加速度与成交量激增的联合异常，当价格快速反向运动且成交量放大时发出强风险信号。适用于检测类似ai_reverse和reverse_netting的亏损模式。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rev_surge",
            name="Reversal Surge",
            display_name="反转冲击因子",
            description="捕捉价格在短期内剧烈反转的风险，通过计算最近N根K线的价格加速度与成交量激增的联合异常，当价格快速反向运动且成交量放大时发出强风险信号。适用于检测类似ai_reverse和reverse_netting的亏损模式。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 参数
        n = 5  # 短期窗口
        m = 10 # 长期窗口
        # 价格变化率
        ret = data['close'].pct_change()
        # 加速度：当前变化率与之前变化率之差
        accel = ret.diff()
        # 成交量异常：当前成交量与滚动均值的比值
        vol_ma = data['volume'].rolling(m).mean()
        vol_ratio = data['volume'] / vol_ma
        # 组合信号：加速度反向且成交量放大
        # 用符号判断方向：正加速度但负收益（反转下跌）或负加速度但正收益（反转上涨）
        sig = -np.sign(accel) * np.sign(ret)  # 反向加速时为1
        sig = sig * np.abs(accel) * (vol_ratio - 1)
        # 滚动归一化至[-1,1]
        sig_std = sig.rolling(m).std()
        result = sig / (sig_std + 1e-10)
        result = result.clip(-1, 1)
        return result.fillna(0)
