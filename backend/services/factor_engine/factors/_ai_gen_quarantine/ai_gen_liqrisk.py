"""AI因子: 流动性风险因子 | 置信:60% | 当成交量相对于近期均值大幅萎缩且价格波动狭窄时，市场流动性降低，容易导致持仓超时或强制平仓。因子值越接近-1表示流动性风险越高，越接近+1表示流动性正常。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityRiskFactor(BaseFactor):
    """当成交量相对于近期均值大幅萎缩且价格波动狭窄时，市场流动性降低，容易导致持仓超时或强制平仓。因子值越接近-1表示流动性风险越高，越接近+1表示流动性正常。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liqrisk",
            name="Liquidity Risk Factor",
            display_name="流动性风险因子",
            description="当成交量相对于近期均值大幅萎缩且价格波动狭窄时，市场流动性降低，容易导致持仓超时或强制平仓。因子值越接近-1表示流动性风险越高，越接近+1表示流动性正常。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        # 计算过去20日平均成交量
        avg_vol = df['volume'].rolling(window=20, min_periods=1).mean()
        # 当前成交量与平均成交量的比值
        vol_ratio = df['volume'] / (avg_vol + 1e-10)
        # 计算价格波动范围（最高最低价差/收盘价）
        price_range = (df['high'] - df['low']) / (df['close'] + 1e-10)
        # 计算过去20日平均波动范围
        avg_range = price_range.rolling(window=20, min_periods=1).mean()
        # 相对波动率（当前波动范围/平均波动范围）
        range_ratio = price_range / (avg_range + 1e-10)
        # 综合风险：成交量萎缩（vol_ratio < 0.8）且波动狭窄（range_ratio < 0.7）时为高风险
        risk = -1.0 * ((vol_ratio < 0.8) & (range_ratio < 0.7)).astype(float)
        # 平滑处理并映射到[-1,1]区间，正常情况为+1
        result = pd.Series(1.0, index=df.index)
        result[risk == -1.0] = -1.0
        # 添加渐变：使用加权组合
        combined = 0.5 * (vol_ratio - 1) + 0.5 * (range_ratio - 1)
        combined = combined.clip(-1, 1)
        # 仅当信号强烈时使用极端值
        result = combined
        return result
