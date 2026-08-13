"""Cloud-synced factor: VWAP 偏离度"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata


class CloudVwapDeviation(BaseFactor):
    """Auto-localized from cloud factor library."""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="cloud_vwap_deviation",
            name="VWAPDeviation",
            display_name="VWAP 偏离度",
            description="""当前价格相对于VWAP(成交量加权平均价)的偏离程度。正偏离表示价格高于平均成本，负偏离表示低于平均成本。""",
            category="technical",
            subcategory="volume",
            version="1.0.0",
            author="Cloud Factor Library",
            required_data_fields=["high", "low", "close", "volume"],
            dependencies=[],
        )

    def get_default_params(self):
        return {"lookback": 20}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
            typical_price = (data['high'] + data['low'] + data['close']) / 3
            lookback = self.params.get('lookback', 20)
            cum_tp_vol = (typical_price * data['volume']).rolling(lookback).sum()
            cum_vol = data['volume'].rolling(lookback).sum()
            vwap = cum_tp_vol / (cum_vol + 1e-9)
            result = ((data['close'] - vwap) / (vwap + 1e-9)).fillna(0)
            return result
