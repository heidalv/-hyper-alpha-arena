# -*- coding: utf-8 -*-
"""[2026-09-29 用户指令] 空头必须真正可开——三层封锁全部解除的回归锁。

## 背景（用户口径）
用户从未要求过「禁止做空」；代码注释里也记录了 09-27 用户指令「撤销禁止做空封锁」，
但当时只把 `MIDLONG_OPEN_SHORT_ENABLED` 改了 true，**剩三层还在拦**：
  ① `MIDLONG_SHORT_MODE=regime_gated` ⇒ 非下行 regime 一律 `midlong_short_regime_block`；
  ② 误判：曾把 `TIER_SHORT_BUDGET` 0→0.15——但 mid 空头吃 **mid 桶**（TIER_MID_BUDGET），
     短线档只喂 scalp；[2026-09-29 二次修订] 用户指令"短线不留资金、中长线各分各的"
     ⇒ 短线档归零（两车道模型），mid 桶 0.40 + mid 单仓保证金 0.16 为空头规模保证；
  ③ `MIDLONG_TIER_MARGIN_PCT_MID=0.16` / `MIDLONG_TIER_MARGIN_PCT_LONG=0.25`（中长线区分）。
本文件把解除后的部署态钉死，防止再被静默改回。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from dotenv import load_dotenv


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("MIDLONG_SHORT_MODE", raising=False)
    yield


def _env() -> dict:
    load_dotenv(r"D:\001Alpha\Hyper-Alpha-Arena\.env", override=True)
    import os
    return dict(os.environ)


def test_deployed_short_mode_is_on():
    """① 空头模式必须为无条件放行档（on），不得回到 regime_gated/off。"""
    e = _env()
    assert str(e.get("MIDLONG_SHORT_MODE", "")).strip().lower() == "on"


def test_deployed_short_tier_budget_restored():
    """②③ mid 空头的资金保证 = mid 桶预算 + mid 单仓保证金上限（与短线档无关）。

    [2026-09-29 修订] §106 曾把 `TIER_SHORT_BUDGET` 0→0.15，但 **mid 空头吃的是
    mid 桶**（TIER_MID_BUDGET），短线档（scalp）只喂短线车道。用户指令：短线不留资金、
    中线/长线各分各的 ⇒ 短线档归零（两车道模型），mid 桶 0.40 + mid 单仓保证金 0.16
    才是空头规模的结构性保证。
    """
    e = _env()
    assert float(e.get("TIER_MID_BUDGET", "0") or 0) >= 0.40, "mid 桶预算不足"
    assert float(e.get("MIDLONG_TIER_MARGIN_PCT_MID", "0") or 0) >= 0.16, "mid 单仓保证金上限不足"
    assert float(e.get("TIER_SHORT_BUDGET", "0") or 0) == 0.0, "短线档应退役（不留资金）"


def test_gate_allows_short_when_mode_on(monkeypatch):
    """mode=on 时空头新开必须被放行（paper 账户，无亏损锁）。"""
    import backend.services.risk_management.loss_lock_policy as _llp

    from backend.services.full_auto import midlong_circuit_gate as G

    monkeypatch.setenv("MIDLONG_SHORT_MODE", "on")
    monkeypatch.setenv("MIDLONG_OPEN_SHORT_ENABLED", "true")
    # [2026-09-29 全面执行] 本测试锁的是空头总开关契约；新入场边际闸（edge_gate）
    # 会先于本契约在真实行情下拦截 → 显式关闭隔离（同 test_midlong_circuit_gate）。
    monkeypatch.setenv("MIDLONG_EDGE_GATE_ENABLED", "false")
    monkeypatch.setattr(G, "_load", lambda: None)
    monkeypatch.setattr(_llp, "loss_locks_disabled", lambda *a, **k: True)
    ok, reason = G.check_midlong_entry(14, "UNI", side="sell", tier="mid")
    assert ok, f"空头仍被拦: {reason}"


def test_gate_blocks_when_mode_off(monkeypatch):
    """回滚档仍有效：off = 机械全停（用户要的是放行，不是把回滚档也删掉）。"""
    import backend.services.risk_management.loss_lock_policy as _llp

    from backend.services.full_auto import midlong_circuit_gate as G

    monkeypatch.setenv("MIDLONG_SHORT_MODE", "off")
    monkeypatch.setenv("MIDLONG_OPEN_SHORT_ENABLED", "true")
    # [2026-09-29 全面执行] 同上：隔离新入场边际闸，本文件锁的是总开关契约。
    monkeypatch.setenv("MIDLONG_EDGE_GATE_ENABLED", "false")
    monkeypatch.setattr(G, "_load", lambda: None)
    monkeypatch.setattr(_llp, "loss_locks_disabled", lambda *a, **k: True)
    ok, reason = G.check_midlong_entry(14, "UNI", side="sell", tier="mid")
    assert not ok and "off" in reason, f"回滚档失效: {ok}/{reason}"
