"""AI因子: 持仓超时反转因子 | 置信:50% | 基于价格长时间区间内趋势衰减特征，捕捉因持仓超时导致的平仓压力反转。通过计算价格在N日区间内未创新高/新低的天数、相对强度（RSI）及成交量萎缩，预测趋势衰竭反转。正信号表示向上反转，负信号表示向下反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeoutReversal(BaseFactor):
    """基于价格长时间区间内趋势衰减特征，捕捉因持仓超时导致的平仓压力反转。通过计算价格在N日区间内未创新高/新低的天数、相对强度（RSI）及成交量萎缩，预测趋势衰竭反转。正信号表示向上反转，负信号表示向下反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_holdto",
            name="Hold Timeout Reversal",
            display_name="持仓超时反转因子",
            description="基于价格长时间区间内趋势衰减特征，捕捉因持仓超时导致的平仓压力反转。通过计算价格在N日区间内未创新高/新低的天数、相对强度（RSI）及成交量萎缩，预测趋势衰竭反转。正信号表示向上反转，负信号表示向下反转。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        n = 20
    
        # 滚动新高/新低天数统计
        roll_high = high.rolling(n, min_periods=1).max()
        roll_low = low.rolling(n, min_periods=1).min()
        days_since_high = n - close.rolling(n).apply(lambda x: x.argmax()) - 1
        days_since_low = n - close.rolling(n).apply(lambda x: x.argmin()) - 1
        # 简单替代: 价格距离滚动极值的比例
        pos_in_range = (close - roll_low) / (roll_high - roll_low + 1e-10)
    
        # 趋势强度：N日收益率绝对值
        ret_n = close.pct_change(n)
        strength = ret_n.abs()
    
        # 成交量萎缩：最近3日均量 / 过去10日均量
        vol_short = volume.rolling(3, min_periods=1).mean()
        vol_long = volume.rolling(10, min_periods=1).mean() + 1e-10
        vol_ratio = vol_short / vol_long
    
        # RSI
        delta = close.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(n, min_periods=1).mean()
        avg_loss = loss.rolling(n, min_periods=1).mean() + 1e-10
        rsi = 100 - 100 / (1 + avg_gain / avg_loss)
    
        # 长时间横盘反转: 价格处于中间位置(0.3~0.7), 趋势弱(strength<0.05), 成交量萎缩(vol_ratio<0.8), RSI中性(40-60)
        long = (pos_in_range > 0.35) & (pos_in_range < 0.65) & (strength < 0.05) & (vol_ratio < 0.8) & (rsi > 40) & (rsi < 60) & (rsi.shift(1) < rsi)  # RSI向上突破中性
        short = (pos_in_range > 0.35) & (pos_in_range < 0.65) & (strength < 0.05) & (vol_ratio < 0.8) & (rsi > 40) & (rsi < 60) & (rsi.shift(1) > rsi) # RSI向下突破中性
    
        result = np.where(long, 1.0, np.where(short, -1.0, 0.0))
        return pd.Series(result, index=data.index)
