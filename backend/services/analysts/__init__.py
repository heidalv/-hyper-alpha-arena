# -*- coding: utf-8 -*-
"""分析师信号层：把六个分析域的判断**数值化**成一等 alpha 信号。

## 为什么要有这一层（用户原话，2026-09-20）
> 「五分析师（基本面/量价/舆情/资金流/宏观）→ 牛熊研究员对抗辩论 → 风控官（有否决权）→ 交易员。
>   分析师每日 thesis 数值化后直接作为一等 alpha 信号进混合打分——这就是"LLM 充分介入"的日频落地，不再是装饰。
>   应该是还有一个 k 线分析师，大概是这些。」

现状审计（轮129，实测）：旧 `trading_analysts.py` 的 6 分析师是**死路径**（`[Analysts]`/`[MasterController]`
日志 0、`caller=MasterController:synthesize` 0），`mlto/debate_layer.py` 只挂在**生产 0 调用点**的
`mlto/orchestrator.py` 上，`constitutional_veto` 拿不到 equity/margin，而"分析师 thesis 数值化入权重"
这一环**生产调用点 = 0**。本层就是补这一环，且只认**今天真的有数据的表**：

| 域 | 中文 | 数据源（实测新鲜度 2026-09-20 11:0x） |
|---|---|---|
| `fundamental` | 基本面 | `market.news_events`(category=exchange/regulatory/listing) + `market.macro_series`（**弱**：无 ETF 净流入/链上基础表，已显式标注缺源） |
| `technical` | 量价 | `market.crypto_klines`（1h/4h/1d） |
| `sentiment` | 舆情 | `market.news_events`（`ai_summary`+`affected_symbols`+方向+强度） |
| `flow` | 资金流 | `market.perp_funding` + `position_structure` + `liquidation_ticks` + `whale_activities` |
| `macro` | 宏观 | `analytics.strategic_reports`（1121 行，10:44 仍在写）+ `market.macro_series` |
| `kline_deep` | K线深度 | `analytics.kline_ai_analysis_logs`（**0 行**：KlineAnalyst 烧了 222 次 LLM/24h 却未落库 ⇒ 本域如实报"缺产物"） |

## 纪律（三轮"设计了没做"的教训）
1. **每个域必须要么给出数值信号，要么显式声明缺源**（`data_quality='missing'` + 原因），
   禁止"声明了但悄悄不产出" —— 由 `contract_report()` 与 ratchet 测试强制；
2. 信号必须**可解释**：每条带 `evidence`（样本量、分项、最新时间、来源表）；
3. 本层只做**取数与数值化**，不改任何交易判定；进不进决策由下游（context_pack / 混合打分）决定，
   开关：`ANALYST_SIGNALS_ENABLED`、`ANALYST_SIGNAL_LOOKBACK_H`。
"""
from __future__ import annotations

from .signals import AnalystSignal, DOMAIN_SPECS, DOMAINS
from .service import (  # noqa: F401
    ensure_table,
    run_once,
    latest_signals,
    contract_report,
    prompt_block,
)

__all__ = [
    "AnalystSignal", "DOMAIN_SPECS", "DOMAINS",
    "ensure_table", "run_once", "latest_signals", "contract_report", "prompt_block",
]
