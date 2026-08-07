"""AI因子: 趋势稳定性因子 | 置信:70% | 基于价格相对于EMA的乖离率与ATR的比值，衡量趋势的稳定性。当价格偏离均线但波动率较大时，趋势不可靠，因子值趋于-1；当价格紧贴均线且波动率低时，趋势稳定，因子值趋于+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trendstability(BaseFactor):
    """基于价格相对于EMA的乖离率与ATR的比值，衡量趋势的稳定性。当价格偏离均线但波动率较大时，趋势不可靠，因子值趋于-1；当价格紧贴均线且波动率低时，趋势稳定，因子值趋于+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendstab",
            name="TrendStability",
            display_name="趋势稳定性因子",
            description="基于价格相对于EMA的乖离率与ATR的比值，衡量趋势的稳定性。当价格偏离均线但波动率较大时，趋势不可靠，因子值趋于-1；当价格紧贴均线且波动率低时，趋势稳定，因子值趋于+1。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算EMA和ATR
        period = 20
        ema = data['close'].ewm(span=period, adjust=False).mean()
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift())
        low_close = np.abs(data['low'] - data['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        # 偏离率 (价格-EMA)/EMA
        deviation = (data['close'] - ema) / ema
        # 标准化偏离率：除以ATR/close比例避免尺度问题
        norm_dev = deviation / (atr / data['close'] + 1e-10)
        # 映射到[-1,1]，使用tanh压缩
        result = -np.tanh(np.abs(norm_dev)) * np.sign(norm_dev)
        # 平滑填充缺失值
        result = result.fillna(0)
        return result
