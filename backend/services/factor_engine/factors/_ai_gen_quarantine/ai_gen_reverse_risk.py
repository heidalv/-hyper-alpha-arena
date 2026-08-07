"""AI因子: 反转净头寸风险 | 置信:55% | 检测短期价格反转的强度与成交量确认。计算过去N根K线的最大回撤或反弹幅度，结合成交量异常放大，预测可能触发反向净头寸（reverse_netting）的亏损模式。值越接近1表示反转风险越高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReverseNettingRisk(BaseFactor):
    """检测短期价格反转的强度与成交量确认。计算过去N根K线的最大回撤或反弹幅度，结合成交量异常放大，预测可能触发反向净头寸（reverse_netting）的亏损模式。值越接近1表示反转风险越高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reverse_risk",
            name="Reverse Netting Risk",
            display_name="反转净头寸风险",
            description="检测短期价格反转的强度与成交量确认。计算过去N根K线的最大回撤或反弹幅度，结合成交量异常放大，预测可能触发反向净头寸（reverse_netting）的亏损模式。值越接近1表示反转风险越高。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算5周期内价格变化方向反转的强度
        ret = data['close'].pct_change(5)
        # 计算5日成交量变化率
        vol_ma = data['volume'].rolling(5).mean()
        vol_ratio = data['volume'] / (vol_ma + 1e-10)
        # 反转信号：最近5日涨跌幅绝对值大，且成交量异常放大
        abs_ret = ret.abs()
        # 使用z-score归一化
        ret_z = (abs_ret - abs_ret.rolling(20).mean()) / (abs_ret.rolling(20).std() + 1e-10)
        vol_z = (vol_ratio - vol_ratio.rolling(20).mean()) / (vol_ratio.rolling(20).std() + 1e-10)
        # 组合：反转强度 * 成交量确认
        risk = ret_z * vol_z
        # 取正值部分，并tanh压缩到[-1,1]
        risk = np.tanh(risk * 0.5)
        risk = risk.fillna(0)
        return risk
