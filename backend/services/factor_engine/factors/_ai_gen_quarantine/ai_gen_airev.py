"""AI因子: AI反转模式 | 置信:55% | 模拟AI自动交易常见的反转模式：价格在短时间内快速上升/下降，随后出现大阴线/大阳线且成交量配合，形成多头/空头陷阱。利用价格动量与K线实体长度的比值来捕捉。计算当前K线涨幅与前N根K线涨幅的绝对差，以及当前K线实体与影线的比例，当出现长上影线缩量上涨或长下影线放量下跌时输出反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class AIReversePattern(BaseFactor):
    """模拟AI自动交易常见的反转模式：价格在短时间内快速上升/下降，随后出现大阴线/大阳线且成交量配合，形成多头/空头陷阱。利用价格动量与K线实体长度的比值来捕捉。计算当前K线涨幅与前N根K线涨幅的绝对差，以及当前K线实体与影线的比例，当出现长上影线缩量上涨或长下影线放量下跌时输出反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_airev",
            name="AI Reverse Pattern",
            display_name="AI反转模式",
            description="模拟AI自动交易常见的反转模式：价格在短时间内快速上升/下降，随后出现大阴线/大阳线且成交量配合，形成多头/空头陷阱。利用价格动量与K线实体长度的比值来捕捉。计算当前K线涨幅与前N根K线涨幅的绝对差，以及当前K线实体与影线的比例，当出现长上影线缩量上涨或长下影线放量下跌时输出反转信号。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        open_ = data['open']
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
    
        # 动量：最近3根K线的变化
        mom = close.pct_change(3)
    
        # 当前K线实体
        body = close - open_
        body_pct = body / (open_ + 1e-8)
    
        # 上影线 / 下影线
        upper_shadow = high - (open_ + close) / 2
        lower_shadow = (open_ + close) / 2 - low
    
        # 长上影线且缩量上涨（多头陷阱）
        bull_trap = (mom > 0.02) & (body_pct < 0) & (upper_shadow > body.abs()) & (volume < volume.rolling(20).mean())
        # 长下影线且放量下跌（空头陷阱）
        bear_trap = (mom < -0.02) & (body_pct > 0) & (lower_shadow > body.abs()) & (volume > volume.rolling(20).mean())
    
        result = pd.Series(0.0, index=data.index)
        result[bull_trap] = -1.0
        result[bear_trap] = 1.0
        return result
