"""AI因子: 布林带位置因子 | 置信:55% | 计算价格在布林带中的相对位置，结合带宽变化判断是否为震荡无趋势状态。当价格位于布林带中轨附近且带宽较窄时，市场可能处于震荡(regime=unknown)，输出负值；当价格紧贴上轨或下轨且带宽扩张时，趋势启动，输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bollinger_Band_Position(BaseFactor):
    """计算价格在布林带中的相对位置，结合带宽变化判断是否为震荡无趋势状态。当价格位于布林带中轨附近且带宽较窄时，市场可能处于震荡(regime=unknown)，输出负值；当价格紧贴上轨或下轨且带宽扩张时，趋势启动，输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bbp",
            name="Bollinger Band Position",
            display_name="布林带位置因子",
            description="计算价格在布林带中的相对位置，结合带宽变化判断是否为震荡无趋势状态。当价格位于布林带中轨附近且带宽较窄时，市场可能处于震荡(regime=unknown)，输出负值；当价格紧贴上轨或下轨且带宽扩张时，趋势启动，输出正值。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算20日均线和标准差
        period = 20
        ma = close.rolling(period).mean()
        std = close.rolling(period).std()
        # 上轨和下轨
        upper = ma + 2 * std
        lower = ma - 2 * std
        # 价格在带内的位置，归一化到[0,1]，0=下轨，1=上轨
        position = (close - lower) / (upper - lower + 1e-10)
        # 带宽：上轨减下轨，用ATR归一化
        tr = pd.DataFrame({'hl': high - low, 'hc': abs(high - close.shift(1)), 'lc': abs(low - close.shift(1))}).max(axis=1)
        atr = tr.rolling(14).mean()
        bandwidth = (upper - lower) / (atr + 1e-10)
        # 带宽小(窄)表示震荡，带宽大(宽)表示趋势
        # 将位置映射到[-1,1]: 接近中轨(0.5)且带宽窄->负值；远离中轨且带宽宽->正值
        # 定义偏移度: 距离中轨的距离，标准化到[0,1]
        dist_from_mid = abs(position - 0.5) * 2  # 0~1
        # 带宽信号: 使用归一化，假设合理带宽在[1,4]之间
        bw_norm = (bandwidth - 1) / 3  # 大致映射到0~1
        bw_norm = np.clip(bw_norm, 0, 1)
        # 合成: 趋势强度 = 偏移度 * 带宽信号，震荡强度 = (1-偏移度)*(1-带宽信号)
        trend_strength = dist_from_mid * bw_norm
        osc_strength = (1 - dist_from_mid) * (1 - bw_norm)
        raw = trend_strength - osc_strength
        result = pd.Series(np.clip(raw, -1, 1), index=close.index)
        return result.fillna(0.0)
