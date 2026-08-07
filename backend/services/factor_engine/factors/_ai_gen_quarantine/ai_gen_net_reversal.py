"""AI因子: 反向净头寸失衡 | 置信:50% | 通过价格与成交量的非对称关系，捕捉类似反向净头寸清洗行为。当价格下跌但成交量萎缩、随后出现放量反弹，或反之，视为反转信号。计算量价背离指标：价格变化方向与成交量变化方向的协方差。返回+1表示空头反转，-1表示多头反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReverseNettingImbalance(BaseFactor):
    """通过价格与成交量的非对称关系，捕捉类似反向净头寸清洗行为。当价格下跌但成交量萎缩、随后出现放量反弹，或反之，视为反转信号。计算量价背离指标：价格变化方向与成交量变化方向的协方差。返回+1表示空头反转，-1表示多头反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_net_reversal",
            name="Reverse Netting Imbalance",
            display_name="反向净头寸失衡",
            description="通过价格与成交量的非对称关系，捕捉类似反向净头寸清洗行为。当价格下跌但成交量萎缩、随后出现放量反弹，或反之，视为反转信号。计算量价背离指标：价格变化方向与成交量变化方向的协方差。返回+1表示空头反转，-1表示多头反转。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        volume = data['volume']
        # 价格变化方向（1上涨，-1下跌）
        price_dir = close.diff().apply(lambda x: 1 if x > 0 else -1 if x < 0 else 0)
        # 成交量变化方向
        vol_dir = volume.diff().apply(lambda x: 1 if x > 0 else -1 if x < 0 else 0)
        # 背离信号：当价格与成交量方向相反且绝对值较大时
        # 计算5周期滚动相关系数
        corr = price_dir.rolling(5).corr(vol_dir)
        # 价格跌幅大但成交量减少（corr负且>0.5）-> 潜在反转向上->做空反转信号+1？
        # 实际上：prices down & vol down -> 可能底部；但实盘亏损模式中short亏损，这里偏向于捕捉空头反转
        # 我们在极端背离时，若price_dir为负且vol_dir为正（量增价跌）-> 可能空头陷阱 -> 做多反转？
        # 需要明确：因子输出[-1,1]对应方向：+1表示做空反转（即价格将下跌），-1表示做多反转（价格将上涨）
        # 根据亏损模式，很多short亏损，意味着空头被反转，因此我们应识别空头陷阱
        # 空头陷阱：价格下跌但成交量放大（vol_dir >0, price_dir<0） -> 后续可能反转向上 -> 此时做多反转信号(-1)
        # 多头陷阱：价格上涨但成交量缩小 -> 后续可能反转向下 -> 做空反转信号(+1)
        signal = pd.Series(0.0, index=data.index)
        cond_bear_trap = (price_dir < 0) & (vol_dir > 0) & (corr < -0.5)
        cond_bull_trap = (price_dir > 0) & (vol_dir < 0) & (corr < -0.5)
        signal[cond_bear_trap] = -1.0
        signal[cond_bull_trap] = 1.0
        return signal
