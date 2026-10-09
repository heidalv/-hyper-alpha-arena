"""AI因子: 分批止盈保护因子 | 置信:50% | 针对staged_tp1和master_running_close亏损模式，这些交易在部分止盈后价格继续反向运行。使用价格在近期高点的位置与成交量萎缩的组合，当价格接近高点但成交量无法放大时，暗示上冲乏力，容易触发止损，因子值降低。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StagedTPGuard(BaseFactor):
    """针对staged_tp1和master_running_close亏损模式，这些交易在部分止盈后价格继续反向运行。使用价格在近期高点的位置与成交量萎缩的组合，当价格接近高点但成交量无法放大时，暗示上冲乏力，容易触发止损，因子值降低。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_staged_tp_guard",
            name="Staged_TP_Guard",
            display_name="分批止盈保护因子",
            description="针对staged_tp1和master_running_close亏损模式，这些交易在部分止盈后价格继续反向运行。使用价格在近期高点的位置与成交量萎缩的组合，当价格接近高点但成交量无法放大时，暗示上冲乏力，容易触发止损，因子值降低。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high_20 = data['high'].rolling(20).max()
        close_pos = (data['close'] - data['low'].rolling(20).min()) / (high_20 - data['low'].rolling(20).min() + 1e-9)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        result = (close_pos - vol_ratio).clip(-1, 1)
        return result
