"""AI因子: 市场微观结构失衡 | 置信:55% | 通过日内价格位置与成交量加权的价格中心对比，度量买卖力量不均衡程度。正因子值表示买方主导，负值表示卖方主导，极端值暗示可能发生异常交易行为（如ai_reverse或master_running_close）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MarketMicrostructureImbalance(BaseFactor):
    """通过日内价格位置与成交量加权的价格中心对比，度量买卖力量不均衡程度。正因子值表示买方主导，负值表示卖方主导，极端值暗示可能发生异常交易行为（如ai_reverse或master_running_close）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_market_microstructure",
            name="MarketMicrostructureImbalance",
            display_name="市场微观结构失衡",
            description="通过日内价格位置与成交量加权的价格中心对比，度量买卖力量不均衡程度。正因子值表示买方主导，负值表示卖方主导，极端值暗示可能发生异常交易行为（如ai_reverse或master_running_close）。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # 计算成交量加权价格VWAP近似（使用高低收均价代替）
        typical_price = (data['high'] + data['low'] + data['close']) / 3
        # 计算成交量加权中心的偏离
        vwap = (typical_price * data['volume']).rolling(20, min_periods=1).sum() / data['volume'].rolling(20, min_periods=1).sum().replace(0, 1e-10)
        # 当前收盘价相对VWAP的偏离，标准化至[-1,1]
        dev = (data['close'] - vwap) / (data['close'] + 1e-10) * 100
        # 使用clip限制极端值，并映射
        result = np.clip(dev * 0.1, -1, 1)
        # 同时考虑日内振幅：如果振幅很小但偏离很大，更可疑
        amp = (data['high'] - data['low']) / data['close'].replace(0, 1e-10)
        # 当振幅极小时，因子信号加强
        small_amp = amp < 0.005  # 0.5%
        result = pd.Series(np.where(small_amp, result * 1.5, result))
        return result.clip(-1, 1)
        # 注意：需要import numpy as np
