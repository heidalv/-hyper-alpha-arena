"""AI因子: 尘埃清理波动 | 置信:55% | 识别类似dust_cleanup模式：价格在小幅震荡后突然出现异常大单推动的快速波动，随后回落。通过计算价格波动与成交量的短期冲击比，捕捉流动性失衡后的清洗行为。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class DustCleanupVolatility(BaseFactor):
    """识别类似dust_cleanup模式：价格在小幅震荡后突然出现异常大单推动的快速波动，随后回落。通过计算价格波动与成交量的短期冲击比，捕捉流动性失衡后的清洗行为。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_dust_vol",
            name="Dust Cleanup Volatility",
            display_name="尘埃清理波动",
            description="识别类似dust_cleanup模式：价格在小幅震荡后突然出现异常大单推动的快速波动，随后回落。通过计算价格波动与成交量的短期冲击比，捕捉流动性失衡后的清洗行为。",
            category="behavioral",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        high = data['high']
        low = data['low']
    
        # 价格差分绝对值
        price_diff = close.diff().abs()
        # 单位成交量价格冲击（价格变化/成交量对数）
        log_vol = np.log1p(volume)
        impact = price_diff / log_vol.replace(0, np.nan)
    
        # 近期波动性（过去5根K线的价格振幅均值）
        range_5 = (high - low).rolling(5).mean()
        # 当前冲击相对于历史冲击的异常程度
        impact_ma = impact.rolling(20).mean()
        impact_std = impact.rolling(20).std()
        z_score = (impact - impact_ma) / (impact_std + 1e-8)
    
        # 当冲击异常高且价格随后小幅回撤时（用下一根K线验证，实际因子中不能使用未来数据，此处为简化使用当前高低点）
        # 使用当前K线的上下影线长度判断
        upper_shadow = high - close
        lower_shadow = close - low
        shadow_ratio = (upper_shadow + 1e-8) / (lower_shadow + 1e-8)
    
        # 综合条件：异常冲击 + 长上影（空头清洗）或长下影（多头清洗）
        cond_dust_long = (z_score > 2) & (shadow_ratio > 2)  # 空头清洗，因子负
        cond_dust_short = (z_score > 2) & (shadow_ratio < 0.5)  # 多头清洗，因子正
    
        factor = pd.Series(0, index=data.index)
        factor[cond_dust_short] = 1.0
        factor[cond_dust_long] = -1.0
        return factor
