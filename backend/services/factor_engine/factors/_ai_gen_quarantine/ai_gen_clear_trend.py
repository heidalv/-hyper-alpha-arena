"""AI因子: 趋势清晰度指数 | 置信:65% | 通过比较收盘价与短期均线的偏离度（标准化为Z-score）和ATR波动率比值，衡量当前市场是否存在清晰趋势。正值表示强趋势（适合顺向交易），负值表示噪音/震荡（regime=unknown风险高，应避免开仓或缩短持有期）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendClarityIndex(BaseFactor):
    """通过比较收盘价与短期均线的偏离度（标准化为Z-score）和ATR波动率比值，衡量当前市场是否存在清晰趋势。正值表示强趋势（适合顺向交易），负值表示噪音/震荡（regime=unknown风险高，应避免开仓或缩短持有期）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_clear_trend",
            name="Trend Clarity Index",
            display_name="趋势清晰度指数",
            description="通过比较收盘价与短期均线的偏离度（标准化为Z-score）和ATR波动率比值，衡量当前市场是否存在清晰趋势。正值表示强趋势（适合顺向交易），负值表示噪音/震荡（regime=unknown风险高，应避免开仓或缩短持有期）。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算20日均线和标准差
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        # Z-score: 当前价格相对于均线的偏离程度
        z = (close - ma20) / (std20 + 1e-10)
        # 计算ATR(14)并归一化波动率
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 波动率相对值：当前ATR与过去20日均值的比值
        vol_ratio = atr / (atr.rolling(20).mean() + 1e-10)
        # 综合：趋势清晰度 = z的绝对值乘以波动率比的倒数（波动越小、偏离越大的趋势越清晰）
        clarity = z.abs() / (vol_ratio + 1e-10)
        # 映射到[-1,1]，使用tanh压缩
        result = pd.Series( np.tanh(clarity - 1.5), index=close.index )
        return result
