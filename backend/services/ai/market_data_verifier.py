"""
确定性市场数据验证覆盖层（整改#11，对标 TradingAgents market_data_validator）。

在 LLM 输出之上叠加确定性市场数据快照，校验 LLM 引用的数字，防 LLM 幻觉具体数值
（如"RSI=72""布林带上轨 X""funding=0.05%"）。纯规则，无 LLM，完全可测。

用法：
    verifier = MarketDataVerifier()
    result = verifier.verify(llm_text, {"rsi": 68.3, "price": 65000, "funding": 0.0001})
    if not result.verified:
        # 用 result.corrected_values 替换下游使用的数值，而非信任 LLM 幻觉值
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    verified: bool
    discrepancies: List[dict] = field(default_factory=list)   # [{'metric','llm_claim','actual','delta','tolerance'}]
    corrected_values: Dict[str, float] = field(default_factory=dict)
    checked_metrics: List[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.verified:
            return f"[VERIFIED] {len(self.checked_metrics)} 项数值与快照一致"
        lines = [f"[MISMATCH] {len(self.discrepancies)} 项数值偏离快照:"]
        for d in self.discrepancies:
            lines.append(f"  - {d['metric']}: LLM={d['llm_claim']} 实际={d['actual']} Δ={d['delta']:.4g}")
        return "\n".join(lines)


# 指标别名 → 规范 key（含中英文），用于把 LLM 文本里的说法映射到 snapshot 键
_METRIC_ALIASES = {
    "rsi": "rsi",
    "macd": "macd",
    "adx": "adx",
    "atr": "atr",
    "价格": "price", "price": "price", "现价": "price", "最新价": "price",
    "funding": "funding", "资金费率": "funding", "资金费": "funding",
    "oi": "open_interest", "持仓量": "open_interest", "open interest": "open_interest",
    "ema": "ema", "布林上轨": "bb_upper", "布林下轨": "bb_lower",
    "bollinger upper": "bb_upper", "bollinger lower": "bb_lower",
    "成交量": "volume", "volume": "volume",
    "vwap": "vwap",
}

# 每个规范 key 的容差；("rel", x) 相对容差，("abs", x) 绝对容差
_DEFAULT_TOLERANCE = {
    "rsi": ("abs", 3.0),
    "adx": ("abs", 3.0),
    "macd": ("abs", 0.05),
    "atr": ("rel", 0.05),
    "price": ("rel", 0.005),
    "funding": ("abs", 0.00005),
    "open_interest": ("rel", 0.05),
    "ema": ("rel", 0.005),
    "bb_upper": ("rel", 0.005),
    "bb_lower": ("rel", 0.005),
    "volume": ("rel", 0.1),
    "vwap": ("rel", 0.005),
    "__default__": ("rel", 0.05),
}


class MarketDataVerifier:
    """LLM 输出的确定性验证器。"""

    def __init__(self, tolerance: Optional[Dict[str, tuple]] = None):
        self.tolerance = {**_DEFAULT_TOLERANCE, **(tolerance or {})}
        # 预编译：别名（按长度降序，先匹配更具体的多词别名）后跟数值
        aliases = sorted(_METRIC_ALIASES.keys(), key=len, reverse=True)
        alias_group = "|".join(re.escape(a) for a in aliases)
        # 形如 "RSI = 72", "价格: 65000", "funding 0.01%", "RSI 为 68.3"
        self._pat = re.compile(
            r"(?P<alias>" + alias_group + r")\s*(?:为|=|:|：|是|at|@)?\s*"
            r"(?P<value>-?\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<pct>%)?",
            re.IGNORECASE,
        )

    def extract_claims(self, llm_output: str) -> Dict[str, float]:
        """从 LLM 文本抽取数值声明 → {规范key: 值}（同一指标取首次出现）。"""
        claims: Dict[str, float] = {}
        if not llm_output:
            return claims
        for m in self._pat.finditer(llm_output):
            alias = m.group("alias").lower()
            key = _METRIC_ALIASES.get(alias)
            if not key or key in claims:
                continue
            raw = m.group("value").replace(",", "")
            try:
                val = float(raw)
            except ValueError:
                continue
            if m.group("pct"):
                val = val / 100.0
            claims[key] = val
        return claims

    def verify(self, llm_output: str, market_snapshot: dict) -> VerificationResult:
        """校验 LLM 文本中的数值声明是否与确定性快照一致。"""
        claims = self.extract_claims(llm_output)
        discrepancies: List[dict] = []
        corrected: Dict[str, float] = {}
        checked: List[str] = []

        snap = {str(k).lower(): v for k, v in (market_snapshot or {}).items()}
        for metric, claimed in claims.items():
            if metric not in snap:
                continue  # 快照没有该确定值，无法校验，跳过
            actual = snap[metric]
            try:
                actual_f = float(actual)
            except (TypeError, ValueError):
                continue
            checked.append(metric)
            corrected[metric] = actual_f
            delta = claimed - actual_f
            mode, tol = self.tolerance.get(metric, self.tolerance["__default__"])
            if mode == "rel":
                limit = abs(actual_f) * tol
            else:
                limit = tol
            if abs(delta) > limit:
                discrepancies.append({
                    "metric": metric,
                    "llm_claim": claimed,
                    "actual": actual_f,
                    "delta": delta,
                    "tolerance": limit,
                })

        return VerificationResult(
            verified=(len(discrepancies) == 0),
            discrepancies=discrepancies,
            corrected_values=corrected,
            checked_metrics=checked,
        )
