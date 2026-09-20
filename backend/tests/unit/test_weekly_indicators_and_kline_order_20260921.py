# -*- coding: utf-8 -*-
"""[轮148 2026-09-21] 补周线/4H **数值**指标 + 对账 kline_deep 的"缺产物"错判。

## 一、模型点名要的"1w/4h 精确指标数值"
实盘 `missing_evidence` 里持续出现「1w精确指标数值缺失」「无 1w/4h 精确指标数值，结构判断仅基于区间位置」。
此前只给了 `trend_1w`（方向），没给数值 ⇒ 本轮补：
  · 周线：`rsi14_1w` / `atr14_1w_pct` / `dist_ema50_1w_pct` / `above_ema50_1w` / `ret_4w_pct` / `ema_trend_1w`
  · 4H：`macd_hist_4h` / `atr14_4h_pct`（原来只有 `rsi14_4h` / `ema_trend_4h`）
并把这些通过量化简报的 `indicators_1w` 块一并交给主脑。

## 二、kline_deep 的"缺产物"错判（顺序问题）
周期任务原顺序是「先 run_once 算六域 → 再落库 K 线深度产物」⇒ **每轮第一个 cycle 必然读不到产物**，
于是写一条 `missing` 信号，主脑上下文里就长期挂着「kline_deep 近72h无产物」，
而产物其实每个周期都写了 3 条（实测 01:28 / 01:43 日志）。
修法：**调换顺序**（先落库产物，再算六域），同轮即可见。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _clear_market_cache():
    from backend.services.analysis import context_pack as cp

    cp._MARKET_LAYER_CACHE.clear()
    yield
    cp._MARKET_LAYER_CACHE.clear()


def test_weekly_numeric_indicators_present():
    from backend.services.analysis import context_pack as cp

    p = cp.build("midlong_thesis", symbols=["BTC"])
    row = (p.layers.get("market") or {}).get("symbols", {}).get("BTC", {})
    for k in ("rsi14_1w", "atr14_1w_pct", "ema_trend_1w"):
        assert row.get(k) is not None, f"周线数值指标缺 {k}（模型点名要）"
    assert 0 <= float(row["rsi14_1w"]) <= 100
    assert float(row["atr14_1w_pct"]) >= 0


def test_4h_numeric_indicators_present():
    from backend.services.analysis import context_pack as cp

    p = cp.build("midlong_thesis", symbols=["BTC"])
    row = (p.layers.get("market") or {}).get("symbols", {}).get("BTC", {})
    for k in ("rsi14_4h", "macd_hist_4h", "atr14_4h_pct"):
        assert row.get(k) is not None, f"4H 数值指标缺 {k}（模型点名要）"


def test_quant_brief_md_carries_weekly_block():
    """组装给量化简报的 `_md` 必须带 `indicators_1w`（否则周线数值到不了预检）。"""
    src = (ROOT / "backend/services/analysis/context_pack.py").read_text(encoding="utf-8", errors="replace")
    assert '"indicators_1w"' in src, "_md 未带周线块"
    assert '"rsi": row.get("rsi14_1w")' in src, "周线 RSI 未接入 _md"


def test_analyst_tick_persists_kline_deep_before_run_once():
    """接线 ratchet：周期任务必须**先落库产物、再算六域**（否则 kline_deep 每轮首 cycle 误判缺产物）。"""
    src = (ROOT / "backend/main.py").read_text(encoding="utf-8", errors="replace")
    i_persist = src.find("_kd_n = persist_report(_kd_rep)")
    i_run = src.find("_analysts_run_once()")
    assert i_persist > 0 and i_run > 0, "周期任务缺少落库或六域调用"
    assert i_persist < i_run, "顺序错：应在 run_once **之前**落库 K 线深度产物"


def test_kline_deep_scorer_reads_products_now():
    """端到端：产物在库时，kline_deep 不得再被判 missing。"""
    from backend.services.analysts import scorers as SC

    sigs = SC.score_kline_deep(["BTC", "ETH", "SOL"])
    ok = [s for s in sigs if s.data_quality != "missing"]
    assert ok, f"库里有产物却全判 missing（窗口/表读法有问题）：{[s.symbol for s in sigs]}"
