"""AI因子: 反向对冲净度 | 置信:50% | 基于价格突破后回撤的幅度和持仓清理特征，当价格突破近期区间后迅速回撤并伴随异常成交量，表明存在反向清洗（反向对冲净量）。计算布林带突破后的返回幅度与成交量突变。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReverseNettingStrength(BaseFactor):
    """基于价格突破后回撤的幅度和持仓清理特征，当价格突破近期区间后迅速回撤并伴随异常成交量，表明存在反向清洗（反向对冲净量）。计算布林带突破后的返回幅度与成交量突变。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_netting",
            name="Reverse Netting Strength",
            display_name="反向对冲净度",
            description="基于价格突破后回撤的幅度和持仓清理特征，当价格突破近期区间后迅速回撤并伴随异常成交量，表明存在反向清洗（反向对冲净量）。计算布林带突破后的返回幅度与成交量突变。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        # 布林带中轨和带宽（20周期，2倍标准差）
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        # 突破上轨或下轨的标记
        above_upper = close > upper
        below_lower = close < lower
        # 回撤幅度：突破后价格回归中轨的百分比
        # 对于上轨突破，回撤 = (close - ma20) / (upper - ma20 + 1e-10) ，小于0.5视为强烈回撤
        # 对于下轨突破，回撤 = (ma20 - close) / (ma20 - lower + 1e-10)
        retrace_upper = np.where(above_upper, (close - ma20) / (upper - ma20 + 1e-10), 0)
        retrace_lower = np.where(below_lower, (ma20 - close) / (ma20 - lower + 1e-10), 0)
        # 成交量突变（当前量相对前5周期均值）
        vol_ratio = volume / (volume.rolling(5).mean() + 1e-10)
        # 信号：突破后回撤>0.5且成交量激增>1.3，则输出反转信号
        # 上轨突破后回撤强烈 -> 做空反转（负值）
        short_signal = np.where((above_upper) & (retrace_upper > 0.5) & (vol_ratio > 1.3), -1.0, 0.0)
        long_signal = np.where((below_lower) & (retrace_lower > 0.5) & (vol_ratio > 1.3), 1.0, 0.0)
        result = pd.Series(short_signal + long_signal, index=data.index)
        return result.clip(-1,1)
