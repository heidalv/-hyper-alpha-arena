"""AI因子: 价格回归因子 | 置信:65% | 衡量价格相对于近期均线的偏离程度，结合波动率调整。偏离越大，均值回归概率越高，在震荡市场中容易导致逆势亏损。输出正值表示超买（看空），负值表示超卖（看多）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Price Reversion(BaseFactor):
    """衡量价格相对于近期均线的偏离程度，结合波动率调整。偏离越大，均值回归概率越高，在震荡市场中容易导致逆势亏损。输出正值表示超买（看空），负值表示超卖（看多）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pr",
            name="Price Reversion",
            display_name="价格回归因子",
            description="衡量价格相对于近期均线的偏离程度，结合波动率调整。偏离越大，均值回归概率越高，在震荡市场中容易导致逆势亏损。输出正值表示超买（看空），负值表示超卖（看多）。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            # 计算20日均线偏离
            ma20 = data['close'].rolling(20).mean()
            deviation = (data['close'] - ma20) / ma20
            # 波动率调整（使用ATR比例）
            tr = pd.concat([data['high'] - data['low'], 
                            (data['high'] - data['close'].shift()).abs(), 
                            (data['low'] - data['close'].shift()).abs()], axis=1).max(axis=1)
            atr = tr.rolling(14).mean()
            # 标准化偏离度
            score = deviation / (atr / ma20 + 1e-10)
            # 滚动Z-score
            score_ma = score.rolling(40).mean()
            score_std = score.rolling(40).std()
            zscore = (score - score_ma) / (score_std + 1e-10)
            result = zscore.clip(-1, 1)
            return result.fillna(0)
