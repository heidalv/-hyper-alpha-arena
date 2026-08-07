"""AI因子: 布林带收缩假突破识别 | 置信:60% | 布林带宽度收缩至低位后价格向上突破，但成交量未明显放大，往往是假突破，随后价格回落。亏损中JTO多次亏损可能由假突破触发。因子检测突破时量能确认，若缺乏量能则输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class BollingerBandSqueezeFakeoutDetector(BaseFactor):
    """布林带宽度收缩至低位后价格向上突破，但成交量未明显放大，往往是假突破，随后价格回落。亏损中JTO多次亏损可能由假突破触发。因子检测突破时量能确认，若缺乏量能则输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bb_squeeze_fake",
            name="Bollinger Band Squeeze Fakeout Detector",
            display_name="布林带收缩假突破识别",
            description="布林带宽度收缩至低位后价格向上突破，但成交量未明显放大，往往是假突破，随后价格回落。亏损中JTO多次亏损可能由假突破触发。因子检测突破时量能确认，若缺乏量能则输出负值。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        volume = data['volume']
        # 布林带
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = ma + 2 * std
        lower = ma - 2 * std
        bandwidth = (upper - lower) / ma
        # 识别收缩：带宽处于20期低百分位
        bw_percentile = bandwidth.rolling(60).rank(pct=True)
        squeeze = (bw_percentile < 0.2).astype(float)  # 收缩区域
        # 突破上轨
        break_up = (close > upper).astype(float)
        # 成交量确认：当日成交量 > 20日均量的1.2倍
        vol_avg = volume.rolling(20).mean()
        vol_confirm = (volume > vol_avg * 1.2).astype(float)
        # 假突破：在收缩区域突破上轨但无成交量确认
        fakeout = squeeze * break_up * (1 - vol_confirm)
        # 平滑后映射为负值，表示假突破风险
        result = -1.0 * fakeout.rolling(3).max().fillna(0).clip(-1, 1)
        return result
