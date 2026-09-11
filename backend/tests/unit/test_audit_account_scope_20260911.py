# -*- coding: utf-8 -*-
"""[§90 契约 2026-09-11 / 缺陷 #73] 审计口径的**账户隔离**：默认只统计活跃 PAPER 账户。

为什么单独立约：`research` 层的 30 天 −$413.08 全在 `accounts.name='[已归档-测试残留]…'`
的账号 147/149，另有一个短期实验账号 #156「150u」。若审计脚本不隔离账户，
发布的"逐层净额"就会被测试残留污染（本审计第一版确实混进了 19 笔 / −$14.62）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
INSTRUMENTS = ("Z221_fix_effect_slice.py", "Z251_peak_transition_analysis.py",
               "Z252_long_loss_mechanism.py", "Z259_entry_profile.py",
               "Z260_p29_recheck.py")


@pytest.fixture(scope="module")
def scope():
    spec = importlib.util.spec_from_file_location("_scope", ROOT / "_audit_ml/_scope.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_default_account_is_live_paper(scope, monkeypatch):
    monkeypatch.delenv("AUDIT_ACCOUNT_ID", raising=False)
    assert scope.account_id() == 14
    assert scope.account_clause() == " and account_id = 14"
    assert scope.account_clause("p") == " and p.account_id = 14"


def test_env_override_and_off_switch(scope, monkeypatch):
    monkeypatch.setenv("AUDIT_ACCOUNT_ID", "156")
    assert scope.account_clause() == " and account_id = 156"
    monkeypatch.setenv("AUDIT_ACCOUNT_ID", "0")
    assert scope.account_clause() == "", "0 应当表示不隔离（对照用）"
    monkeypatch.setenv("AUDIT_ACCOUNT_ID", "abc")
    assert scope.account_id() == 14, "非法值必须回退默认账户"


def test_describe_scope_warns_on_archived(scope, monkeypatch):
    monkeypatch.setenv("AUDIT_ACCOUNT_ID", "149")
    assert "已归档" in scope.describe_scope()
    monkeypatch.setenv("AUDIT_ACCOUNT_ID", "156")
    assert "实验账户" in scope.describe_scope()
    monkeypatch.setenv("AUDIT_ACCOUNT_ID", "14")
    assert "account_id=14" in scope.describe_scope()


def test_instruments_are_account_scoped():
    """防回退：主要度量脚本必须**真的**带上账户条件（只 import 不用 = 没隔离）。"""
    for name in INSTRUMENTS:
        src = (ROOT / "_audit_ml" / name).read_text(encoding="utf-8", errors="replace")
        assert "_scope" in src, f"{name} 未接入 _scope"
        assert "{ACCT}" in src, f"{name} 的 SQL 没有账户占位 {{ACCT}}"
        assert "describe_scope()" in src, f"{name} 输出未打印口径"
