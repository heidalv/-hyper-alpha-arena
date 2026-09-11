# -*- coding: utf-8 -*-
"""[§84 契约 2026-09-11 / 目标①③] 熔断的"证据自锁"判定规则（纯函数）双向验证。

背景（缺陷 #69）：抑制发生在 `record_close()` **之前** ⇒ 被抑制的通道不再产生样本
⇒ 窗口永久冻结在该胜率上。核查（`_audit_ml/Z241_breaker_evidence_lock.py`）实测：
`mid|trend_broken` 的窗口最新样本已 **14.1 天**、`mid|midlong` **8.0 天**，
而它们**仍会抑制**下一次离场 —— 用两周前的证据拦今天的出场。

本文件锁住规则 `is_evidence_frozen(shadowed, suppressible, newest_age_days, stale_days)`：
只有"已 shadow **且** 可被抑制 **且** 证据过期"才算冻结；保护性通道/未 shadow/新鲜证据都不算。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def z241():
    spec = importlib.util.spec_from_file_location(
        "z241", ROOT / "_audit_ml/Z241_breaker_evidence_lock.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_frozen_when_stale_evidence_and_suppressible(z241):
    """**能红**：shadow + 可抑制 + 证据 14 天 > 7 天 ⇒ 冻结（= 线上 mid|trend_broken 现状）。"""
    assert z241.is_evidence_frozen(shadowed=True, suppressible=True,
                                   newest_age_days=14.1, stale_days=7.0) is True


def test_not_frozen_with_fresh_evidence(z241):
    """**能绿**：证据新鲜（1 天）⇒ 不算冻结。"""
    assert z241.is_evidence_frozen(shadowed=True, suppressible=True,
                                   newest_age_days=1.0, stale_days=7.0) is False


def test_not_frozen_when_protected(z241):
    """保护性通道本来就不抑制 ⇒ 不存在自锁（例如 short|sl / max_hold_timeout）。"""
    assert z241.is_evidence_frozen(shadowed=True, suppressible=False,
                                   newest_age_days=99.0, stale_days=7.0) is False


def test_not_frozen_when_not_shadowed(z241):
    """未 shadow ⇒ 没有抑制 ⇒ 无自锁。"""
    assert z241.is_evidence_frozen(shadowed=False, suppressible=True,
                                   newest_age_days=99.0, stale_days=7.0) is False


def test_no_record_counts_as_frozen(z241):
    """窗口里没有任何 DB 记录 ⇒ 视为过期（无证据却要抑制，必须当作冻结）。"""
    assert z241.is_evidence_frozen(shadowed=True, suppressible=True,
                                   newest_age_days=None, stale_days=7.0) is True


def test_boundary_is_strictly_greater(z241):
    """边界：恰好等于阈值**不算**过期（与 P17 的 `>=` 口径分开写清，避免歧义）。"""
    assert z241.is_evidence_frozen(shadowed=True, suppressible=True,
                                   newest_age_days=7.0, stale_days=7.0) is False


def test_code_path_proof_all_true(z241):
    """代码路径证据必须成立：抑制先于记账（否则"冻结"论点不成立）。"""
    proofs = z241._code_proof()
    assert len(proofs) == 3
    assert all(ok for _name, ok, _d in proofs), [p for p in proofs if not p[1]]
