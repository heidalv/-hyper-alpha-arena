"""AI因子: 趋势清晰度指数 | 置信:60% | 基于ADX和价格与均线的关系，判断趋势的清晰度。当趋势不明确（低ADX且价格在均线附近震荡）时，因子趋近-1，提示容易触发止损；趋势明确时趋近+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendClarityIndex(BaseFactor):
    """基于ADX和价格与均线的关系，判断趋势的清晰度。当趋势不明确（低ADX且价格在均线附近震荡）时，因子趋近-1，提示容易触发止损；趋势明确时趋近+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendclarity",
            name="Trend Clarity Index",
            display_name="趋势清晰度指数",
            description="基于ADX和价格与均线的关系，判断趋势的清晰度。当趋势不明确（低ADX且价格在均线附近震荡）时，因子趋近-1，提示容易触发止损；趋势明确时趋近+1。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算典型价格
        typical = (data['high'] + data['low'] + data['close']) / 3
        # 计算14周期ADX
        period = 14
        high = data['high']
        low = data['low']
        close = data['close']
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        tr = np.maximum(high - low, 
                        np.maximum(abs(high - close.shift(1)), 
                                   abs(low - close.shift(1))))
        atr = pd.Series(tr).rolling(period).mean()
        plus_di = 100 * pd.Series(plus_dm).rolling(period).mean() / atr
        minus_di = 100 * pd.Series(minus_dm).rolling(period).mean() / atr
        dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100
        adx = dx.rolling(period).mean()
        # 计算价格与20日均线的距离（标准化后）
        ma20 = data['close'].rolling(20).mean()
        price_dev = (data['close'] - ma20) / (data['close'].rolling(20).std() + 1e-10)
        # 组合：ADX高且价格偏离均值时清晰度高
        # 将ADX归一化到0~1，假设ADX大于40为强趋势
        adx_norm = np.clip(adx / 40, 0, 1)
        # 价格偏离绝对值，越大越清晰但需结合ADX
        dev_abs = np.abs(price_dev).clip(0, 3) / 3
        # 清晰度 = 0.5*adx_norm + 0.5*dev_abs，再映射到[-1,1]
        clarity = 0.5 * adx_norm + 0.5 * dev_abs
        result = 2 * clarity - 1
        result = result.clip(-1, 1).fillna(0)
        return pd.Series(result, index=data.index)
