"""AI因子: 波动率异常信号 | 置信:70% | 识别波动率与价格行为不匹配的异常状态，类似于regime=unknown。计算当前波动率相对于历史波动率的分位数，同时检查价格是否处于窄幅区间，当波动率极低且价格窄幅震荡时，模型容易失效导致亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Regime_Anomaly(BaseFactor):
    """识别波动率与价格行为不匹配的异常状态，类似于regime=unknown。计算当前波动率相对于历史波动率的分位数，同时检查价格是否处于窄幅区间，当波动率极低且价格窄幅震荡时，模型容易失效导致亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_anomaly",
            name="Volatility Regime Anomaly",
            display_name="波动率异常信号",
            description="识别波动率与价格行为不匹配的异常状态，类似于regime=unknown。计算当前波动率相对于历史波动率的分位数，同时检查价格是否处于窄幅区间，当波动率极低且价格窄幅震荡时，模型容易失效导致亏损。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']

        # 计算真实波幅
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        # 20日ATR
        atr20 = tr.rolling(20).mean()
        # 价格区间宽度（最高最低差）相对收盘价
        range_width = (high - low) / close

        # 计算短期波动率（5日标准差）
        std5 = close.rolling(5).std() / close
        # 20日标准差
        std20 = close.rolling(20).std() / close
        # 波动率比率：短期/长期，当比率远小于1时，表示波动率萎缩
        vol_ratio = std5 / (std20 + 1e-6)

        # 同时价格区间宽度也低
        range_ma = range_width.rolling(20).mean()
        range_ratio = range_width / (range_ma + 1e-6)

        # 综合信号：波动率萎缩且价格区间极度狭窄
        raw = (1 - vol_ratio) * (1 - range_ratio)  # 两者都低时乘积大
        # 归一化到[-1,1]
        result = np.tanh(raw * 5.0 - 2.0)  # 通过tanh映射
        return result
