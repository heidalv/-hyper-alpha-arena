# -*- coding: utf-8 -*-
"""[F376 2026-09-18] `backtest_wisdom` 把**样本内极值**当"经验"喂给主脑（评分科学性又一例）。

## 实测链条（全部有代码/运行证据）

1. 运行态实测（`scripts/probe_mlto_learning_feeds.py`）：MLTO 主脑的
   `backtest_wisdom` 饲料**非空（870 字符）且被主脑读取**，内容含：
   ```
   - 回测最佳胜率: 92.9% (基于50次回测)
   - 回测最优夏普比率: 11.46
   - 建议止损: 5.7% / 建议止盈: 5.2% / 建议仓位: 6% / 建议杠杆: 8x
   ```
2. 来源 `backtest_insight_compiler._extract_risk_wisdom():230-251`：
   - `good_runs = [r for r in runs if r.sharpe_ratio > 0.5 and r.win_rate > 0.35]`
     —— **同一批样本内**筛选；无则退化为 `runs[:10]`；
   - `best_sharpe = max(...)`、`best_win_rate = max(...)*100` —— **取最大值**，
     **无样本量、无 OOS、无收缩/去膨胀**；
   - `sample_runs = len(good_runs)` —— 标签写"基于50次回测"，但那是**筛后条数**，
     不是每个 Sharpe 背后的样本量。
3. Sharpe 本身被**年化放大**：`live_pipeline_backtest_engine.py:1380`
   `sharpe = avg_r / std_r * math.sqrt(trades_per_year)` ⇒ 交易笔数少时
   √N 会显著抬高数值；11.46 是这类伪影的典型量级，不是"策略很好"的证据。
4. 该文本经 `_compile_risk()` 渲染后**直接进 prompt**，且本路径的 header
   （`:194`）只有"(自动生成)"，**没有**另一处 header（`:108`）的"仅供参考"字样。

⇒ 结论：主脑把"样本内最好的一次"当成经验参考，并据此给出风控建议（含 8x 杠杆）。
这是本报告"评分是否科学"主题下的又一例：**数字算得出来，但不代表它科学**。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.backtest_insight_compiler import BacktestInsightCompiler  # noqa: E402


def _run(sharpe, win, sl=None, tp=None, lev=None, pos=None):
    risk = {}
    if sl is not None:
        risk["stop_loss_pct"] = sl
    if tp is not None:
        risk["take_profit_pct"] = tp
    if lev is not None:
        risk["default_leverage"] = lev
    if pos is not None:
        risk["max_position_size"] = pos
    return SimpleNamespace(sharpe_ratio=sharpe, win_rate=win,
                           strategy_config={"risk_params": risk}, risk_params=risk)


def test_best_is_max_not_central_tendency():
    """锁住"取最大值"这一事实（若改成中位数/分位，本用例失败 ⇒ 请更新报告 §32）。"""
    c = BacktestInsightCompiler()
    runs = [_run(0.6, 0.40), _run(3.0, 0.55), _run(11.46, 0.929), _run(0.7, 0.42)]
    out = c._extract_risk_wisdom(runs, [])
    assert out["best_sharpe"] == pytest.approx(11.46), "当前口径是 max()"
    assert out["best_win_rate"] == pytest.approx(92.9)


def test_insample_filter_then_max_is_double_selection():
    """先按样本内表现筛，再取最大 —— 双重选择性偏差（两层都无 OOS 保护）。"""
    c = BacktestInsightCompiler()
    # 低于阈值的优秀 OOS 值不会被选中；选中的是样本内最好的那几个
    runs = [_run(0.2, 0.9), _run(0.4, 0.9), _run(9.9, 0.36), _run(1.0, 0.5)]
    out = c._extract_risk_wisdom(runs, [])
    assert out["sample_runs"] == 2, "只有 sharpe>0.5 且 win_rate>0.35 的进入候选"
    assert out["best_sharpe"] == pytest.approx(9.9)


def test_no_fallback_marks_absence():
    """完全无好样本时退化为 runs[:10] —— 这个退化**不体现在输出文本里**。"""
    c = BacktestInsightCompiler()
    runs = [_run(0.1, 0.2), _run(0.2, 0.3)]
    out = c._extract_risk_wisdom(runs, [])
    assert out["sample_runs"] == 2, "退化路径下 sample_runs 仍是 2（与'筛选后'不可区分）"


def test_compiled_text_has_no_caveat_for_extreme_values():
    """渲染出的文本**没有任何"存疑/样本内/不可预期"提示**（修好时本用例会失败）。"""
    c = BacktestInsightCompiler()
    txt = c._compile_risk({
        "optimal_stop_loss": "5.7%", "optimal_take_profit": "5.2%",
        "optimal_position_size": "6%", "optimal_leverage": "8x",
        "best_win_rate": 92.9, "best_sharpe": 11.46, "sample_runs": 50,
    })
    assert "11.46" in txt and "92.9" in txt
    for kw in ("存疑", "样本内", "不可预期", "上限", "仅供参考"):
        assert kw not in txt, f"文本出现了『{kw}』⇒ 可能已加保护，请更新报告 §32"


def test_sharpe_annualization_source_pin():
    """Sharpe 的 √N 年化是小样本放大的机制来源，锁住它以便将来换口径时被提醒。"""
    src = (ROOT / "backend/services/live_pipeline_backtest_engine.py").read_text(
        encoding="utf-8", errors="replace")
    assert "math.sqrt(trades_per_year)" in src, "Sharpe 年化口径变了 ⇒ 需重估极值含义（§32）"


def test_wisdom_header_caveat_inconsistency():
    """本路径 header 缺"仅供参考"（另一处有）——记录这个不一致。"""
    src = (ROOT / "backend/services/backtest_insight_compiler.py").read_text(
        encoding="utf-8", errors="replace")
    assert "回测进化经验参考 (自动生成)" in src, "header 文案变了 ⇒ 复核 §32"
    assert "回测进化经验参考 (自动生成，仅供参考)" in src, "另一处 header 也变了"
