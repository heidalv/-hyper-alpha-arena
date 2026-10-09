# -*- coding: utf-8 -*-
"""[F298 2026-09-21] 参数分叉告警回归测试。

# 事故

把 `.env` 的 `MM_SPREAD_MULT` 由 0.9 改成 0.5 后，正在跑的 worker 心跳**连续 100 秒
仍是 0.9**。根因：`apply_env_param_overrides()` 读 `os.getenv` —— 那是**进程启动时**
由 `load_dotenv` 灌进 `os.environ` 的**冻结值**，改文件对已运行进程无效。

后果链：`scripts/mm_apply_*.py` 那族脚本改完会打印"≤60s 内热采用"，
**而对这 7 个白名单键这句话是错的** ⇒ 静默偏差
（F189 / F280 / F287 / F292 都是同一根因的不同表现）。

# 本测试固定什么

1. 语义**不变**：env 仍然赢（只有 env 设了值才覆盖）—— 不改既有部署行为
2. 分叉**必须告警**（warning 级）：注册表值与 env 值不同时要能在日志里看见
3. 一致时**不告警**（避免每 tick 刷日志）
"""
from __future__ import annotations

import logging

import pytest

from backend.services.market_maker.core import QuoteParams
from backend.services.market_maker.runner import apply_env_param_overrides


@pytest.fixture(autouse=True)
def _force_legacy_mode(monkeypatch):
    """本文件测的是 **F280/F298 的 legacy 语义（env 赢）**。

    ⚠️ 必须显式关掉 `MM_REGISTRY_AUTHORITATIVE`：生产 `.env` 一旦打开它
    （F327，2026-09-21 起为 1），`load_dotenv` 会把它灌进 `os.environ`
    并**泄漏进本测试进程** ⇒ 这里的 3 条断言会假失败。
    实测发生过（本文件正是这样红的），所以这一段是必需的，不是防御性冗余。

    关掉之后本文件验证的是**回退路径仍然完好** —— 这仍有价值：
    它是 F327 出问题时的退路，退路必须始终可用。
    F327 自身的行为由 `test_f327_registry_authoritative.py` 覆盖。
    """
    monkeypatch.delenv("MM_REGISTRY_AUTHORITATIVE", raising=False)
    yield


@pytest.mark.unit
def test_env_still_wins(monkeypatch):
    """语义不变：env 显式设了值 ⇒ 覆盖注册表值。"""
    monkeypatch.setenv("MM_SPREAD_MULT", "0.5")
    p = QuoteParams(spread_mult=0.9)
    out = apply_env_param_overrides(p)
    assert out.spread_mult == pytest.approx(0.5)


@pytest.mark.unit
def test_no_env_keeps_registry(monkeypatch):
    """env 没设 ⇒ 保留注册表值（不改变未配置的部署）。"""
    monkeypatch.delenv("MM_SPREAD_MULT", raising=False)
    p = QuoteParams(spread_mult=0.9)
    out = apply_env_param_overrides(p)
    assert out.spread_mult == pytest.approx(0.9)


@pytest.mark.unit
def test_divergence_warns(monkeypatch, caplog):
    """**核心回归**：注册表 0.9 / env 0.5 分叉时必须 warning。"""
    monkeypatch.setenv("MM_SPREAD_MULT", "0.5")
    p = QuoteParams(spread_mult=0.9)
    with caplog.at_level(logging.WARNING):
        apply_env_param_overrides(p)
    msgs = [r.getMessage() for r in caplog.records]
    hit = [m for m in msgs if "分叉" in m or "F298" in m]
    assert hit, f"分叉未告警；实际日志：{msgs}"


@pytest.mark.unit
def test_no_divergence_no_warn(monkeypatch, caplog):
    """一致时不告警（否则每 tick 刷屏，warning 会失去意义）。

    ⚠️ 必须先把**全部**白名单 env 键清掉，否则 conftest/.env 里带的其它键
    （实测 `MM_SPREAD_MULT_REDUCE=0.95` vs 注册表 0.0）会引入无关分叉，
    把这条测试变成"环境敏感"的假失败。
    """
    for _env in ("MM_SPREAD_MULT", "MM_SPREAD_MULT_REDUCE", "MM_MIN_EDGE_FRAC",
                 "MM_SPREAD_CROSS_MARGIN", "MM_W_BASE_BP", "MM_MIN_WIDTH_BP",
                 "MM_K_INV"):
        monkeypatch.delenv(_env, raising=False)
    p = QuoteParams(spread_mult=0.9)
    with caplog.at_level(logging.WARNING):
        apply_env_param_overrides(p)
    msgs = [r.getMessage() for r in caplog.records]
    assert not [m for m in msgs if "分叉" in m or "F298" in m], \
        f"一致时不应告警；实际：{msgs}"


@pytest.mark.unit
def test_whitelist_keys_note_current_divergences(monkeypatch, caplog):
    """把**当前部署**里真实存在的分叉固化成测试（记录事实，不是制造失败）。

    实测（2026-09-21）：`.env` 与注册表在这些键上本就不一致 ——
      · `spread_mult_reduce` 注册表 0.0 / env 0.95
      · `w_base_bp`          注册表 1.5 / env 5.0
      · `k_inv`              注册表 0.5 / env 1.0
    ⇒ 它们**靠 env 覆盖**才跑在当前口径上；注册表里那几个值是过时的。
    这正是 F298 要暴露的东西：**运行时权威是 env，不是注册表**。
    """
    monkeypatch.setenv("MM_SPREAD_MULT_REDUCE", "0.95")
    p = QuoteParams(spread_mult_reduce=0.0)
    with caplog.at_level(logging.WARNING):
        apply_env_param_overrides(p)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("spread_mult_reduce" in m and "分叉" in m for m in msgs), \
        "应报告 spread_mult_reduce 的注册表/env 分叉"
    assert p.spread_mult_reduce == pytest.approx(0.95), "env 仍应生效"


@pytest.mark.unit
def test_non_numeric_env_ignored(monkeypatch):
    """env 值不是数值 ⇒ 忽略并保持原值（不能让 tick 崩）。"""
    monkeypatch.setenv("MM_SPREAD_MULT", "abc")
    p = QuoteParams(spread_mult=0.9)
    out = apply_env_param_overrides(p)
    assert out.spread_mult == pytest.approx(0.9)


@pytest.mark.unit
def test_all_whitelist_keys_covered(monkeypatch):
    """7 个白名单键都要真的生效（防止漏掉某个键而静默失效）。"""
    cases = {
        "MM_SPREAD_MULT": ("spread_mult", 0.11),
        "MM_SPREAD_MULT_REDUCE": ("spread_mult_reduce", 0.12),
        "MM_MIN_EDGE_FRAC": ("min_edge_frac", 0.13),
        "MM_SPREAD_CROSS_MARGIN": ("spread_cross_margin", 0.14),
        "MM_W_BASE_BP": ("w_base_bp", 0.15),
        "MM_MIN_WIDTH_BP": ("min_width_bp", 0.16),
        "MM_K_INV": ("k_inv", 0.17),
    }
    for env, (field, val) in cases.items():
        monkeypatch.setenv(env, str(val))
    p = QuoteParams()
    out = apply_env_param_overrides(p)
    for env, (field, val) in cases.items():
        assert getattr(out, field) == pytest.approx(val), f"{env} 未生效"

