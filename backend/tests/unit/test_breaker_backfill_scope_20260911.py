# -*- coding: utf-8 -*-
"""[§91 契约 2026-09-11 / 缺陷 #74] 熔断回填的**账户口径**护栏。

熔断是在线风控闸；其证据窗来自 DB 回填 ⇒ 回填**默认必须只读活跃 PAPER 账户（14）**。
现场：30 天全层平仓 400/1864 笔（21.5%）来自已归档测试账户（#147/#149）与短期实验账户（#156）；
实测**当前不改变**任何 shadow 判定（`_audit_ml/Z266`），但口径必须正确且可回归。

本文件锁：默认账户 = `AUDIT_ACCOUNT_ID`（14）、`--all-accounts` 逃生门、以及源码护栏。
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "backend/scripts/backfill_exit_channel_breaker.py"


@pytest.fixture(scope="module")
def scope():
    spec = importlib.util.spec_from_file_location("_scope2", ROOT / "_audit_ml/_scope.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_default_account_is_live_paper(scope, monkeypatch):
    monkeypatch.delenv("AUDIT_ACCOUNT_ID", raising=False)
    assert scope.DEFAULT_ACCOUNT_ID == 14
    assert scope.account_id() == 14


def test_backfill_default_is_scoped_and_has_escape_hatch():
    """源码护栏：默认账户走 `AUDIT_ACCOUNT_ID`，且保留 `--all-accounts` 对照开关。"""
    src = SCRIPT.read_text(encoding="utf-8", errors="replace")
    assert 'os.environ.get("AUDIT_ACCOUNT_ID", "14")' in src, \
        "回填默认账户没有走 AUDIT_ACCOUNT_ID（会被测试账户污染）"
    assert "--all-accounts" in src, "缺少对照用逃生门"
    assert "if args.all_accounts:" in src and "args.account = 0" in src
    assert "p.account_id = " in src, "SQL 未按账户过滤"


def test_backfill_account_filter_reaches_sql(monkeypatch):
    """行为：`--account N` 必须真的进 SQL（用源码里的条件表达式验证语义）。"""
    src = SCRIPT.read_text(encoding="utf-8", errors="replace")
    assert "{'and p.account_id = ' + str(int(args.account)) if args.account else ''}" in src, \
        "账户条件没有拼进 SQL"
