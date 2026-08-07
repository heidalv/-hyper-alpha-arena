"""AI因子: 市场状态未知指示器 | 置信:60% | 基于波动率与趋势强度识别市场是否处于低波动、无明确趋势的‘未知’状态。计算20日ATR与收盘价的比值（归一化波动率），以及价格相对于20日均线的偏离度。当两者均处于历史低位时，认为市场状态未知，输出负值（不利于做多）；反之输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Regime_Unknown_Indicator(BaseFactor):
    """基于波动率与趋势强度识别市场是否处于低波动、无明确趋势的‘未知’状态。计算20日ATR与收盘价的比值（归一化波动率），以及价格相对于20日均线的偏离度。当两者均处于历史低位时，认为市场状态未知，输出负值（不利于做多）；反之输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rui",
            name="Regime_Unknown_Indicator",
            display_name="市场状态未知指示器",
            description="基于波动率与趋势强度识别市场是否处于低波动、无明确趋势的‘未知’状态。计算20日ATR与收盘价的比值（归一化波动率），以及价格相对于20日均线的偏离度。当两者均处于历史低位时，认为市场状态未知，输出负值（不利于做多）；反之输出正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # ATR
        tr = pd.concat([data['high'] - data['low'],
                        abs(data['high'] - data['close'].shift(1)),
                        abs(data['low'] - data['close'].shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(20).mean()
        norm_vol = atr / data['close']
        # 价格偏离度
        ma20 = data['close'].rolling(20).mean()
        dev = abs(data['close'] - ma20) / data['close']
        # 合成指标：低波动+低偏离 => 未知状态
        # 对每个指标计算百分位排名（滚动窗口）
        def rolling_percentile(series, window):
            return series.rolling(window).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)
        vol_pct = rolling_percentile(norm_vol, 60)
        dev_pct = rolling_percentile(dev, 60)
        # 两者均低时（<0.3）为未知，因子取-1；否则取+1
        unknown = (vol_pct < 0.3) & (dev_pct < 0.3)
        result = np.where(unknown, -1.0, 1.0)
        return pd.Series(result, index=data.index).fillna(0)
