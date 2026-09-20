# -*- coding: utf-8 -*-
"""[轮134 2026-09-20] K线深度产物落库并接上消费 —— 不许再"烧了 LLM 不留痕"。

## 缺陷（实测）
`KlineAnalyst.analyze()` 每 24h 被调用 ~222 次，但 `_warmup_analyst_reports` **丢弃返回值**：
```
_analyst.analyze(_syms)     # ← 没人接
```
⇒ `alpha_analytics.kline_ai_analysis_logs` **0 行** ⇒ 六分析师里 `kline_deep` 域恒报 `missing`。

## 本轮
- 新增 `analysts/kline_deep_store.py`：把 `AnalystReport.signals` 逐币结构化落库
  （`analysis_result` = JSON：direction/signal/score/summary/recommendation/detail）；
- `_warmup_analyst_reports` 接住返回值并落库（日志带 `落库=N`）；
- `score_kline_deep` 优先解析结构化 JSON（`parsed=structured`），旧纯文本行才退回关键词
  近似（`parsed=keywords`，且显式标注为 weak —— 不冒充结构化）。

用户指令（不要为省预算牺牲正确性）在本轮体现为：**产物必须落库并被消费**，
而不是"反正没人看就不存"。
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.analysts import kline_deep_store as KDS  # noqa: E402


class _Rep:
    """最小 AnalystReport 替身（真实对象来自 trading_analysts.AnalystReport）。"""

    def __init__(self):
        self.signals = [
            {"symbol": "ZZTEST", "signal": "bullish", "score": 72,
             "detail": "多周期共振：1h 上穿 EMA21，4h 结构转多。", "data": {}},
            {"symbol": "ZZTEST2", "signal": "bearish", "score": 31,
             "detail": "日线跌破 EMA200，反弹乏力。", "data": {}},
        ]
        self.summary = "2 个币种方向分化"
        self.recommendation = "等待共振"


def test_persist_report_writes_structured_rows():
    from sqlalchemy import text

    from backend.database.connection import analytics_engine

    with analytics_engine.connect() as c:
        before = c.execute(text(
            "select count(*) from kline_ai_analysis_logs where symbol like 'ZZTEST%'")).scalar()
    n = KDS.persist_report(_Rep(), account_id=14, period="multi")
    assert n == 2, f"应写 2 行，实际 {n}"
    with analytics_engine.connect() as c:
        rows = c.execute(text(
            "select symbol, analysis_result from kline_ai_analysis_logs "
            "where symbol like 'ZZTEST%' order by id desc limit 2")).fetchall()
        after = c.execute(text(
            "select count(*) from kline_ai_analysis_logs where symbol like 'ZZTEST%'")).scalar()
    assert after == before + 2
    d = json.loads(rows[0][1])
    for key in ("direction", "signal", "score", "summary", "recommendation", "detail", "producer"):
        assert key in d, f"结构化字段缺 {key}"
    assert d["producer"].endswith("kline_deep_store.py")


def test_persist_disabled_switch(monkeypatch):
    monkeypatch.setenv("KLINE_DEEP_PERSIST_ENABLED", "false")
    assert KDS.persist_report(_Rep()) == 0, "开关关闭时不得写库"


def test_scorer_prefers_structured_payload():
    """有结构化产物时，`score_kline_deep` 必须走 structured（不是关键词近似）。"""
    from backend.services.analysts import scorers as SC

    sigs = [s for s in SC.score_kline_deep(["ZZTEST"]) if s.symbol == "ZZTEST"]
    assert sigs, "刚落库的 ZZTEST 应被 scorer 读到"
    s = sigs[0]
    assert s.evidence.get("parsed") == "structured", f"未走结构化路径：{s.evidence}"
    assert s.data_quality == "ok", "结构化产物应为 ok（关键词近似才是 weak）"
    assert s.score > 0, "bullish → 正分"


def test_warmup_wires_persist_and_reports_count():
    """接线 ratchet：预热函数必须接住返回值并落库，日志带计数（否则又会'烧了不留痕'）。"""
    src = (ROOT / "backend/services/full_auto_trading_service.py").read_text(
        encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            names.add(f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else ""))
    assert "persist_report" in names, "预热函数没有落库调用（产物又会被丢掉）"
    assert "_rep = _analyst.analyze(" in src or "_rep = analyst.analyze(" in src, \
        "没有接住 analyze() 的返回值"
    assert "落库=%d" in src, "日志必须报告落库条数（可观测）"


def test_kline_deep_contract_domain_now_deliverable():
    """契约视角：kline_deep 不再是"永远 missing"的域（有产物就该 delivered）。"""
    from backend.services.analysts import scorers as SC

    sigs = SC.score_kline_deep(["ZZTEST"])
    assert any(s.data_quality != "missing" for s in sigs), \
        "落库后 kline_deep 仍全 missing ⇒ 落库或读取链路断了"
