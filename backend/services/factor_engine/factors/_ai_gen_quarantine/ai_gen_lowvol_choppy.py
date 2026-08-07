"""AI因子: 低波动窄幅震荡风险因子 | 置信:60% | 针对max_hold_timeout和sl亏损模式，低波动震荡市场容易导致持仓超时或假突破止损。使用ATR与价格变化率的比值衡量波动率相对趋势，同时结合布林带宽度。当ATR较小且价格在布林带内窄幅波动时，返回负值表示高风险；趋势突破时返回正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Low_Volatility_Choppy_Market_Risk(BaseFactor):
    """针对max_hold_timeout和sl亏损模式，低波动震荡市场容易导致持仓超时或假突破止损。使用ATR与价格变化率的比值衡量波动率相对趋势，同时结合布林带宽度。当ATR较小且价格在布林带内窄幅波动时，返回负值表示高风险；趋势突破时返回正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lowvol_choppy",
            name="Low Volatility Choppy Market Risk",
            display_name="低波动窄幅震荡风险因子",
            description="针对max_hold_timeout和sl亏损模式，低波动震荡市场容易导致持仓超时或假突破止损。使用ATR与价格变化率的比值衡量波动率相对趋势，同时结合布林带宽度。当ATR较小且价格在布林带内窄幅波动时，返回负值表示高风险；趋势突破时返回正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算ATR百分比
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(20).mean()
        atr_pct = atr / close * 100  # ATR占价格百分比
        # 计算20日价格变化率
        price_range = close.rolling(20).apply(lambda x: x.max() - x.min(), raw=True)
        range_pct = price_range / close * 100
        # 计算布林带宽度 (20日2倍标准差)
        std = close.rolling(20).std()
        boll_width = (2 * std) / close * 100
        # 组合：低波动（ATR小，布林带宽窄）且价格变化率小 -> 信号负
        # 使用三个指标的z-score均值，然后取反
        atr_z = (atr_pct - atr_pct.rolling(100).mean()) / atr_pct.rolling(100).std()
        range_z = (range_pct - range_pct.rolling(100).mean()) / range_pct.rolling(100).std()
        boll_z = (boll_width - boll_width.rolling(100).mean()) / boll_width.rolling(100).std()
        composite = (atr_z + range_z + boll_z) / 3
        # 负值表示低波动（风险高），取负号使得低波动时因子为负
        signal = -np.tanh(composite * 2)
        return pd.Series(signal, index=data.index).fillna(0)
