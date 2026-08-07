"""AI因子: 成交量确认动量 | 置信:60% | 计算近期价格变化方向与成交量变化方向的一致性。当价格上涨且成交量放大时为正信号，反之为负。避免在量价背离的情况下持仓，因为这种状态下容易发生反转或超时亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Confirmed_Momentum(BaseFactor):
    """计算近期价格变化方向与成交量变化方向的一致性。当价格上涨且成交量放大时为正信号，反之为负。避免在量价背离的情况下持仓，因为这种状态下容易发生反转或超时亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_confirm_momentum",
            name="Volume Confirmed Momentum",
            display_name="成交量确认动量",
            description="计算近期价格变化方向与成交量变化方向的一致性。当价格上涨且成交量放大时为正信号，反之为负。避免在量价背离的情况下持仓，因为这种状态下容易发生反转或超时亏损。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 14
        close = data['close']
        volume = data['volume']
        # 价格变化（收益）
        returns = close.pct_change(n)
        # 成交量变化（相对均值）
        vol_ma = volume.rolling(n).mean()
        vol_ratio = volume / vol_ma
        # 量价一致性：收益乘以成交量比率，再取符号
        raw = returns * vol_ratio
        # 用滚动标准差标准化
        std = raw.rolling(n).std()
        zscore = raw / (std + 1e-10)
        # 压缩到[-1,1]
        result = pd.Series(np.clip(zscore, -1, 1), index=data.index)
        result = result.fillna(0)
        return result
