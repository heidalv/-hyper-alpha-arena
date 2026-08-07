"""AI因子: 微观结构熵 | 置信:60% | 基于价格序列的符号变化模式计算熵，当价格频繁反转（高低点交替）时熵高，对应噪声环境输出-1；当价格持续同向运动时熵低，对应趋势环境输出+1。使用最近10个价格方向变化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MicrostructureEntropy(BaseFactor):
    """基于价格序列的符号变化模式计算熵，当价格频繁反转（高低点交替）时熵高，对应噪声环境输出-1；当价格持续同向运动时熵低，对应趋势环境输出+1。使用最近10个价格方向变化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_entropy_micro",
            name="Microstructure Entropy",
            display_name="微观结构熵",
            description="基于价格序列的符号变化模式计算熵，当价格频繁反转（高低点交替）时熵高，对应噪声环境输出-1；当价格持续同向运动时熵低，对应趋势环境输出+1。使用最近10个价格方向变化。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        from collections import Counter
        def entropy(series):
            diff = np.sign(series.diff().dropna())
            if len(diff) < 10:
                return 0.5
            seq = ''.join(['1' if d > 0 else '0' for d in diff.iloc[-10:]])
            cnt = Counter([seq[i:i+2] for i in range(len(seq)-1)])
            total = sum(cnt.values())
            ent = -sum((c/total)*np.log2(c/total) for c in cnt.values() if c>0)
            return ent
        result = data['close'].rolling(12).apply(entropy, raw=False)
        # normalize: max entropy for binary 2-char is 1, min 0
        result = 2 * (0.5 - result)  # map to [-1,1] roughly
        return result.clip(-1, 1)
