# -*- coding: utf-8 -*-
"""分析师信号的**契约与模型**（单一事实来源）。

`DOMAIN_SPECS` 是这套体系唯一的"承诺清单"：谁负责什么、数据来自哪张表、缺源时怎么报。
任何"说做了其实没做"的回归，都会在 `service.contract_report()` 与
`backend/tests/unit/test_analyst_signals_20260920.py` 里被抓住。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

#: 信号取值范围（一等 alpha 信号统一口径：[-1, 1]，正=看多）
SCORE_MIN, SCORE_MAX = -1.0, 1.0

#: 数据质量：ok=源齐全 / weak=源可用但有已知缺口 / missing=缺产物（必须写 reason）
QUALITY_OK = "ok"
QUALITY_WEAK = "weak"
QUALITY_MISSING = "missing"


@dataclass
class AnalystSignal:
    """一个分析师对一个标的（或全局 `*`）的数值判断。"""

    domain: str
    symbol: str                      # '*' = 全局（宏观）
    score: float                     # [-1, 1]
    confidence: float                # [0, 1]
    data_quality: str                # ok | weak | missing
    n_samples: int = 0
    producer: str = ""               # 计算它的生产代码位置（可回溯）
    as_of: str = ""                  # 数据最新时间（ISO 或 'YYYY-MM-DD HH:MM:SS'）
    evidence: Dict[str, Any] = field(default_factory=dict)
    missing_sources: List[str] = field(default_factory=list)
    reason: str = ""                 # data_quality != ok 时必须写清

    def __post_init__(self) -> None:
        self.score = _clamp(self.score, SCORE_MIN, SCORE_MAX)
        self.confidence = _clamp(self.confidence, 0.0, 1.0)
        if self.data_quality == QUALITY_MISSING and not self.reason:
            raise ValueError(f"{self.domain}: data_quality=missing 必须给出 reason（禁止静默缺失）")

    def to_row(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence_json"] = d.pop("evidence")
        return d


def _clamp(v: Any, lo: float, hi: float) -> float:
    try:
        f = float(v)
    except Exception:  # noqa: BLE001
        return 0.0
    if f != f:                       # NaN
        return 0.0
    return max(lo, min(hi, f))


#: 六域契约：`tables` 是**今天必须真实存在**的数据源；`missing_when` 写明缺源判定，
#: `role` 写它向决策链路提供的判断口径。改这里 = 改承诺，必须同步测试。
DOMAIN_SPECS: Dict[str, Dict[str, Any]] = {
    "fundamental": {
        "cn": "基本面分析师",
        "role": "ETF/监管/上所等基本面事件 + 利率环境 → 中期多空倾向",
        "tables": ["market.news_events", "market.macro_series"],
        "score_scale": "[-1,1]：事件方向×强度 加权 + 利率环境惩罚项",
        "missing_when": "两表都取不到数据",
        "known_gaps": ["无 ETF 净流入结构化表（只能从新闻类别近似）", "无链上基本面表（活跃地址/手续费收入）"],
    },
    "technical": {
        "cn": "量价分析师",
        "role": "多周期趋势/超买超卖/区间位置 → 短线方向与强度",
        "tables": ["market.crypto_klines"],
        "score_scale": "[-1,1]：EMA 结构 + RSI14 + 24h 区间位置 复合",
        "missing_when": "1h/4h/1d 全无 K 线",
        "known_gaps": [],
    },
    "sentiment": {
        "cn": "舆情分析师",
        "role": "新闻情绪（AI 摘要+方向+强度）按标的总和 → 情绪面多空",
        "tables": ["market.news_events"],
        "score_scale": "[-1,1]：Σ(方向×强度)/Σ(强度)",
        "missing_when": "近窗口内无任何已标注新闻",
        "known_gaps": ["社交情绪（Twitter/Telegram）不在此表，仅新闻源"],
    },
    "flow": {
        "cn": "资金流分析师",
        "role": "资金费率拥挤度 + 持仓量变化 + 多空比 + 清算 + 鲸鱼流向 → 资金面多空（含反向指标）",
        "tables": ["market.perp_funding", "market.position_structure",
                   "market.liquidation_ticks", "market.whale_activities"],
        "score_scale": "[-1,1]：拥挤度取反向、清算取反向、鲸鱼顺向",
        "missing_when": "四表都取不到数据",
        "known_gaps": ["鲸鱼表为聚合事件流，覆盖不全"],
    },
    "macro": {
        "cn": "宏观分析师",
        "role": "战略报告（周期阶段/宏观偏向/置信度/风险预算）+ 利率序列 → 全局风险偏好",
        "tables": ["analytics.strategic_reports", "market.macro_series"],
        "score_scale": "[-1,1]：macro_bias 方向 × macro_confidence，受 risk_budget_adjustment 调节",
        "missing_when": "战略报告表空或超过 stale_hours",
        "known_gaps": [],
    },
    "kline_deep": {
        "cn": "K线深度分析师",
        "role": "K线深度 LLM 分析（逐标的形态/结构解读）→ 结构与形态判断",
        "tables": ["analytics.kline_ai_analysis_logs"],
        "score_scale": "[-1,1]：LLM 结构化结论（当前无产物 ⇒ 如实报 missing）",
        "missing_when": "产物表 0 行或过期",
        "known_gaps": ["KlineAnalyst 每 24h 烧 222 次 LLM 调用，但产物**未落库**（表 0 行）⇒ 全丢"],
    },
}

#: 顺序即"日频落地"的推荐阅读顺序（宏观→基本面→量价→资金流→舆情→K线深度）
DOMAINS: List[str] = list(DOMAIN_SPECS.keys())
