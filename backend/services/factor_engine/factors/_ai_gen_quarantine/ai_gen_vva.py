"""AI因子: 波动率成交量异常因子 | 置信:65% | 当成交量异常放大但价格未延续趋势（如放量滞涨/滞跌），表明市场分歧加大或假突破，后续易反转。因子为负值时表示当前环境不利于持仓，正值表示量价配合良好。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityVolumeAnomaly(BaseFactor):
    """当成交量异常放大但价格未延续趋势（如放量滞涨/滞跌），表明市场分歧加大或假突破，后续易反转。因子为负值时表示当前环境不利于持仓，正值表示量价配合良好。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vva",
            name="Volatility Volume Anomaly",
            display_name="波动率成交量异常因子",
            description="当成交量异常放大但价格未延续趋势（如放量滞涨/滞跌），表明市场分歧加大或假突破，后续易反转。因子为负值时表示当前环境不利于持仓，正值表示量价配合良好。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ATR（波动率）
        high = data['high']
        low = data['low']
        close = data['close']
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(14, min_periods=1).mean()
        # 成交量指标
        vol = data['volume']
        vol_ma = vol.rolling(20, min_periods=1).mean()
        vol_std = vol.rolling(20, min_periods=1).std()
        # 标准化成交量异常
        vol_z = (vol - vol_ma) / (vol_std + 1e-8)
        # 价格变化方向：短期收益率
        ret = close.pct_change(5)
        # 核心逻辑：当成交量异常大（>1.5倍标准差）且价格变化方向与成交量方向背离（放量但价格未明显上涨或下跌）
        # 用成交量方向（当前价格相对20日均线）判断
        ma20 = close.rolling(20, min_periods=1).mean()
        price_pos = (close - ma20) / (ma20 + 1e-8)  # 价格相对位置
        # 构造信号
        # 放量且价格处于均线附近或反向移动时信号为负
        anomaly = (vol_z > 1.5) & (abs(price_pos) < 0.02)  # 放量但价格无明显趋势
        mean_rev_signal = -anomaly.astype(int)
        # 正常量价配合：缩量趋势延续则正信号
        vol_small = (vol_z < -0.5)
        trend_strong = (abs(ret) > 0.03)
        positive = vol_small & trend_strong
        mean_rev_signal = mean_rev_signal + positive.astype(int)
        # 归一化到[-1,1]
        result = pd.Series(np.clip(mean_rev_signal, -1, 1), index=data.index)
        return result
