"""AI因子: 横盘超时风险 | 置信:65% | 识别价格在窄幅区间内长时间盘整（容易触发超时平仓）。计算当前K线在最近N日区间内的相对位置标准差，并检测区间宽度是否收窄到历史低位，信号为负值时表示盘整风险高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldingTimeoutRiskSqueeze(BaseFactor):
    """识别价格在窄幅区间内长时间盘整（容易触发超时平仓）。计算当前K线在最近N日区间内的相对位置标准差，并检测区间宽度是否收窄到历史低位，信号为负值时表示盘整风险高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hsp",
            name="Holding Timeout Risk (Squeeze)",
            display_name="横盘超时风险",
            description="识别价格在窄幅区间内长时间盘整（容易触发超时平仓）。计算当前K线在最近N日区间内的相对位置标准差，并检测区间宽度是否收窄到历史低位，信号为负值时表示盘整风险高。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        n = 20
        # 区间相对位置
        recent_high = data['high'].rolling(n).max()
        recent_low = data['low'].rolling(n).min()
        position = (data['close'] - recent_low) / (recent_high - recent_low + 1e-10)
        pos_std = position.rolling(n).std()  # 位置波动小说明盘整
        # 区间宽度相对于过去宽度的比率
        range_width = (recent_high - recent_low) / data['close'] * 100
        width_median = range_width.rolling(50).median()
        width_ratio = range_width / (width_median + 1e-10)
        # 盘整条件：位置波动低（<0.1）且区间窄（<0.5倍中位数）
        squeeze = (pos_std < 0.1).astype(float) * (width_ratio < 0.5).astype(float)
        # 转换为负信号，表示高风险
        signal = - squeeze * 1.0
        # 平滑
        result = signal.rolling(3).mean().fillna(0).clip(-1, 0)  # 只有负值
        return result
