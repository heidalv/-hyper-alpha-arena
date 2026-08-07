"""AI因子: 量异动反转潜力 | 置信:60% | 计算14日RSI，当RSI处于超买超卖区域（<30或>70）且成交量出现异常放大（当前成交量比均值高2倍标准差）时，认为反转概率大，信号方向与RSI方向相反；其他情况信号中性。通过非线性映射输出[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Reversion_Potential_with_Volume_Anomaly(BaseFactor):
    """计算14日RSI，当RSI处于超买超卖区域（<30或>70）且成交量出现异常放大（当前成交量比均值高2倍标准差）时，认为反转概率大，信号方向与RSI方向相反；其他情况信号中性。通过非线性映射输出[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_revpot",
            name="Reversion Potential with Volume Anomaly",
            display_name="量异动反转潜力",
            description="计算14日RSI，当RSI处于超买超卖区域（<30或>70）且成交量出现异常放大（当前成交量比均值高2倍标准差）时，认为反转概率大，信号方向与RSI方向相反；其他情况信号中性。通过非线性映射输出[-1,1]。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        # RSI计算
        delta = data['close'].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(14, min_periods=14).mean()
        avg_loss = loss.rolling(14, min_periods=14).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        # 成交量异常
        vol_ma = data['volume'].rolling(20, min_periods=20).mean()
        vol_std = data['volume'].rolling(20, min_periods=20).std()
        vol_z = (data['volume'] - vol_ma) / (vol_std + 1e-10)
        # 信号：超买且大量 => 看空（负值）；超卖且大量 => 看多（正值）
        signal = pd.Series(np.where(
            (rsi > 70) & (vol_z > 2), -1.0,
            np.where(
                (rsi < 30) & (vol_z > 2), 1.0,
                0.0
            )
        ), index=data.index)
        # 加入平滑以避免突变
        result = signal.ewm(span=3).mean()
        result = result.fillna(0)
        return result
