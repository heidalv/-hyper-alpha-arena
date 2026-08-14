"""阶段 3（假闭环修复群）回归测试（2026-08-14）。

锁定：
- P1-D1 清理决策文件 round-trip（rejected/low_signal 名单真实可读）
- P1-D3 隔离因子恢复逻辑（restore_quarantined_factors）
- P1-B1 存储层租户写回语义（update_scores 带 tenant 命中 t{tid}: 键）

注：不使用 pytest tmp_path（沙箱环境对 %TEMP% 目录枚举受限），
改为工作区内自管理临时目录，测试内自行清理。
"""
from __future__ import annotations

import json
import os
import shutil

import pytest

_TMP_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp_phase3")


def _ws_tmp(name: str) -> str:
    d = os.path.join(_TMP_ROOT, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    return d


def teardown_module(module):
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


# ═══════════════════════════════════════════════════════════
# P1-D1：清理决策文件
# ═══════════════════════════════════════════════════════════

def test_cleanup_decisions_roundtrip(monkeypatch):
    import backend.services.factor_cleanup_service as svc

    d = _ws_tmp("cleanup")
    f = os.path.join(d, "factor_cleanup_decisions.json")
    monkeypatch.setattr(svc, "_DECISIONS_FILE", f)
    with open(f, "w", encoding="utf-8") as fh:
        json.dump({"rejected": ["a", "b"], "low_signal": ["c"]}, fh)
    assert svc.get_rejected_factor_ids() == {"a", "b"}
    assert svc.get_low_signal_factor_ids() == {"c"}

    # 缺失文件 → 空集（不再有假数据）
    monkeypatch.setattr(svc, "_DECISIONS_FILE", os.path.join(d, "nope.json"))
    assert svc.get_rejected_factor_ids() == set()


# ═══════════════════════════════════════════════════════════
# P1-D3：隔离恢复
# ═══════════════════════════════════════════════════════════

def test_restore_quarantined_factors(monkeypatch):
    import backend.services.factor_engine.factor_slimming_audit as aud

    d = _ws_tmp("slimming")
    state_f = os.path.join(d, "state.json")
    qdir = os.path.join(d, "_ai_gen_quarantine")
    src = os.path.join(d, "factors", "ai_generated", "ai_gen_x.py")
    monkeypatch.setattr(aud, "STATE_PATH", state_f)
    monkeypatch.setattr(aud, "QUARANTINE_DIR", qdir)

    os.makedirs(os.path.dirname(src), exist_ok=True)
    os.makedirs(qdir, exist_ok=True)
    with open(os.path.join(qdir, "ai_gen_x.py"), "w", encoding="utf-8") as fh:
        fh.write("class X: pass\n")
    aud._save_json(state_f, {
        "fx": {
            "status": "quarantined",
            "since": "2026-08-01T00:00:00+00:00",
            "src": src,
        }
    })

    res = aud.restore_quarantined_factors()
    assert res["restored"] == ["fx"]
    assert os.path.exists(src)                      # 文件已移回原分类目录
    st = aud._load_json(state_f)
    assert st["fx"]["status"] == "restored"


# ═══════════════════════════════════════════════════════════
# P1-B1：存储层租户写回语义
# ═══════════════════════════════════════════════════════════

def test_store_update_scores_with_tenant(monkeypatch):
    from backend.services.factor_engine import custom_factor_store as cs

    d = _ws_tmp("store")
    monkeypatch.setattr(cs, "_STORE_FILE", os.path.join(d, "store.json"))
    # 重置单例内存态，隔离其它用例
    cs.custom_factor_store._data = {}
    cs.custom_factor_store._loaded = False

    reg = cs.custom_factor_store.register(
        name="x1", formula="delta(close, 1) / (close + 1e-9)",
        category="discovered", tenant_id=7,
    )
    assert reg["ok"] is True

    ok = cs.custom_factor_store.update_scores(
        "ai_x1", grade="B", scores={"ic_mean": 0.1}, status="active", tenant_id=7,
    )
    assert ok is True
    rec = cs.custom_factor_store.get("ai_x1", tenant_id=7)
    assert rec["status"] == "active"

    # 无租户调用 → False（修复前 scalp_active_factor_set 漏传租户即命中此失败分支）
    ok2 = cs.custom_factor_store.update_scores(
        "ai_x1", grade="C", scores={}, status="rejected",
    )
    assert ok2 is False
