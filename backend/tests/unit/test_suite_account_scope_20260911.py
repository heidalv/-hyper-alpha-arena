# -*- coding: utf-8 -*-
"""[§92 契约 2026-09-11 / 缺陷 #75] 套件脚本的账户口径护栏。

§90.4/§91.4 定的规则是"业绩一律按活跃 PAPER 账户"。本轮发现套件里两个**面向业绩**的脚本
（`audit_profit_giveback` / `audit_gate_edge`）此前无账户条件 ⇒ 一旦窗口回看到 8/23 之前
（#156「150u」实验账户、#147/#149 测试残留），数字就会混入非策略仓位。

本文件锁：口径模块的规范位置与转发、两个脚本必须真的带账户条件。
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SUITE_SCRIPTS = ("audit_profit_giveback.py", "audit_gate_edge.py")


@pytest.fixture(scope="module")
def canon():
    spec = importlib.util.spec_from_file_location(
        "audit_scope", ROOT / "backend/config/audit_scope.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_canonical_scope_defaults(canon, monkeypatch):
    monkeypatch.delenv("AUDIT_ACCOUNT_ID", raising=False)
    assert canon.DEFAULT_ACCOUNT_ID == 14
    assert canon.account_id() == 14
    assert canon.account_clause() == " and account_id = 14"
    monkeypatch.setenv("AUDIT_ACCOUNT_ID", "0")
    assert canon.account_clause() == ""
    monkeypatch.setenv("AUDIT_ACCOUNT_ID", "149")
    assert "已归档" in canon.describe_scope()


def test_shim_forwards_same_values():
    """`_audit_ml/_scope.py` 必须转发到规范模块（历史脚本依赖它）。"""
    import sys
    sys.path.insert(0, str(ROOT / "_audit_ml"))
    import _scope  # noqa: PLC0415
    from backend.config import audit_scope  # noqa: PLC0415
    assert _scope.DEFAULT_ACCOUNT_ID == audit_scope.DEFAULT_ACCOUNT_ID
    assert _scope.account_clause() == audit_scope.account_clause()


@pytest.mark.parametrize("name", SUITE_SCRIPTS)
def test_suite_scripts_are_account_scoped(name):
    """防回退：两个业绩审计脚本必须引用口径模块并向 SQL 传 `{ACCT}`。"""
    src = (ROOT / "backend/scripts" / name).read_text(encoding="utf-8", errors="replace")
    assert "audit_scope" in src, f"{name} 未接入账户口径模块"
    assert "{ACCT}" in src, f"{name} 的 SQL 未带账户占位"
    assert "describe_scope()" in src, f"{name} 未打印口径"
