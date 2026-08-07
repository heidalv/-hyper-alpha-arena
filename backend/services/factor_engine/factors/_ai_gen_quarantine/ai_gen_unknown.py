"""AI因子: 未知市场状态 | 置信:55% | 识别市场缺乏明确趋势或波动率异常（高波动但无方向）的状态。通过结合价格变化的方向一致性（ADX-like）和成交量异常来生成信号。当趋势强度低且成交量骤增时，预示可能变盘或随机波动，给出中性偏负信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownRegimeSignal(BaseFactor):
    """识别市场缺乏明确趋势或波动率异常（高波动但无方向）的状态。通过结合价格变化的方向一致性（ADX-like）和成交量异常来生成信号。当趋势强度低且成交量骤增时，预示可能变盘或随机波动，给出中性偏负信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknown",
            name="Unknown Regime Signal",
            display_name="未知市场状态",
            description="识别市场缺乏明确趋势或波动率异常（高波动但无方向）的状态。通过结合价格变化的方向一致性（ADX-like）和成交量异常来生成信号。当趋势强度低且成交量骤增时，预示可能变盘或随机波动，给出中性偏负信号。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        n = 14
        high, low, close, volume = data['high'], data['low'], data['close'], data['volume']
        # 计算类似ADX的趋势强度
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
        atr = tr.rolling(n).mean()
        plus_di = 100 * pd.Series(plus_dm).rolling(n).mean() / atr
        minus_di = 100 * pd.Series(minus_dm).rolling(n).mean() / atr
        dx = np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10) * 100
        # 成交量异常：相对过去n日均量
        vol_ma = volume.rolling(n).mean()
        vol_ratio = volume / (vol_ma + 1e-10)
        # 信号：低趋势（dx<25）且成交量异常高（>1.5倍）则视为未知状态
        sig = np.where((dx < 25) & (vol_ratio > 1.5), -0.8, 0.0)
        # 如果趋势很低且成交量正常，可能也是未知，给出轻微负向
        sig = np.where((dx < 15) & (vol_ratio <= 1.5), -0.3, sig)
        # 如果有趋势但成交量异常，则可能是趋势加速，正向
        sig = np.where((dx >= 25) & (vol_ratio > 1.5), 0.6, sig)
        result = np.clip(sig, -1, 1)
        return pd.Series(result, index=data.index)
