"""AI因子: 低趋势强度 | 置信:70% | 计算14期ADX，当ADX低于20时视为震荡市，不利于趋势做多；因子值为 -(ADX-20)/20，范围[-1,1]，负值表示趋势弱应避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Low_ADX(BaseFactor):
    """计算14期ADX，当ADX低于20时视为震荡市，不利于趋势做多；因子值为 -(ADX-20)/20，范围[-1,1]，负值表示趋势弱应避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_low_adx",
            name="Low ADX",
            display_name="低趋势强度",
            description="计算14期ADX，当ADX低于20时视为震荡市，不利于趋势做多；因子值为 -(ADX-20)/20，范围[-1,1]，负值表示趋势弱应避免做多。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算TR
        high = data['high']
        low = data['low']
        close = data['close']
        prev_close = close.shift(1)
        tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
        # 计算+DM和-DM
        up_dm = high - high.shift(1)
        down_dm = low.shift(1) - low
        pos_dm = np.where((up_dm > down_dm) & (up_dm > 0), up_dm, 0)
        neg_dm = np.where((down_dm > up_dm) & (down_dm > 0), down_dm, 0)
        # 14期平滑
        period = 14
        atr = tr.ewm(span=period, adjust=False).mean()
        smooth_pos = pd.Series(pos_dm, index=data.index).ewm(span=period, adjust=False).mean()
        smooth_neg = pd.Series(neg_dm, index=data.index).ewm(span=period, adjust=False).mean()
        # 计算DI
        di_plus = 100 * smooth_pos / atr
        di_minus = 100 * smooth_neg / atr
        # 计算DX
        dx = 100 * np.abs(di_plus - di_minus) / (di_plus + di_minus + 1e-10)
        adx = dx.ewm(span=period, adjust=False).mean()
        # 因子：负值表示趋势弱
        result = - (adx - 20) / 20
        result = result.clip(-1, 1)
        return result.fillna(0)
