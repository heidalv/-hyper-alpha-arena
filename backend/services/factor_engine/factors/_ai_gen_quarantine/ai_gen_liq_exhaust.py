"""AI因子: 流动性衰竭反转 | 置信:60% | 捕捉市场在极窄波动后突然放量突破但迅速衰竭的反转。计算ATR（14周期）与价格变化率，若前N根K线ATR处于历史低位（窄幅），随后出现价格大幅偏离（>2倍ATR）但成交量异常放大后快速回归，则产生反转信号。使用ATR相对阈值和价格回归速度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityExhaustionReversal(BaseFactor):
    """捕捉市场在极窄波动后突然放量突破但迅速衰竭的反转。计算ATR（14周期）与价格变化率，若前N根K线ATR处于历史低位（窄幅），随后出现价格大幅偏离（>2倍ATR）但成交量异常放大后快速回归，则产生反转信号。使用ATR相对阈值和价格回归速度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_exhaust",
            name="Liquidity Exhaustion Reversal",
            display_name="流动性衰竭反转",
            description="捕捉市场在极窄波动后突然放量突破但迅速衰竭的反转。计算ATR（14周期）与价格变化率，若前N根K线ATR处于历史低位（窄幅），随后出现价格大幅偏离（>2倍ATR）但成交量异常放大后快速回归，则产生反转信号。使用ATR相对阈值和价格回归速度。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算ATR
        tr = np.maximum(data['high'] - data['low'], 
                        np.abs(data['high'] - data['close'].shift(1)),
                        np.abs(data['low'] - data['close'].shift(1)))
        atr = pd.Series(tr).rolling(14).mean()
        # 窄幅条件：最近5根ATR均值为过去50根ATR的0.5倍以下
        atr_short = atr.rolling(5).mean()
        atr_long = atr.rolling(50).mean()
        narrow_range = (atr_short / atr_long) < 0.5
        # 价格突变：当前收盘价相对前一根收盘价变化超过2倍ATR
        price_change = (data['close'] - data['close'].shift(1)) / data['close'].shift(1)
        vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
        # 突变且放量
        surge = (np.abs(price_change) > 2 * atr / data['close'].shift(1)) & (vol_ratio > 2)
        # 反转条件：突变后价格反向（下一根K线收盘价向均值回归）
        # 这里使用当前信号预测未来，需用滞后处理避免未来数据，但作为因子输出，我们使用当前条件信号
        # 实际上我们用前一根突变且当前价格反向
        signal = pd.Series(0, index=data.index)
        # 检测前一根为突变窄幅，当前价格反向
        cond = narrow_range.shift(1) & surge.shift(1) & (np.sign(price_change) != np.sign(price_change.shift(1)))
        signal[cond] = -np.sign(price_change.shift(1))  # 反向
        return signal.clip(-1, 1)
