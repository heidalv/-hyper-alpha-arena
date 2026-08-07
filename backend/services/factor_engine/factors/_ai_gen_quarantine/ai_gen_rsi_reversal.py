"""AI因子: RSI超买反转风险因子 | 置信:65% | 针对多头亏损模式中的反转信号（如ai_reverse），使用RSI和价格相对均线的位置。当RSI高于70且收盘价高于20日均线时，返回负值表示反转风险大；反之RSI低于30且低于均线时返回正值。中间区域平滑过渡。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RSI_Overbought_Reversal_Risk(BaseFactor):
    """针对多头亏损模式中的反转信号（如ai_reverse），使用RSI和价格相对均线的位置。当RSI高于70且收盘价高于20日均线时，返回负值表示反转风险大；反之RSI低于30且低于均线时返回正值。中间区域平滑过渡。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rsi_reversal",
            name="RSI Overbought Reversal Risk",
            display_name="RSI超买反转风险因子",
            description="针对多头亏损模式中的反转信号（如ai_reverse），使用RSI和价格相对均线的位置。当RSI高于70且收盘价高于20日均线时，返回负值表示反转风险大；反之RSI低于30且低于均线时返回正值。中间区域平滑过渡。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        period = 14
        delta = close.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        # 计算20日均线
        ma20 = close.rolling(20).mean()
        # 构造信号：rsi>70且价格高于均线时接近-1；rsi<30且价格低于均线时接近1
        # 用双曲正切或线性组合
        above_ma = (close > ma20).astype(float) * 2 - 1  # 1表示在均线上方，-1下方
        rsi_scaled = (rsi - 50) / 50  # -1到1之间
        # 组合：当rsi极端且价格偏离均线时，信号强烈
        signal = -rsi_scaled * above_ma  # 例如rsi高（正值）且价格在均线上（正值），乘积正，负号得负
        # 平滑处理
        result = np.tanh(signal * 2)  # 映射到接近-1/1
        return pd.Series(result, index=data.index).fillna(0)
