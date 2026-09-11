# -*- coding: utf-8 -*-
"""[§82 契约 2026-09-11 / 缺陷 #68] 决策审计**不得被测试夹具污染**。

现场：`data/midlong_direction_audit.jsonl` 里出现 `KBONKBONK` / `TESTCOIN` 的
`outcome=opened` 行（近 24h 的 opened 行里 16/33 ≈ 48% 是夹具），来源是 pytest 进程
直接调用 `record_decision_audit()` 写盘 —— 与 §75.1「测试进程写生产日志」同一类问题。
后果：漏斗统计（`opened`/`skip`）被系统性放大，报告里引用过的 `opened=26/30` 必须打对折看。

修复口径：
  * `PYTEST_CURRENT_TEST` 存在**且**未显式重定向（`MIDLONG_DIRECTION_AUDIT_PATH`）
    ⇒ 不写生产文件（早退，不触碰路径）；
  * 需要写盘验证的测试用该环境变量重定向到临时文件；
  * 写入失败必须 ≥WARNING（审计缺行不能静默）。
"""
from __future__ import annotations

import inspect
import os
from pathlib import Path

import backend.services.mlto.midlong_direction_audit as mda


def test_guard_active_under_pytest():
    """本进程就在 pytest 里：守卫必须判定为"跳过写盘"。"""
    assert os.environ.get("PYTEST_CURRENT_TEST"), "本测试应在 pytest 下运行"
    assert mda._skip_write_under_pytest() is True


def test_no_write_to_production_path_under_pytest(monkeypatch, tmp_path):
    """核心回归：pytest 下不写生产审计文件（用临时路径观察写入与否）。"""
    target = tmp_path / "audit.jsonl"
    monkeypatch.delenv("MIDLONG_DIRECTION_AUDIT_PATH", raising=False)
    monkeypatch.setattr(mda, "_path", lambda: str(target))
    mda.record_decision_audit(outcome="opened", stage="exec", symbol="TESTCOIN",
                              reason="filled")
    assert not target.exists(), "pytest 进程往审计文件写了夹具行（缺陷 #68 复发）"


def test_writes_when_not_under_pytest(monkeypatch, tmp_path):
    """非 pytest 进程照常写（守卫不能把生产写盘也一起关掉）。"""
    target = tmp_path / "audit.jsonl"
    monkeypatch.setattr(mda, "_path", lambda: str(target))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    _invalidate_guard_cache()
    mda.record_decision_audit(outcome="opened", stage="exec", symbol="BTCUSDT",
                              reason="filled")
    assert target.exists() and target.read_text(encoding="utf-8").strip(), \
        "非 pytest 环境也没写盘 ⇒ 守卫过宽"


def test_explicit_redirect_still_writes(monkeypatch, tmp_path):
    """显式重定向（测试常用）时应当写进临时文件 —— 守卫只拦"未重定向"的情形。"""
    target = tmp_path / "redirect.jsonl"
    monkeypatch.setenv("MIDLONG_DIRECTION_AUDIT_PATH", str(target))
    _invalidate_guard_cache()
    mda.record_decision_audit(outcome="skip", stage="gate", symbol="ETHUSDT",
                              reason="score_low(30<32)")
    assert target.exists() and "ETHUSDT" in target.read_text(encoding="utf-8")


def test_write_failure_is_visible():
    """写入失败必须可见（WARNING 及以上）——原实现是 `logger.debug` 静默。"""
    src = inspect.getsource(mda._write_row)
    assert "logger.warning" in src, "审计写失败又变成静默了"
    assert "logger.debug" not in src


def test_guard_is_wired_into_write_row():
    """反漂移：守卫必须真的被 `_write_row` 调用（只定义不接线等于没修）。"""
    src = inspect.getsource(mda._write_row)
    assert "_skip_write_under_pytest" in src, "守卫没有接进 `_write_row`"


def _invalidate_guard_cache() -> None:
    """守卫若将来加缓存，这里保证环境变量改动后重新求值。"""
    fn = getattr(mda, "_skip_write_under_pytest", None)
    if fn is not None and hasattr(fn, "cache_clear"):
        fn.cache_clear()
