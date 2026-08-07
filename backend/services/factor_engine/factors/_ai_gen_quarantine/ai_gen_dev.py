"""AI因子: 偏移状态指标 | 置信:65% | 基于价格与移动平均的偏离程度以及ATR波动率，判断市场是否处于明确的趋势状态（高偏离高波动）还是未知盘整状态（低偏离低波动）。返回[-1,1]，正值表示强趋势（适合交易），负值表示未知盘整（避免交易）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class DeviationRegimeIndicator(BaseFactor):
    """基于价格与移动平均的偏离程度以及ATR波动率，判断市场是否处于明确的趋势状态（高偏离高波动）还是未知盘整状态（低偏离低波动）。返回[-1,1]，正值表示强趋势（适合交易），负值表示未知盘整（避免交易）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_dev",
            name="Deviation Regime Indicator",
            display_name="偏移状态指标",
            description="基于价格与移动平均的偏离程度以及ATR波动率，判断市场是否处于明确的趋势状态（高偏离高波动）还是未知盘整状态（低偏离低波动）。返回[-1,1]，正值表示强趋势（适合交易），负值表示未知盘整（避免交易）。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        # 计算20日简单移动平均
        ma = data['close'].rolling(window=20, min_periods=1).mean()
        # 价格对数的偏离度（标准化）
        log_return = np.log(data['close'] / ma)
        # 计算20日ATR（平均真实波幅）作为波动率
        high, low, close = data['high'], data['low'], data['close']
        tr = pd.concat([high - low,
                        (high - close.shift(1)).abs(),
                        (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(window=20, min_periods=1).mean()
        # 价格相对于移动平均的百分比偏离
        pct_dev = (data['close'] - ma) / ma
        # 使用偏离度的绝对值和ATR（用最新收盘价归一化）构造趋势强度
        norm_dev = pct_dev.abs() / (pct_dev.abs().rolling(60, min_periods=1).max() + 1e-10)
        norm_atr = atr / data['close']
        norm_atr = norm_atr / (norm_atr.rolling(60, min_periods=1).max() + 1e-10)
        trend_intensity = (norm_dev + norm_atr) / 2  # 0到1
        # 映射到[-1,1]，使用tanh增强区分
        result = 2 * (trend_intensity - 0.5)
        result = result.fillna(0)
        return result.clip(-1, 1)
