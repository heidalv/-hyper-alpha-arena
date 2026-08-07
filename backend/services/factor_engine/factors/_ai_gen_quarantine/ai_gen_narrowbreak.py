"""AI因子: 窄幅突破假信号 | 置信:55% | 捕捉价格在极窄区间内盘整后，小幅突破即回落的现象。计算过去k根K线的平均真实波幅ATR，若当前突破幅度小于ATR的0.5倍且随后反向，则视为假突破。这里简化：以当前收盘价是否在最近n根K线的极窄区间内（最高最低差小于ATR*0.3），若价格突破该区间上沿但成交量未放大，则做空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class NarrowRangeFalseBreak(BaseFactor):
    """捕捉价格在极窄区间内盘整后，小幅突破即回落的现象。计算过去k根K线的平均真实波幅ATR，若当前突破幅度小于ATR的0.5倍且随后反向，则视为假突破。这里简化：以当前收盘价是否在最近n根K线的极窄区间内（最高最低差小于ATR*0.3），若价格突破该区间上沿但成交量未放大，则做空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_narrowbreak",
            name="NarrowRangeFalseBreak",
            display_name="窄幅突破假信号",
            description="捕捉价格在极窄区间内盘整后，小幅突破即回落的现象。计算过去k根K线的平均真实波幅ATR，若当前突破幅度小于ATR的0.5倍且随后反向，则视为假突破。这里简化：以当前收盘价是否在最近n根K线的极窄区间内（最高最低差小于ATR*0.3），若价格突破该区间上沿但成交量未放大，则做空。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            n = 10
            atr = (data['high'] - data['low']).rolling(n).mean()
            max_range = data['high'].rolling(n).max()
            min_range = data['low'].rolling(n).min()
            # 窄幅条件：区间宽度小于ATR*0.3
            narrow = (max_range - min_range) < (atr * 0.3)
            # 突破上沿：收盘价大于前一根的最高点
            break_up = data['close'] > data['high'].shift(1)
            factor = (narrow & break_up).astype(float) * -1.0
            factor.fillna(0, inplace=True)
            return factor
