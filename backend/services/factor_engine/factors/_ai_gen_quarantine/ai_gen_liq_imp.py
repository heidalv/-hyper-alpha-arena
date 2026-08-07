"""AI因子: 流动性冲击因子 | 置信:60% | 衡量大成交量对价格冲击的敏感度。当单位成交量引起价格大幅变动时，流动性不足，微小订单即易触发止损。因子值接近+1表示流动性冲击大，做空风险高；接近-1表示流动性好。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityImpactFactor(BaseFactor):
    """衡量大成交量对价格冲击的敏感度。当单位成交量引起价格大幅变动时，流动性不足，微小订单即易触发止损。因子值接近+1表示流动性冲击大，做空风险高；接近-1表示流动性好。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_imp",
            name="Liquidity Impact Factor",
            display_name="流动性冲击因子",
            description="衡量大成交量对价格冲击的敏感度。当单位成交量引起价格大幅变动时，流动性不足，微小订单即易触发止损。因子值接近+1表示流动性冲击大，做空风险高；接近-1表示流动性好。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        # 日内振幅
        range_ = high - low
        # 价格移动平均变化
        price_move = close.pct_change().abs()
        # 避免零成交量
        vol_safe = volume.replace(0, np.nan).ffill().fillna(volume.median())
        # 单位成交量价格冲击：价格变化 / 成交量（标准化）
        impact = price_move / vol_safe
        # 滚动平均平滑
        impact_ma = impact.rolling(10).mean()
        # 使用z-score归一化到[-1,1]
        mean_ = impact_ma.rolling(50).mean()
        std_ = impact_ma.rolling(50).std().replace(0, np.nan)
        z = (impact_ma - mean_) / std_
        result = np.clip(z / 3.0, -1, 1)  # 3倍标准差截断
        result = result.ffill().fillna(0)
        return pd.Series(result, index=data.index)
