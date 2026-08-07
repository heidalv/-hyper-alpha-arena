"""AI因子: 动量衰减与持仓超时风险因子 | 置信:60% | 衡量短期动量衰减程度，结合价格横盘时间。计算过去10日涨幅的斜率变化，若动量从正转负且价格振幅收窄（ATR下降），则预示趋势衰竭，容易触发max_hold_timeout或回撤止损，输出负信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Decay_and_Timeout_Risk(BaseFactor):
    """衡量短期动量衰减程度，结合价格横盘时间。计算过去10日涨幅的斜率变化，若动量从正转负且价格振幅收窄（ATR下降），则预示趋势衰竭，容易触发max_hold_timeout或回撤止损，输出负信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momdec",
            name="Momentum Decay and Timeout Risk",
            display_name="动量衰减与持仓超时风险因子",
            description="衡量短期动量衰减程度，结合价格横盘时间。计算过去10日涨幅的斜率变化，若动量从正转负且价格振幅收窄（ATR下降），则预示趋势衰竭，容易触发max_hold_timeout或回撤止损，输出负信号。",
            category="behavioral",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        # 动量：过去5日收益率
        ret5 = close.pct_change(5)
        # 动量变化：当前5日收益与前5日收益之差（二阶导数）
        mom_change = ret5 - ret5.shift(5)
        # 横盘度量：ATR（平均真实波幅）的衰减
        high = data['high']
        low = data['low']
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr10 = tr.rolling(10).mean()
        atr_ratio = atr10 / atr10.rolling(20).mean().shift(1)  # 当前ATR相对过去20日均值
        # 组合信号：动量衰减(负)且波幅收缩(小于0.9)时预警
        signal = -1.0 * ((mom_change < 0) & (atr_ratio < 0.9)).astype(float)
        # 平滑处理
        result = signal.rolling(3).mean().fillna(0)
        return result.clip(-1, 0)
