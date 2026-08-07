"""AI因子: 主力收尾反转 | 置信:55% | 模仿master_running_close模式：价格在趋势末端出现快速拉升或下跌，但伴随成交量递减，暗示主力出货完毕反转。通过计算价格动量与成交量的背离来捕获。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MasterRunningCloseReversal(BaseFactor):
    """模仿master_running_close模式：价格在趋势末端出现快速拉升或下跌，但伴随成交量递减，暗示主力出货完毕反转。通过计算价格动量与成交量的背离来捕获。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_master_run",
            name="Master Running Close Reversal",
            display_name="主力收尾反转",
            description="模仿master_running_close模式：价格在趋势末端出现快速拉升或下跌，但伴随成交量递减，暗示主力出货完毕反转。通过计算价格动量与成交量的背离来捕获。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        # 计算5日价格变化率
        df['roc5'] = (df['close'] - df['close'].shift(5)) / df['close'].shift(5)
        # 计算5日成交量变化率
        df['vol_roc5'] = (df['volume'] - df['volume'].shift(5)) / df['volume'].shift(5)
        # 计算动量背离：价格正增长但成交量负增长（或反之）
        # 做空信号：价格上升（roc5 > 0.02）但成交量下降（vol_roc5 < -0.2）
        cond_short = (df['roc5'] > 0.02) & (df['vol_roc5'] < -0.2)
        # 做多信号：价格下跌（roc5 < -0.02）但成交量下降（vol_roc5 < -0.2），表示下跌动能衰竭
        cond_long = (df['roc5'] < -0.02) & (df['vol_roc5'] < -0.2)
        # 额外考虑价格位置：当前价格接近近期高点或低点（高于近20日高点95%或低于低点5%）
        df['hh20'] = df['high'].rolling(20).max()
        df['ll20'] = df['low'].rolling(20).min()
        cond_near_high = df['close'] >= df['hh20'] * 0.95
        cond_near_low = df['close'] <= df['ll20'] * 1.05
        # 最终信号
        signal = pd.Series(0, index=df.index)
        signal[cond_short & cond_near_high] = -1.0
        signal[cond_long & cond_near_low] = 1.0
        return signal
