# -*- coding: utf-8 -*-
"""[§72 / 决策 P17] 回撤熔断的**拒单语义**契约测试（锁现状 + 锁可见性）。

实证（2026-09-10，`_audit_ml/Z183/Z184`）：
  * `PB_FREEZE_ENABLED=false`（按用户指令"冻结机制整体删除"）只关掉**冻结记账**，
    `portfolio_budget.evaluate_open()` 里的回撤分支**仍然 `return False` 拒单**；
  * `_freeze()` 在开关关闭时打印「继续交易」⇒ **日志与行为互相矛盾**；
  * 该判据在亏损期**不自愈**：禁止开仓 → 无新平仓 → 累计曲线不变 ⇒ dd/σ 恒定。
    实测 `midlong drawdown=18.99σ>10σ` 自 14:23 起连续 3 小时、**110 次**成为中长线开仓 TOP1 拦截；
    独立复算（30 天窗口、168 笔已平仓 midlong）：σ=$9.87、回撤=$187.47 ⇒ **18.99σ**（与运行期一致）。

本测试锁定三件事（**不改变任何裁决**）：
  1. 现状语义：dd>cap ⇒ 拒单，且**与 PB_FREEZE_ENABLED 无关**（决策前不得被"以为已关闭"误导）；
  2. 拒单原因里必须带 `σ` 与阈值（可判定"何时会解除"）；
  3. 开关关闭时必须有**限流 WARNING**（把"日志说继续交易、实际在拒单"的矛盾暴露出来）。

[2026-09-11 同步 · 用户指令变更] 用户在「模拟账户交易还配置全局冻结？」之后，
`portfolio_budget.evaluate_open()` 增加了 **paper 短路**：`mode="paper"` 且
`PB_PAPER_SKIP=true`（生产 .env 值）时整段跳过、直接放行（唯一权威落点）。
因此上述 1–3 的契约改为在 **live 口径**下验证（live 行为未变、仍全量检查），
另新增 `test_paper_mode_skips_gate_entirely` 锁定新的 paper 语义。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.risk_management import portfolio_budget as pbm  # noqa: E402


class _Dec:
    def __init__(self, allowed, reasons):
        self.allowed = allowed
        self.reasons = reasons


@pytest.fixture
def budget(monkeypatch):
    pb = pbm.PortfolioBudget()
    # 只测回撤分支：冻结表为空、其它判据放行；样本"新鲜"（1.0h）
    monkeypatch.setattr(pb, "_strategy_drawdown_metric", lambda *a, **k: {
        "ratio": 18.99, "sigma": 9.87, "drawdown": 187.47, "peak": 41.19,
        "last_value": -146.28, "n_samples": 168,
        "last_sample_ts": "2026-09-10 10:08:29", "age_hours": 1.0,
    })
    monkeypatch.setattr(pb, "_worst_symbols", lambda *a, **k: ["XRP", "BTC", "ASTER"])
    monkeypatch.setattr(pb, "_consecutive_losses", lambda *a, **k: 0)
    monkeypatch.setattr(pb, "_freeze_via_coordinator", lambda *a, **k: None)
    return pb


def _set_age(budget, monkeypatch, age_h):
    monkeypatch.setattr(budget, "_strategy_drawdown_metric", lambda *a, **k: {
        "ratio": 18.99, "sigma": 9.87, "drawdown": 187.47, "peak": 41.19,
        "last_value": -146.28, "n_samples": 168,
        "last_sample_ts": "2026-09-10 00:00:00", "age_hours": age_h,
    })


def test_dd_reject_is_independent_of_freeze_switch(budget, monkeypatch):
    """现状锁定：冻结开关关闭 ⇒ **仍然拒单**（这正是需要决策的点）。"""
    monkeypatch.setenv("PB_FREEZE_ENABLED", "false")
    dec = budget.evaluate_open(
        symbol="XRP", action="buy", notional_usd=800.0, equity=4700.0,
        strategy="midlong", mode="live", db=object(), account_id=14, positions=None,
    )
    assert dec.allowed is False, "现状应拒单（若这里变成 True，说明语义已按 P17 改了，请同步本测试）"
    joined = " ".join(dec.reasons)
    assert "drawdown=" in joined and "σ>" in joined, joined


def test_reject_reason_carries_sigma_and_cap(budget, monkeypatch):
    monkeypatch.setenv("PB_FREEZE_ENABLED", "false")
    dec = budget.evaluate_open(
        symbol="XRP", action="buy", notional_usd=800.0, equity=4700.0,
        strategy="midlong", mode="live", db=object(), account_id=14, positions=None,
    )
    import re

    m = re.search(r"drawdown=([\d.]+)σ>([\d.]+)σ", " ".join(dec.reasons))
    assert m, f"原因里缺少 σ/阈值: {dec.reasons}"
    assert float(m.group(1)) == pytest.approx(18.99)
    assert float(m.group(2)) == pytest.approx(10.0), "midlong 阈值应为放宽后的 10σ"


def test_metrics_expose_the_reject(budget, monkeypatch):
    monkeypatch.setenv("PB_FREEZE_ENABLED", "false")
    dec = budget.evaluate_open(
        symbol="XRP", action="buy", notional_usd=800.0, equity=4700.0,
        strategy="midlong", mode="live", db=object(), account_id=14, positions=None,
    )
    assert dec.metrics.get("drawdown_sigma") == pytest.approx(18.99)
    assert dec.metrics.get("dd_sigma_reject", {}).get("worst"), dec.metrics


def test_stuck_fuse_is_visible_when_freeze_disabled(budget, monkeypatch, caplog):
    monkeypatch.setenv("PB_FREEZE_ENABLED", "false")
    with caplog.at_level(logging.WARNING):
        budget.evaluate_open(
            symbol="XRP", action="buy", notional_usd=800.0, equity=4700.0,
            strategy="midlong", mode="live", db=object(), account_id=14, positions=None,
        )
    msgs = [r.getMessage() for r in caplog.records]
    assert any("回撤熔断**拒单**" in m and "PB_FREEZE_ENABLED=false" in m for m in msgs), msgs


def test_no_warning_when_freeze_enabled(budget, monkeypatch, caplog):
    """冻结开启时该 WARNING 不该刷（那是"设计如此"，不是矛盾）。"""
    monkeypatch.setenv("PB_FREEZE_ENABLED", "true")
    with caplog.at_level(logging.WARNING):
        budget.evaluate_open(
            symbol="XRP", action="buy", notional_usd=800.0, equity=4700.0,
            strategy="midlong", mode="live", db=object(), account_id=14, positions=None,
        )
    assert not [m for m in (r.getMessage() for r in caplog.records) if "回撤熔断**拒单**" in m]


def test_non_worst_symbol_not_rejected_by_dd_branch(budget, monkeypatch):
    """最小粒度语义：非亏损源 symbol 不该被回撤分支拦住（继续走后续规则）。"""
    monkeypatch.setenv("PB_FREEZE_ENABLED", "false")
    dec = budget.evaluate_open(
        symbol="SOL", action="buy", notional_usd=800.0, equity=4700.0,
        strategy="midlong", mode="live", db=object(), account_id=14, positions=None,
    )
    assert dec.allowed is True, f"非亏损源被回撤分支拦了: {dec.reasons}"


# ══ P17-C① 自愈规则（2026-09-10 执行）══

def test_stale_metric_does_not_reject(budget, monkeypatch, caplog):
    """距最后一次样本 ≥ PB_DD_STALE_HOURS ⇒ 判据降级为告警、**不拒单**（打破自锁）。"""
    monkeypatch.setenv("PB_FREEZE_ENABLED", "false")
    monkeypatch.setenv("PB_DD_STALE_HOURS", "12")
    _set_age(budget, monkeypatch, 13.0)
    with caplog.at_level(logging.WARNING):
        dec = budget.evaluate_open(
            symbol="XRP", action="buy", notional_usd=800.0, equity=4700.0,
            strategy="midlong", mode="live", db=object(), account_id=14, positions=None,
        )
    assert dec.allowed is True, f"stale 判据仍然拒单（自锁未解除）: {dec.reasons}"
    assert dec.metrics.get("dd_sigma_stale"), dec.metrics
    assert dec.metrics["dd_sigma_stale"]["age_hours"] == pytest.approx(13.0)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("stale" in m and "不拒单" in m for m in msgs), msgs


def test_fresh_metric_still_rejects(budget, monkeypatch):
    """样本新鲜（1h）⇒ 行为与修复前一致（拒单），确保自愈规则不误放。"""
    monkeypatch.setenv("PB_FREEZE_ENABLED", "false")
    monkeypatch.setenv("PB_DD_STALE_HOURS", "12")
    _set_age(budget, monkeypatch, 1.0)
    dec = budget.evaluate_open(
        symbol="XRP", action="buy", notional_usd=800.0, equity=4700.0,
        strategy="midlong", mode="live", db=object(), account_id=14, positions=None,
    )
    assert dec.allowed is False, f"新鲜样本应维持拒单: {dec.reasons}"
    assert not dec.metrics.get("dd_sigma_stale")


def test_stale_switch_zero_disables_self_healing(budget, monkeypatch):
    """`PB_DD_STALE_HOURS=0` = 关闭自愈（回到"永久拒单"的原语义，便于回滚）。"""
    monkeypatch.setenv("PB_FREEZE_ENABLED", "false")
    monkeypatch.setenv("PB_DD_STALE_HOURS", "0")
    _set_age(budget, monkeypatch, 999.0)
    dec = budget.evaluate_open(
        symbol="XRP", action="buy", notional_usd=800.0, equity=4700.0,
        strategy="midlong", mode="live", db=object(), account_id=14, positions=None,
    )
    assert dec.allowed is False, "0 应表示关闭自愈"
    assert not dec.metrics.get("dd_sigma_stale")


def test_metric_exposes_sample_age_and_size():
    """`_strategy_drawdown_metric` 必须给出样本新鲜度（P17-C① 的输入）。"""
    import inspect

    src = inspect.getsource(pbm.PortfolioBudget._strategy_drawdown_metric)
    for field in ("age_hours", "last_sample_ts", "n_samples", "ratio", "sigma", "drawdown"):
        assert field in src, f"缺少字段 {field}"
    # 兼容入口仍在
    assert hasattr(pbm.PortfolioBudget, "_strategy_drawdown_sigma")


# ══ [2026-09-11 用户指令] paper 短路：模拟账户不接组合预算闸 ══

def test_paper_mode_skips_gate_entirely(budget, monkeypatch):
    """mode=paper + PB_PAPER_SKIP=true ⇒ 整段跳过、直接放行（含回撤熔断）。"""
    monkeypatch.setenv("PB_PAPER_SKIP", "true")
    monkeypatch.setenv("PB_FREEZE_ENABLED", "false")
    dec = budget.evaluate_open(
        symbol="XRP", action="buy", notional_usd=800.0, equity=4700.0,
        strategy="midlong", mode="paper", db=object(), account_id=14, positions=None,
    )
    assert dec.allowed is True, f"paper 应放行: {dec.reasons}"
    assert dec.metrics.get("paper_skip") is True
    assert any("paper_skip" in r for r in dec.reasons), dec.reasons


def test_paper_skip_off_restores_full_check(budget, monkeypatch):
    """PB_PAPER_SKIP=false ⇒ paper 也走全量检查（回滚口径），回撤分支照旧拒单。"""
    monkeypatch.setenv("PB_PAPER_SKIP", "false")
    monkeypatch.setenv("PB_FREEZE_ENABLED", "false")
    dec = budget.evaluate_open(
        symbol="XRP", action="buy", notional_usd=800.0, equity=4700.0,
        strategy="midlong", mode="paper", db=object(), account_id=14, positions=None,
    )
    assert dec.allowed is False, f"关闭短路后应恢复拒单: {dec.reasons}"
    assert dec.metrics.get("drawdown_sigma") == pytest.approx(18.99)
