"""Cloud-synced factor: Kyle Lambda"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata


class CloudMicrostructureKyle(BaseFactor):
    """Auto-localized from cloud factor library."""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="cloud_microstructure_kyle",
            name="KyleLambda",
            display_name="Kyle Lambda",
            description="""Kyle's Lambda市场微观结构指标，衡量价格对交易量的敏感度。高Lambda表示市场流动性差(大单导致大波动)，低Lambda表示流动性好。""",
            category="behavioral",
            subcategory="orderflow",
            version="1.0.0",
            author="Cloud Factor Library",
            required_data_fields=["close", "volume"],
            dependencies=[],
        )

    def get_default_params(self):
        return {"lookback": 20}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
            lookback = self.params.get('lookback', 20)
            price_change = data['close'].diff().abs()
            sqrt_volume = np.sqrt(data['volume'] + 1e-9)
            rolling_price_vol = price_change.rolling(lookback).sum()
            rolling_sqrt_vol = sqrt_volume.rolling(lookback).sum()
            kyle_lambda = rolling_price_vol / (rolling_sqrt_vol + 1e-9)
            result = kyle_lambda.rank(pct=True).fillna(0.5)
            return result
