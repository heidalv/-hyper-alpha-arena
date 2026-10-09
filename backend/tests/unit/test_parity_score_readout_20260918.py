# -*- coding: utf-8 -*-
"""[F350/F351 2026-09-18] Parity Score 的**读数可信度**不变量。

本次复查发现的两种"分数骗人"：

- **F350（未记录、本次修复）**：`available=False`（样本不足 / 回测无交易 ⇒ **根本没比对**）
  的提前 return **不传 `score`**，而数据类默认值是 **1.0** ⇒ "没测"被写成**满分**并落进
  `data/parity_score_history.jsonl` 与周报。实测 2026-09-12：5 条 nature 里 4 条如此，
  唯一真比对的 `swing` 是 0.0 ⇒ 报表里"满分"与"零分"并存，而满分那几条啥都没测。
- **F351（作者已在 63-72 行记录、刻意保留 score 口径）**：`avg_fill_price_dev` 与
  `avg_slippage` 合计权重 0.5，但回测侧基准是**固定常数 `SLIPPAGE`**（`parity_score.py:379-380`
  直接把常数写进 stats，**不是测出来的**）⇒ 实盘只要有任何真实成交噪声，这两个维度偏差
  必然打满 `_DEV_CAP=5.0` ⇒ `score = max(0, 1-0.25*5-0.25*5) = 0.0` **恒为 0（凡可比即 0）**。
  实测 8 周报告 / 14 个可比样本：`bt_value` 恒为 0.0003、`avg_fill_price_dev` 偏差 14/14 = 5.0。

本文件的第 3 组用例是**已知缺陷钉**：它断言"构造性 0 分"这一事实，从而在有人真正修好
这两个维度时**必然失败**，逼迫同步更新报告与口径说明（而不是让读数悄悄变好却没人知道）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.backtest_engine import parity_score as PS  # noqa: E402
from backend.services.backtest_engine.parity_score import (  # noqa: E402
    ParityMetric,
    ParityScoreResult,
    compute_parity_score,
)


# ───────────── ① F350：未比对不得序列化出数字分 ─────────────

def test_unavailable_result_serializes_no_score():
    r = ParityScoreResult(nature="swing", tier="mid", lookback_days=7,
                          computed_at="2026-09-18T00:00:00+00:00",
                          n_live_trades=0, n_bt_trades=0, available=False,
                          reason="样本不足跳过")
    d = r.to_dict()
    assert d["score"] is None, "未比对绝不能写出数字分（旧行为=1.0 满分）"
    assert d["score_measured"] is False
    assert d["available"] is False


def test_available_result_keeps_numeric_score():
    """修 F350 不得改变"真比对过"时的数值口径。"""
    r = ParityScoreResult(nature="swing", tier="mid", lookback_days=7,
                          computed_at="2026-09-18T00:00:00+00:00",
                          n_live_trades=30, n_bt_trades=40, available=True,
                          score=0.4321, reason="ok")
    d = r.to_dict()
    assert d["score"] == 0.4321 and d["score_measured"] is True


def test_default_score_is_none_not_one():
    """数据类默认值本身也不得是 1.0——它是 F350 的根源。"""
    r = ParityScoreResult(nature="x", tier="mid", lookback_days=7,
                          computed_at="t", n_live_trades=0, n_bt_trades=0)
    assert r.score is None


def test_first_early_return_path_has_no_score(monkeypatch):
    """真实路径①：实盘成交不足 MIN_LIVE_TRADES。"""
    monkeypatch.setattr(PS, "_fetch_live_closing_orders", lambda nature, days: [])
    r = compute_parity_score("swing", lookback_days=7)
    assert r.available is False
    assert r.score is None
    assert "样本不足" in r.reason
    assert r.to_dict()["score"] is None


def test_second_early_return_path_has_no_score(monkeypatch):
    """真实路径②：实盘够但回测回放没产出交易（本次实测最常发生的一种）。"""
    fake_orders = [{"symbol": "BTC", "filled_at": None, "pnl": 0.01},
                   {"symbol": "BTC", "filled_at": None, "pnl": 0.02},
                   {"symbol": "ETH", "filled_at": None, "pnl": -0.01},
                   {"symbol": "ETH", "filled_at": None, "pnl": 0.03},
                   {"symbol": "SOL", "filled_at": None, "pnl": 0.01}]
    monkeypatch.setattr(PS, "_fetch_live_closing_orders", lambda nature, days: list(fake_orders))
    monkeypatch.setattr(PS, "_load_reference_bars",
                        lambda *a, **k: [])
    monkeypatch.setattr(PS, "_compute_live_side", lambda orders, bars: {"win_rate": 0.3})
    monkeypatch.setattr(PS, "_run_backtest_side", lambda *a, **k: {})
    r = compute_parity_score("swing", lookback_days=7)
    assert r.available is False and r.score is None
    assert "回测回放未产出任何交易" in r.reason
    assert r.to_dict()["score"] is None


def test_pipeline_history_rows_do_not_invent_scores(monkeypatch, tmp_path):
    """周报/历史落盘行同样不得编造分数（F350 的传播面就是这里）。"""
    monkeypatch.setattr(PS, "_fetch_live_closing_orders", lambda nature, days: [])
    r = compute_parity_score("swing", lookback_days=7)
    d = r.to_dict()
    assert d["score"] is None and d["n_bt_trades"] == 0 and d["metrics"] == []


# ───────────── ② 已知构造性缺陷：F351 的算术（钉住，改好必失败） ─────────────

def test_f351_known_constant_baseline_pins_score_to_zero():
    """F351 已知缺陷钉：两个 0.25 权重维度的回测基准是常数 ⇒ 凡可比即 0 分。

    这不是"我以为"，而是 `parity_score.py:379-380` 的直接后果：
        stats["avg_fill_price_dev"] = float(SLIPPAGE)
        stats["avg_slippage"]        = float(SLIPPAGE)
    实盘只要有一点成交噪声（实测 live 0.0135~0.0211 vs 0.0003），偏差就打满 _DEV_CAP。
    断言这条算术，便于将来修好时**这个用例先红**，从而强制同步文档与口径。
    """
    live = {"avg_fill_price_dev": 0.021135, "avg_slippage": -0.008066}
    bt = {"avg_fill_price_dev": 0.0003, "avg_slippage": 0.0003}
    dev_sum = 0.0
    for key in ("avg_fill_price_dev", "avg_slippage"):
        dev = min(abs(live[key] - bt[key]) / max(abs(bt[key]), PS._EPS), PS._DEV_CAP)
        assert dev == PS._DEV_CAP, "实盘噪声必然打满偏差上限"
        dev_sum += PS.WEIGHTS[key] * dev
    score = max(0.0, 1.0 - dev_sum)
    assert score == 0.0, "score 恒为 0：与任何真实执行质量无关"
    # 这两个维度合计权重 0.5 —— 即使另外四个维度完美，也只够把 score 抬到 0.0
    assert PS.WEIGHTS["avg_fill_price_dev"] + PS.WEIGHTS["avg_slippage"] == 0.5


def test_historical_reports_show_constant_baseline():
    """用**仓库里已有的历史报告**复现：可比样本里 bt_value 恒为常数、偏差 14/14 打满。"""
    import glob
    import json
    files = sorted(glob.glob(str(ROOT / "data" / "parity_reports" / "*.json")))
    if not files:
        pytest.skip("无历史 parity 报告")
    bts, devs, scores = set(), [], []
    for f in files:
        rep = json.loads(Path(f).read_text(encoding="utf-8"))
        for nat, d in rep.items():
            if not d.get("available"):
                continue
            scores.append(d.get("score"))
            for m in d.get("metrics") or []:
                if m["name"] in ("avg_fill_price_dev", "avg_slippage"):
                    bts.add(round(float(m["bt_value"]), 6))
                    if m["name"] == "avg_fill_price_dev":
                        devs.append(float(m["deviation"]))
    assert bts == {0.0003}, f"回测侧基准应为固定常数 0.0003，实测 {bts}"
    assert devs and all(x == PS._DEV_CAP for x in devs), "实盘噪声下偏差应 14/14 打满"
    assert set(scores) == {0.0}, f"凡可比即 0 分，实测取值 {set(scores)}"


def test_core_freeze_metrics_still_usable():
    """F351 的作者缓解措施必须仍然有效：冻结判据用的是 core 指标而非全量 score。"""
    assert PS.CORE_FREEZE_METRICS == {"win_rate", "profit_factor", "max_drawdown"}
    assert PS.FREEZE_CONSECUTIVE_REQUIRED == 2
    assert "avg_fill_price_dev" not in PS.CORE_FREEZE_METRICS
    assert "avg_slippage" not in PS.CORE_FREEZE_METRICS


# ───────────── ③ F352：连续性判据把"样本不足"当空气 ─────────────

def test_f352_unavailable_week_neither_increments_nor_resets(monkeypatch):
    """F352 观察记录：不比对的一周**既不递增也不清零**计数。

    后果：「连续 2 周」实际可能是"隔周 2 次"（中间那周根本没比对），
    反之亦然——安全刹车的连续性语义与字面不符。此处只把行为钉住（不擅自改行为，
    冻结逻辑属实盘行为，需用户批准后再动）。
    """
    state = {"swing": {"count": 1, "frozen_at": ""}}
    monkeypatch.setattr(PS, "_load_freeze_state", lambda: dict(state))
    monkeypatch.setattr(PS, "_save_freeze_state", lambda s: state.update(s))
    # 模拟"这一周不可用"：compute_parity_score 提前 return，全程不调用 _record_freeze_breach
    monkeypatch.setattr(PS, "_fetch_live_closing_orders", lambda nature, days: [])
    monkeypatch.setattr(
        PS, "_record_freeze_breach",
        lambda *a, **k: pytest.fail("不可用的一周不得触碰冻结计数"))
    r = compute_parity_score("swing", lookback_days=7)
    assert r.available is False
    assert state["swing"]["count"] == 1, "计数原样保留（既不加也不清零）"
