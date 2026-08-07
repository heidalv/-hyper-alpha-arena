"""AI因子: AI反向动量 | 置信:55% | 捕捉类似损失模式中'ai_reverse'的特征：短期价格急剧拉升后回落，或下跌后反弹，结合成交量异常。通过计算价格相对短期移动平均的偏离度与成交量放大比值，判断潜在反转风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class AIReverseMomentum(BaseFactor):
    """捕捉类似损失模式中'ai_reverse'的特征：短期价格急剧拉升后回落，或下跌后反弹，结合成交量异常。通过计算价格相对短期移动平均的偏离度与成交量放大比值，判断潜在反转风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_reverse_signal",
            name="AI Reverse Momentum",
            display_name="AI反向动量",
            description="捕捉类似损失模式中'ai_reverse'的特征：短期价格急剧拉升后回落，或下跌后反弹，结合成交量异常。通过计算价格相对短期移动平均的偏离度与成交量放大比值，判断潜在反转风险。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 短期均线
        ma5 = close.rolling(window=5).mean()
        # 价格对均线的偏离度
        dev = (close - ma5) / ma5
        # 成交量放大倍数（相对20日均量）
        vol_ma20 = volume.rolling(window=20).mean()
        vol_ratio = volume / vol_ma20
        # 反转信号：当偏离度绝对值大且成交量放大时，可能为反转点
        # 正向偏离（价格上涨过猛）后回落风险 -> 负值
        # 负向偏离（价格下跌过度）后反弹可能 -> 正值
        # 但根据错误模式中ai_reverse为亏损，多发生在long方向，因此更关注顶部反转
        # 这里输出对称：顶部反转给负值，底部反转给正值
        signal = np.where((dev > 0.02) & (vol_ratio > 1.5), -dev*10,  # 卖空信号
                          np.where((dev < -0.02) & (vol_ratio > 1.5), -dev*10, 0.0))  # 买多信号，但dev负，-dev为正
        # 限制范围
        result = pd.Series(signal, index=data.index).clip(-1, 1)
        result.fillna(0.0, inplace=True)
        return result
