"""中线弹药扩源（P2-1 reopen + registry 引用管道）回归测试（2026-08-14）。"""
from __future__ import annotations

import os
import shutil

import pytest

_TMP_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp_ammo")


def _ws_tmp(name: str) -> str:
    d = os.path.join(_TMP_ROOT, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    return d


def teardown_module(module):
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


def _fresh_store(tmp, monkeypatch):
    from backend.services.factor_engine import custom_factor_store as cs

    monkeypatch.setattr(cs, "_STORE_FILE", tmp)
    cs.custom_factor_store._data = {}
    cs.custom_factor_store._loaded = False
    return cs.custom_factor_store


# ═══════════════════════════════════════════════════════════
# P2-1：register 公式变化重开 + reopen_rejected
# ═══════════════════════════════════════════════════════════

def test_register_formula_change_reopens(monkeypatch):
    store = _fresh_store(_ws_tmp("p21_a") + "/store.json", monkeypatch)
    store.register(name="x1", formula="delta(close, 1) / (close + 1e-9)",
                   category="alpha101", tenant_id=7)
    store.update_scores("ai_x1", grade="C", scores={"ic_mean": 0.01},
                        status="rejected", tenant_id=7)
    assert store.get("ai_x1", tenant_id=7)["status"] == "rejected"

    # 换公式重登记 → 应重开为 candidate、清空评分
    r = store.register(name="x1", formula="delta(close, 2) / (close + 1e-9)",
                       category="alpha101", tenant_id=7)
    assert r["reason"] == "reopened"
    rec = store.get("ai_x1", tenant_id=7)
    assert rec["status"] == "candidate"
    assert rec["grade"] is None
    assert rec["scores"] == {}


def test_register_same_formula_keeps_status(monkeypatch):
    store = _fresh_store(_ws_tmp("p21_b") + "/store.json", monkeypatch)
    store.register(name="x1", formula="delta(close, 1) / (close + 1e-9)",
                   category="alpha101", tenant_id=7)
    store.update_scores("ai_x1", grade="C", scores={"ic_mean": 0.01},
                        status="rejected", tenant_id=7)
    # 同公式重登记 → 保持 rejected（幂等种子语义）
    r = store.register(name="x1", formula="delta(close, 1) / (close + 1e-9)",
                       category="alpha101", tenant_id=7)
    assert r["reason"] == "updated"
    assert store.get("ai_x1", tenant_id=7)["status"] == "rejected"


def test_reopen_rejected(monkeypatch):
    store = _fresh_store(_ws_tmp("p21_c") + "/store.json", monkeypatch)
    for n in ("x1", "x2"):
        store.register(name=n, formula="delta(close, 1) / (close + 1e-9)",
                       category="alpha101", tenant_id=7)
    store.update_scores("ai_x1", grade="C", scores={}, status="rejected", tenant_id=7)
    store.update_scores("ai_x2", grade="C", scores={}, status="rejected", tenant_id=7)
    reopened = store.reopen_rejected(tenant_id=7, category="alpha101")
    assert reopened == 2
    assert store.get("ai_x1", tenant_id=7)["status"] == "candidate"
    assert store.get("ai_x2", tenant_id=7)["status"] == "candidate"


# ═══════════════════════════════════════════════════════════
# registry 引用记录
# ═══════════════════════════════════════════════════════════

def test_register_reference_roundtrip(monkeypatch):
    store = _fresh_store(_ws_tmp("reg") + "/store.json", monkeypatch)
    r = store.register_reference("ai_gen_bsq", tenant_id=7, timeframe="4h")
    assert r["ok"] is True
    rec = store.get("ai_gen_bsq", tenant_id=7)
    assert rec["formula"] is None
    assert rec["extra"]["kind"] == "registry"
    assert rec["extra"]["horizon"] == "midlong"
    assert rec["status"] == "candidate"
    # 打分回写可用
    ok = store.update_scores("ai_gen_bsq", grade="B", scores={"ic_mean": 0.04},
                             status="active", tenant_id=7)
    assert ok is True
    assert store.get("ai_gen_bsq", tenant_id=7)["status"] == "active"


# ═══════════════════════════════════════════════════════════
# 分级门槛（与 score_formula 同款）
# ═══════════════════════════════════════════════════════════

def test_grade_from_metrics_thresholds():
    from backend.services.factor_engine.midlong_registry_factors import _grade_from_metrics

    # A：IC/ICIR 达标 + perf 达标
    assert _grade_from_metrics(0.06, 0.6, 0.8, 0.02, 0.005, 0.0021, 0.4, 0.0) == "A"
    # B：略弱但达标
    assert _grade_from_metrics(0.04, 0.4, 0.6, 0.01, 0.005, 0.0021, 0.4, 0.0) == "B"
    # C：IC 达标但 perf 不达标（sharpe 低）
    assert _grade_from_metrics(0.06, 0.6, 0.1, -0.02, -0.001, 0.0021, 0.4, 0.0) == "C"
    # C：IC 中等
    assert _grade_from_metrics(0.02, 0.2, 0.6, 0.01, 0.005, 0.0021, 0.4, 0.0) == "C"
    # D：IC 太弱
    assert _grade_from_metrics(0.005, 0.05, 0.6, 0.01, 0.005, 0.0021, 0.4, 0.0) == "D"
