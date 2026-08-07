"""AI因子: 波动率调整动量 | 置信:60% | 计算近期价格动量（N日收益率）除以同期平均真实波幅（ATR）得到波动率调整后的动量强度。当波动率异常高时（如ATR超过近期均值2倍），缩小信号以避免在噪声行情中追涨杀跌。输出归一化到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentum(BaseFactor):
    """计算近期价格动量（N日收益率）除以同期平均真实波幅（ATR）得到波动率调整后的动量强度。当波动率异常高时（如ATR超过近期均值2倍），缩小信号以避免在噪声行情中追涨杀跌。输出归一化到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_momentum",
            name="Volatility-Adjusted Momentum",
            display_name="波动率调整动量",
            description="计算近期价格动量（N日收益率）除以同期平均真实波幅（ATR）得到波动率调整后的动量强度。当波动率异常高时（如ATR超过近期均值2倍），缩小信号以避免在噪声行情中追涨杀跌。输出归一化到[-1,1]。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 参数
        mom_period = 10
        atr_period = 14
        # 计算ATR
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(atr_period).mean()
        # 动量
        mom = close.pct_change(periods=mom_period)
        # 波动率调整：动量除以ATR（归一化到价格比例）
        # 将ATR转换为相对价格的变化比例
        atr_pct = atr / close
        # 避免除零
        atr_pct = atr_pct.replace(0, np.nan).fillna(0.01)
        adjusted = mom / atr_pct
        # 使用滚动标准差进行归一化到[-1,1]
        std = adjusted.rolling(30).std()
        normalized = adjusted / (std * 3)  # 约99%落在[-1,1]
        # 当ATR异常高时（超过近期均值2倍），压缩信号
        atr_ma = atr.rolling(60).mean()
        vol_ratio = atr / atr_ma
        weight = np.where(vol_ratio > 2.0, 0.3, 1.0)  # 异常波动时信号弱化
        result = normalized.clip(-1, 1) * weight
        result = result.fillna(0)
        return result
