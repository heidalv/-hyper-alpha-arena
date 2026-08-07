"""AI因子: 清理反弹量价背离 | 置信:60% | 模拟‘dust_cleanup’模式：价格快速下跌后伴随成交量萎缩但价格未创新低，随后可能出现反向清理。因子检测近期低点形成的量价背离（价格新低但成交量递减），发出反转看多信号（正值），反之价格新高且量递减则看空（负值）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class DustCleanupReverseDivergence(BaseFactor):
    """模拟‘dust_cleanup’模式：价格快速下跌后伴随成交量萎缩但价格未创新低，随后可能出现反向清理。因子检测近期低点形成的量价背离（价格新低但成交量递减），发出反转看多信号（正值），反之价格新高且量递减则看空（负值）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_dust_cln",
            name="Dust Cleanup Reverse Divergence",
            display_name="清理反弹量价背离",
            description="模拟‘dust_cleanup’模式：价格快速下跌后伴随成交量萎缩但价格未创新低，随后可能出现反向清理。因子检测近期低点形成的量价背离（价格新低但成交量递减），发出反转看多信号（正值），反之价格新高且量递减则看空（负值）。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close'].values
        volume = data['volume'].values
        lookback = 10
        if len(close) < lookback+1:
            return pd.Series(0.0, index=[data.index[-1]])
        # 取最近lookback+1根K线
        recent_close = close[-(lookback+1):]
        recent_vol = volume[-(lookback+1):]
        # 计算最近低点与次低点
        min_idx = np.argmin(recent_close)
        if min_idx == 0:
            return pd.Series(0.0, index=[data.index[-1]])
        # 价格是否新低？当前是否为最低？
        current_is_low = (recent_close[-1] == np.min(recent_close))
        # 成交量是否递减？比较最后一个区间与之前区间
        vol_before = np.mean(recent_vol[min_idx:min_idx+3]) if min_idx+3 <= len(recent_vol) else np.mean(recent_vol[min_idx:])
        vol_now = recent_vol[-1]
        # 背离：价格新低但成交量缩小
        if current_is_low and vol_now < vol_before * 0.8:
            score = 1.0 * min(1.0, (vol_before/vol_now - 1)/2.0)
        # 价格新高但成交量缩小（顶部背离）
        max_idx = np.argmax(recent_close)
        if max_idx == 0:
            return pd.Series(0.0, index=[data.index[-1]])
        current_is_high = (recent_close[-1] == np.max(recent_close))
        if current_is_high and vol_now < vol_before * 0.8:
            score = -1.0 * min(1.0, (vol_before/vol_now - 1)/2.0)
        else:
            score = 0.0
        return pd.Series(score, index=[data.index[-1]])
