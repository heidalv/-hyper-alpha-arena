"""AI因子: 反转波动率逆势 | 置信:50% | 短期（5日）涨幅过大且波动率（ATR/收盘价）处于90分位数以上时，容易出现反转。因子值负表示做空风险高（价格可能继续上涨），正表示做多风险高（价格可能继续下跌）？实际亏损模式是做空亏损，即价格在超涨高波动后继续上涨，所以因子应输出正。本因子设计为：超涨+高波动=>正信号（做多），超跌+高波动=>负信号（做空）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalVolatilityContrarian(BaseFactor):
    """短期（5日）涨幅过大且波动率（ATR/收盘价）处于90分位数以上时，容易出现反转。因子值负表示做空风险高（价格可能继续上涨），正表示做多风险高（价格可能继续下跌）？实际亏损模式是做空亏损，即价格在超涨高波动后继续上涨，所以因子应输出正。本因子设计为：超涨+高波动=>正信号（做多），超跌+高波动=>负信号（做空）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_revvol",
            name="Reversal Volatility Contrarian",
            display_name="反转波动率逆势",
            description="短期（5日）涨幅过大且波动率（ATR/收盘价）处于90分位数以上时，容易出现反转。因子值负表示做空风险高（价格可能继续上涨），正表示做多风险高（价格可能继续下跌）？实际亏损模式是做空亏损，即价格在超涨高波动后继续上涨，所以因子应输出正。本因子设计为：超涨+高波动=>正信号（做多），超跌+高波动=>负信号（做空）。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 5日收益率
        ret5 = data['close'].pct_change(5)
        # ATR（14日）
        tr = pd.DataFrame({'hl': data['high'] - data['low'],
                           'hc': abs(data['high'] - data['close'].shift(1)),
                           'lc': abs(data['low'] - data['close'].shift(1))}).max(axis=1)
        atr = tr.rolling(14).mean()
        # 波动率比率：ATR/收盘价
        vol_ratio = atr / data['close']
        # 波动率高分位数阈值（90分位）
        vol_threshold = vol_ratio.rolling(60).quantile(0.9)
        high_vol = vol_ratio > vol_threshold
        # 超涨：5日收益>4%（可调）
        overbought = ret5 > 0.04
        # 超跌：5日收益<-4%
        oversold = ret5 < -0.04
        # 信号：超涨且高波动 => 做多信号（+1），超跌且高波动 => 做空信号（-1）
        signal = pd.Series(0.0, index=data.index)
        signal[overbought & high_vol] = 1.0
        signal[oversold & high_vol] = -1.0
        return signal
