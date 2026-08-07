"""AI因子: 趋势脆弱性指数 | 置信:60% | 识别趋势即将反转的脆弱点，模拟trend_review_close亏损模式。通过计算价格与长期均线的偏离度、短期动量衰减以及成交量萎缩来度量趋势脆弱性。返回-1表示强下跌脆弱（看空），+1表示强上涨脆弱（看多），0为中性。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendVulnerabilityIndex(BaseFactor):
    """识别趋势即将反转的脆弱点，模拟trend_review_close亏损模式。通过计算价格与长期均线的偏离度、短期动量衰减以及成交量萎缩来度量趋势脆弱性。返回-1表示强下跌脆弱（看空），+1表示强上涨脆弱（看多），0为中性。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_vuln",
            name="Trend Vulnerability Index",
            display_name="趋势脆弱性指数",
            description="识别趋势即将反转的脆弱点，模拟trend_review_close亏损模式。通过计算价格与长期均线的偏离度、短期动量衰减以及成交量萎缩来度量趋势脆弱性。返回-1表示强下跌脆弱（看空），+1表示强上涨脆弱（看多），0为中性。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        # 计算趋势强度指标
        ma50 = close.rolling(50).mean()
        # 价格偏离度
        deviation = (close - ma50) / ma50
        # 短期动量：最近5日收益率
        ret5 = close.pct_change(5)
        # 成交量萎缩：当前成交量低于过去20日均值的80%
        vol_ma20 = volume.rolling(20).mean()
        vol_shrink = volume < (vol_ma20 * 0.8)
        # 趋势脆弱性：当偏离度大但动量减弱且成交量萎缩时，趋势脆弱
        # 上涨脆弱：偏离度>0.05且正动量减弱（ret5 < ret5.shift(1)）且成交量萎缩
        up_vuln = (deviation > 0.05) & (ret5 < ret5.shift(1)) & vol_shrink
        # 下跌脆弱：偏离度<-0.05且负动量减弱（ret5 > ret5.shift(1)）且成交量萎缩
        down_vuln = (deviation < -0.05) & (ret5 > ret5.shift(1)) & vol_shrink
        # 映射到[-1,1]
        result = pd.Series(0, index=close.index)
        result[up_vuln] = -1.0  # 上涨脆弱意味着即将下跌，看空
        result[down_vuln] = 1.0  # 下跌脆弱意味着即将上涨，看多
        return result
