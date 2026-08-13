"""Cloud-synced factor: 布林带宽度"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata


class CloudBollingerWidth(BaseFactor):
    """Auto-localized from cloud factor library."""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="cloud_bollinger_width",
            name="BollingerBandWidth",
            display_name="布林带宽度",
            description="""布林带宽度指标，反映市场波动率状态。宽度收窄表示市场进入盘整，宽度扩张表示趋势可能来临。用于识别波动率压缩和突破机会。""",
            category="technical",
            subcategory="volatility",
            version="1.0.0",
            author="Cloud Factor Library",
            required_data_fields=["close"],
            dependencies=[],
        )

    def get_default_params(self):
        return {"period": 20, "num_std": 2.0}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
            period = self.params.get('period', 20)
            num_std = self.params.get('num_std', 2.0)
            sma = data['close'].rolling(period).mean()
            std = data['close'].rolling(period).std()
            upper = sma + num_std * std
            lower = sma - num_std * std
            bandwidth = ((upper - lower) / (sma + 1e-9)).fillna(0)
            result = bandwidth.rank(pct=True).fillna(0.5)
            return result
