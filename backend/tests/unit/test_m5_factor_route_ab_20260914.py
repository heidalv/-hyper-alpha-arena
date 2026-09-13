# -*- coding: utf-8 -*-
"""[M5 2026-09-14] 因子路由 A/B 车道契约测试。

背景：脑开启时 factor_route_open 直接返回 brain_evidence_only（因子永远不开仓，
mid 车道消费者=0）。A/B 车道：MIDLONG_MID_FACTOR_ROUTE_AB=true（默认）+ paper
→ 因子路由与 LLM 主脑并行开仓，entry_source=factor_route 独立记账。

契约：
- AB 开 + paper + 脑开 → 不被 brain_evidence_only 短路（gate 标记 ab_parallel）。
- AB 关（回滚档）→ 保持旧行为（brain_evidence_only）。
- live 模式 → 即使 AB 开也保持 brain_evidence_only（A/B 只属于 paper 数据工厂）。
"""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

MOD = "backend.services.factor_engine.midlong_factor_route"


class _FakeHost:
    def inject_midlong_indicators(self, *a, **k):
        pass


class _FakeSession:
    paper_account_id = 14
    account_id = 14
    session_id = "fa_7e12e7a1b6"
    status = "running"


def _fresh(monkeypatch, ab="true", mode="paper"):
    monkeypatch.setenv("MIDLONG_MID_FACTOR_ROUTE_AB", ab)
    mod = importlib.import_module(MOD)
    mod = importlib.reload(mod)
    import backend.config.settings as settings
    monkeypatch.setattr(settings, "midlong_brain_enabled", lambda: True, raising=False)
    # decide 返回 hold：只验证门禁语义，不触碰执行链
    monkeypatch.setattr(
        mod, "factor_route_decide",
        lambda *a, **k: {"action": "hold", "score": 0.0, "votes": {}, "reason": "stub", "opened": False, "gate": ""},
        raising=False,
    )
    return mod


def test_ab_paper_not_short_circuited(monkeypatch):
    mod = _fresh(monkeypatch, ab="true", mode="paper")
    dec = mod.factor_route_open(
        host=_FakeHost(), session=_FakeSession(), symbol="SOL",
        market_summary={}, portfolio={}, trading_mode="paper",
    )
    # hold 决策直接返回，但 gate 必须是 ab_parallel（证明没被 brain_evidence_only 短路）
    assert dec.get("gate") == "ab_parallel(paper)"


def test_ab_off_rollback_keeps_old_behavior(monkeypatch):
    mod = _fresh(monkeypatch, ab="false", mode="paper")
    dec = mod.factor_route_open(
        host=_FakeHost(), session=_FakeSession(), symbol="SOL",
        market_summary={}, portfolio={}, trading_mode="paper",
    )
    assert dec.get("gate") == "brain_evidence_only"
    assert dec.get("opened") is False


def test_ab_live_still_evidence_only(monkeypatch):
    """A/B 只属于 paper 数据工厂；live 保持旧行为（因子只产证据）。"""
    mod = _fresh(monkeypatch, ab="true", mode="live")
    dec = mod.factor_route_open(
        host=_FakeHost(), session=_FakeSession(), symbol="SOL",
        market_summary={}, portfolio={}, trading_mode="live",
    )
    assert dec.get("gate") == "brain_evidence_only"
    assert dec.get("opened") is False
