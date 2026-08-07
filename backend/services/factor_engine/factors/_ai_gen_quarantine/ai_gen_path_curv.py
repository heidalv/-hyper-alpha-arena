"""AI因子: 价格路径曲折度 | 置信:60% | 衡量价格走势的曲折程度——如果价格频繁反转（如连续多根K线收盘方向变化），则市场处于无趋势震荡状态。因子计算连续N根K线收盘价方向变化的次数与振幅的相对大小，曲折度高时给出负信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Price_Path_Curvature(BaseFactor):
    """衡量价格走势的曲折程度——如果价格频繁反转（如连续多根K线收盘方向变化），则市场处于无趋势震荡状态。因子计算连续N根K线收盘价方向变化的次数与振幅的相对大小，曲折度高时给出负信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_path_curv",
            name="Price Path Curvature",
            display_name="价格路径曲折度",
            description="衡量价格走势的曲折程度——如果价格频繁反转（如连续多根K线收盘方向变化），则市场处于无趋势震荡状态。因子计算连续N根K线收盘价方向变化的次数与振幅的相对大小，曲折度高时给出负信号。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        n = 10  # 窗口
        # 收盘价方向：1涨，-1跌
        direction = np.sign(data['close'].diff())
        # 方向变化次数（异或）
        changes = (direction.shift(1) != direction).astype(int)
        # 滚动窗口内变化次数总和
        change_count = changes.rolling(n).sum()
        # 归一化最大可能变化次数为n-1
        max_changes = n - 1
        # 同时考虑振幅相对大小（振幅大但变化多更曲折）
        amp = data['high'] - data['low']
        amp_avg = amp.rolling(n).mean()
        # 曲折分数 = 变化次数比例 * 振幅偏离均值程度
        curv_score = (change_count / max_changes) * (amp / amp_avg).fillna(1)
        # 映射到[-1,0]，阈值0.6以上为负
        signal = -np.clip((curv_score - 0.6) * 5, 0, 1)
        return pd.Series(signal, index=data.index)
