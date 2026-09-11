# -*- coding: utf-8 -*-
"""[2026-09-10 第二十九轮] 配置「静默失效」契约测试。

覆盖两类已确认缺陷：
  1. **falsy 吞值**：`getattr(settings, K, d) or d` 把显式 0/False 换回默认值
     —— 使「0 = 关闭/不作要求」的语义静默失效（§38.9/2 的 MIDLONG_MAX_OPEN_POSITIONS 同源）；
  2. **近名误配**：`.env` 键与代码读取的键名不一致（如
     `REENTRY_COOLDOWN_SEC` vs `REENTRY_COOLDOWN_SECONDS`），配置永久不生效。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))


def _settings(monkeypatch):
    from backend.config import settings
    return settings


# ---------- 1. keep-0 语义 ----------

def test_open_gate_cfg_int_keeps_zero(monkeypatch):
    from backend.services.mlto import open_gate
    s = _settings(monkeypatch)
    monkeypatch.setattr(s, "AUDIT_PROBE_K", 0, raising=False)
    assert open_gate._cfg_int_keep0("AUDIT_PROBE_K", 78) == 0, "显式 0 必须保留（0 = 不作要求）"
    monkeypatch.setattr(s, "AUDIT_PROBE_K", 25, raising=False)
    assert open_gate._cfg_int_keep0("AUDIT_PROBE_K", 78) == 25


def test_open_gate_missing_key_falls_back(monkeypatch):
    from backend.services.mlto import open_gate
    from backend.config import settings as s
    if hasattr(s, "AUDIT_PROBE_MISSING"):
        monkeypatch.delattr(s, "AUDIT_PROBE_MISSING", raising=False)
    assert open_gate._cfg_int_keep0("AUDIT_PROBE_MISSING", 78) == 78


def test_portfolio_risk_cfg_float_keeps_zero(monkeypatch):
    from backend.services.mlto import midlong_portfolio_risk as mpr
    s = _settings(monkeypatch)
    monkeypatch.setattr(s, "AUDIT_PROBE_F", 0.0, raising=False)
    assert mpr._cfg_float("AUDIT_PROBE_F", 0.5) == 0.0, "0.0 必须保留"
    monkeypatch.setattr(s, "AUDIT_PROBE_F", 0.25, raising=False)
    assert mpr._cfg_float("AUDIT_PROBE_F", 0.5) == 0.25


def test_portfolio_risk_cfg_int_keeps_zero_and_default(monkeypatch):
    from backend.services.mlto import midlong_portfolio_risk as mpr
    s = _settings(monkeypatch)
    monkeypatch.setattr(s, "MIDLONG_MAX_OPEN_POSITIONS", 4, raising=False)
    assert mpr._cfg_int_allow_zero("MIDLONG_MAX_OPEN_POSITIONS", 4) == 4
    monkeypatch.setattr(s, "MIDLONG_MAX_OPEN_POSITIONS", 0, raising=False)
    assert mpr._cfg_int_allow_zero("MIDLONG_MAX_OPEN_POSITIONS", 4) == 0


def test_trade_design_helpers_keep_zero(monkeypatch):
    from backend.services.mlto import midlong_trade_design as mtd
    s = _settings(monkeypatch)
    monkeypatch.setattr(s, "AUDIT_PROBE_I", 0, raising=False)
    monkeypatch.setattr(s, "AUDIT_PROBE_F2", 0.0, raising=False)
    assert mtd._cfg_int("AUDIT_PROBE_I", 7) == 0
    assert mtd._cfg_float("AUDIT_PROBE_F2", 0.7) == 0.0


def test_position_manager_helpers_keep_zero(monkeypatch):
    from backend.services.full_auto import midlong_position_manager as mpm
    s = _settings(monkeypatch)
    monkeypatch.setattr(s, "AUDIT_PROBE_I2", 0, raising=False)
    monkeypatch.setattr(s, "AUDIT_PROBE_F3", 0.0, raising=False)
    assert mpm._cfg_int("AUDIT_PROBE_I2", 5) == 0
    assert mpm._cfg_float("AUDIT_PROBE_F3", 0.5) == 0.0


def test_midlong_helpers_keep0_float(monkeypatch):
    from backend.services.full_auto import midlong_helpers as mh
    assert mh._keep0_float(0, 0.01) == 0.0
    assert mh._keep0_float(None, 0.01) == 0.01
    assert mh._keep0_int(0, 15) == 0


# ---------- 2. 审计工具的纯函数（近名误配 / .env 解析） ----------

def test_audit_parse_env_strips_inline_comment_and_quotes():
    from backend.scripts.audit_config_effective import parse_env
    env = parse_env('A=1\nB="x"\nC=false  # 说明文字\n# 注释\nD=0\n')
    assert env["A"] == "1"
    assert env["B"] == "x"
    assert env["C"] == "false", "行内注释不得混进值"
    assert env["D"] == "0"


def test_audit_normalize_unifies_quoted_and_bare():
    from backend.scripts.audit_config_effective import normalize
    assert normalize('"true"') == normalize("true") == "true"
    assert normalize('false  # x') == "false"


def test_audit_detect_near_miss_finds_real_case():
    """回归：REENTRY_COOLDOWN_SEC（.env）≈ REENTRY_COOLDOWN_SECONDS（代码读）。"""
    from backend.scripts.audit_config_effective import detect_near_miss
    hits = detect_near_miss(["REENTRY_COOLDOWN_SEC"], ["REENTRY_COOLDOWN_SECONDS", "OTHER_KEY"])
    assert hits and hits[0]["candidate"] == "REENTRY_COOLDOWN_SECONDS"
    assert hits[0]["score"] > 0.85


def test_audit_detect_near_miss_ignores_unrelated():
    from backend.scripts.audit_config_effective import detect_near_miss
    assert detect_near_miss(["ZZZ_TOTALLY_DIFFERENT"], ["MIDLONG_MAX_OPEN_POSITIONS"]) == []


def test_audit_falsy_severity_classification():
    """getattr(settings,...) or X 判为 HIGH；os.getenv(...) or X 判为 LOW。"""
    from backend.scripts.audit_config_effective import find_falsy_sites
    text = (
        'v = int(getattr(settings, "SOME_CAP", 15) or 15)\n'
        'w = float(os.getenv("SOME_PCT", str(d)) or d)\n'
    )
    sites = find_falsy_sites(text)
    sev = {s[1]: s[2] for s in sites}
    assert sev.get("getattr_or") == "HIGH"
    assert sev.get("getenv_or") == "LOW"
