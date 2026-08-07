"""AI因子: 趋势状态强度 | 置信:65% | 通过ADX和价格相对于长期均线的偏离度判断当前市场趋势强度与方向。高正值表示强上升趋势，高负值表示强下降趋势，接近0表示震荡/未知状态。用于过滤regime=unknown时的交易信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendRegimeStrength(BaseFactor):
    """通过ADX和价格相对于长期均线的偏离度判断当前市场趋势强度与方向。高正值表示强上升趋势，高负值表示强下降趋势，接近0表示震荡/未知状态。用于过滤regime=unknown时的交易信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendregime",
            name="Trend Regime Strength",
            display_name="趋势状态强度",
            description="通过ADX和价格相对于长期均线的偏离度判断当前市场趋势强度与方向。高正值表示强上升趋势，高负值表示强下降趋势，接近0表示震荡/未知状态。用于过滤regime=unknown时的交易信号。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算ADX（简化版使用DI+和DI-）
        high = data['high']
        low = data['low']
        close = data['close']
        period = 14
        # 计算True Range
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(window=period).mean()
        # 方向移动
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        # 平滑
        pos_di = 100 * (pd.Series(pos_dm, index=data.index).rolling(window=period).mean() / atr)
        neg_di = 100 * (pd.Series(neg_dm, index=data.index).rolling(window=period).mean() / atr)
        dx = 100 * np.abs(pos_di - neg_di) / (pos_di + neg_di + 1e-10)
        adx = dx.rolling(window=period).mean()
        # 价格相对于长期均线（200期）的偏离
        ma_long = close.rolling(window=200).mean()
        price_dev = (close - ma_long) / (ma_long + 1e-10)
        # 组合：ADX标准化到[-1,1]，乘以方向信号
        adx_norm = (adx - 20) / 40  # 20及以上视为趋势，40以上强趋势
        adx_norm = np.clip(adx_norm, -1, 1)
        direction = np.sign(close - close.shift(period)).fillna(0)
        trend_regime = adx_norm * direction
        # 用价格偏离度做微调
        result = trend_regime * (1 + 0.5 * np.tanh(price_dev * 5))
        result = np.clip(result, -1, 1)
        return result
