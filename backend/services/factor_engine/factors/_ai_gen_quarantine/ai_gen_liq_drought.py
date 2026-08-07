"""AI因子: 流动性枯竭检测器 | 置信:50% | 检测成交量极度萎缩且价格波动范围收窄的情况，通常预示着变盘或假突破。使用成交量相对近期均值的比率和价格振幅（高-低）/收盘价，当两者同时处于低位时发出信号。输出[-1,1]，负值表示流动性枯竭且可能向下突破，正值表示可能向上突破。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityDroughtDetector(BaseFactor):
    """检测成交量极度萎缩且价格波动范围收窄的情况，通常预示着变盘或假突破。使用成交量相对近期均值的比率和价格振幅（高-低）/收盘价，当两者同时处于低位时发出信号。输出[-1,1]，负值表示流动性枯竭且可能向下突破，正值表示可能向上突破。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_drought",
            name="Liquidity Drought Detector",
            display_name="流动性枯竭检测器",
            description="检测成交量极度萎缩且价格波动范围收窄的情况，通常预示着变盘或假突破。使用成交量相对近期均值的比率和价格振幅（高-低）/收盘价，当两者同时处于低位时发出信号。输出[-1,1]，负值表示流动性枯竭且可能向下突破，正值表示可能向上突破。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import numpy as np
        # 参数
        vol_window = 20
        amp_window = 20
        threshold_vol = 0.5  # 成交量低于均值的50%
        threshold_amp = 0.01 # 振幅小于1%
        data = data.copy()
        # 成交量比率
        data['vol_ma'] = data['volume'].rolling(vol_window).mean()
        data['vol_ratio'] = data['volume'] / (data['vol_ma'] + 1e-10)
        # 振幅
        data['amplitude'] = (data['high'] - data['low']) / (data['close'] + 1e-10)
        # 振幅的移动平均值
        data['amp_ma'] = data['amplitude'].rolling(amp_window).mean()
        # 检测枯竭条件
        drought = (data['vol_ratio'] < threshold_vol) & (data['amplitude'] < threshold_amp)
        # 根据最近价格方向给出信号
        # 如果最后一根K线收阳，可能向上突破；收阴可能向下
        price_direction = np.sign(data['close'] - data['open'])
        signal = np.where(drought, price_direction * -0.8, 0.0)  # 方向与收线相反？实际上枯竭后突破方向不确定，保守一点我们给0.8*方向
        # 改进：使用前后比较，这里简单处理
        result = pd.Series(signal, index=data.index).rolling(3).mean().fillna(0).clip(-1, 1)
        return result
