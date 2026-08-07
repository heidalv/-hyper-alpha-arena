"""Cloud-synced factor: RSI 背离检测"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata


class CloudRsiDivergence(BaseFactor):
    """Auto-localized from cloud factor library."""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="cloud_rsi_divergence",
            name="RSIDivergence",
            display_name="RSI 背离检测",
            description="""检测价格与RSI之间的背离。价格创新高但RSI未创新高=熊市背离(看跌)，价格创新低但RSI未创新低=牛市背离(看涨)。""",
            category="technical",
            subcategory="momentum",
            version="1.0.0",
            author="Cloud Factor Library",
            required_data_fields=["close"],
            dependencies=[],
        )

    def get_default_params(self):
        return {"rsi_period": 14, "lookback": 20}

    def calculate(self, data: pd.DataFrame) -> pd.Series:
            period = self.params.get('rsi_period', 14)
            lookback = self.params.get('lookback', 20)
            delta = data['close'].diff()
            gain = delta.where(delta > 0, 0).rolling(period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
            rs = gain / (loss + 1e-9)
            rsi = 100 - (100 / (1 + rs))
            price_roc = data['close'].pct_change(lookback)
            rsi_roc = rsi.pct_change(lookback)
            result = np.where(
                (price_roc > 0.02) & (rsi_roc < -0.02), -1.0,
                np.where(
                    (price_roc < -0.02) & (rsi_roc > 0.02), 1.0,
                    0.0
                )
            )
            return pd.Series(result, index=data.index).fillna(0)
