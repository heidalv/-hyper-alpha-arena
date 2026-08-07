"""AI因子: 波动率指数因子 | 置信:70% | 基于过去一段时间的高低价差和收盘价变化综合度量市场波动率。低波动环境容易产生假突破和频繁止损，适合做空波动率或观望。输出正值表示低波动（预示震荡），负值表示高波动（趋势有望延续）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility Index(BaseFactor):
    """基于过去一段时间的高低价差和收盘价变化综合度量市场波动率。低波动环境容易产生假突破和频繁止损，适合做空波动率或观望。输出正值表示低波动（预示震荡），负值表示高波动（趋势有望延续）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vi",
            name="Volatility Index",
            display_name="波动率指数因子",
            description="基于过去一段时间的高低价差和收盘价变化综合度量市场波动率。低波动环境容易产生假突破和频繁止损，适合做空波动率或观望。输出正值表示低波动（预示震荡），负值表示高波动（趋势有望延续）。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            # 计算真实波幅百分比
            tr = pd.concat([data['high'] - data['low'], 
                            (data['high'] - data['close'].shift()).abs(), 
                            (data['low'] - data['close'].shift()).abs()], axis=1).max(axis=1)
            atr = tr.rolling(14).mean()
            # 价格均值
            avg_price = (data['high'] + data['low'] + data['close']) / 3
            vol_ratio = atr / avg_price
            # 计算波动率的变化方向：当前波动率低于历史均值=低波动
            vol_ma = vol_ratio.rolling(30).mean()
            vol_std = vol_ratio.rolling(30).std()
            zscore = (vol_ma - vol_ratio) / (vol_std + 1e-10)  # 低波动时正值
            result = zscore.clip(-1, 1)
            return result.fillna(0)
