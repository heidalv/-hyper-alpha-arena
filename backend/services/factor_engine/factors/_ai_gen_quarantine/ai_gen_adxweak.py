"""AI因子: 弱趋势检测 | 置信:60% | 计算简化版ADX（基于价格方向运动），当ADX低于20时认为趋势不明，输出负信号；高于25时输出正信号，值通过线性映射调整。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Weak_Trend_Detection(BaseFactor):
    """计算简化版ADX（基于价格方向运动），当ADX低于20时认为趋势不明，输出负信号；高于25时输出正信号，值通过线性映射调整。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_adxweak",
            name="Weak Trend Detection",
            display_name="弱趋势检测",
            description="计算简化版ADX（基于价格方向运动），当ADX低于20时认为趋势不明，输出负信号；高于25时输出正信号，值通过线性映射调整。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        period = 14
        # 计算+DM和-DM
        high = data['high']
        low = data['low']
        up_move = high.diff()
        down_move = -low.diff()
        pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        tr = pd.concat([high - low, np.abs(high - data['close'].shift()), np.abs(low - data['close'].shift())], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        pos_di = 100 * pd.Series(pos_dm, index=data.index).rolling(window=period).mean() / atr
        neg_di = 100 * pd.Series(neg_dm, index=data.index).rolling(window=period).mean() / atr
        dx = 100 * np.abs(pos_di - neg_di) / (pos_di + neg_di)
        adx = dx.rolling(window=period).mean()
        # 映射：小于20为-1，20-25线性，大于25为1
        signal = np.where(adx < 20, -1, np.where(adx > 25, 1, (adx - 20) / 5 * 2 - 1))
        return pd.Series(signal, index=data.index).fillna(0)
