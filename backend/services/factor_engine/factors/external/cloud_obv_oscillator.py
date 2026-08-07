"""Cloud-synced factor: OBV 震荡器"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata


class CloudObvOscillator(BaseFactor):
    """Auto-localized from cloud factor library."""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="cloud_obv_oscillator",
            name="OBVOscillator",
            display_name="OBV 震荡器",
            description="""基于OBV(能量潮)的震荡指标，衡量买卖压力变化。OBV与价格的背离是重要的趋势反转信号。""",
            category="technical",
            subcategory="volume",
            version="1.0.0",
            author="Cloud Factor Library",
            required_data_fields=["close", "volume"],
            dependencies=[],
        )

    def get_default_params(self):
        return {"fast_period": 10, "slow_period": 30}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
            close = data['close']
            volume = data['volume']
            direction = np.sign(close.diff())
            obv = (direction * volume).cumsum()
            fast_ma = obv.rolling(self.params.get('fast_period', 10)).mean()
            slow_ma = obv.rolling(self.params.get('slow_period', 30)).mean()
            result = ((fast_ma - slow_ma) / (slow_ma.abs() + 1e-9)).fillna(0)
            return result
