# -*- coding: utf-8 -*-
"""[F362 2026-09-18] v7 Codegen 记忆池的**可达性**不变量。

实测（`scripts/probe_v7_codegen_reachability.py`）：`build_codegen_context` 先
`ORDER BY id DESC LIMIT 200` **再**打分，于是在 docstring 声明的排序之外**偷偷叠加**
一层"最新 200 条"过滤 ⇒ active 810 条里 **528 条（65.2%）** 永远进不了挖掘 prompt。

本文件用**真库（临时 SQLite）**锁两件事：
1. 默认（`V7_CODEGEN_FULL_POOL` 未设）逐字节保持旧行为：窗口外的教训**读不到**；
2. 置 `1` 后全量参与打分：**最高质量的窗口外教训必须被读到**；
3. 窗口饱和时**必须告警**（禁止静默退化），且同一 (period, cycle) 只告警一次。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.evolution import evolution_memory_v7 as EV7  # noqa: E402


def _mkdb(tmp_path: Path, *, n_old: int = 5, n_new: int = 205) -> Path:
    """造一个 (4h, L) 匹配数 > 200 的库；最老的 n_old 条质量最高。"""
    p = tmp_path / "v7.db"
    con = sqlite3.connect(str(p))
    con.executescript(EV7._SCHEMA)
    now = datetime.now(timezone.utc).isoformat()
    # 先写"最老但质量最高"的教训（id 最小 ⇒ 必然落在 LIMIT 200 窗口之外）
    for i in range(n_old):
        con.execute(
            "INSERT INTO v7_lessons (created_at,kind,cycle,period,title,summary,quality)"
            " VALUES (?,?,?,?,?,?,?)",
            (now, "success_recipe", "L", "4h",
             f"黄金配方-{i} 因子挖掘 晋升 拒绝 衰退 换手 ICIR", "高价值教训", 0.99))
    # 再写大量新的低质量教训，把老教训挤出窗口
    for i in range(n_new):
        con.execute(
            "INSERT INTO v7_lessons (created_at,kind,cycle,period,title,summary,quality)"
            " VALUES (?,?,?,?,?,?,?)",
            (now, "pipeline_issue", "L", "4h", f"链路问题-{i} 噪声", "低价值", 0.5))
    con.commit()
    con.close()
    return p


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    EV7._CODEGEN_SATURATED_WARNED.clear()
    monkeypatch.delenv("V7_CODEGEN_FULL_POOL", raising=False)
    yield


def test_default_keeps_legacy_window(tmp_path, monkeypatch):
    """默认关 ⇒ 旧行为：窗口外的高质量教训**读不到**（这是要被记录的事实，不是 bug 修复）。"""
    db = _mkdb(tmp_path)
    monkeypatch.setattr(EV7, "_DB_PATH", db, raising=False)
    ctx = EV7.build_codegen_context("4h", limit=8)
    assert ctx, "应产出上下文"
    assert "黄金配方-0" not in ctx, "默认模式下老教训必须在窗口外（旧行为）"
    assert "链路问题-" in ctx


def test_full_pool_flag_makes_oldest_reachable(tmp_path, monkeypatch):
    """置 1 ⇒ 取消 id 窗口：质量最高的老教训必须被读到。"""
    db = _mkdb(tmp_path)
    monkeypatch.setattr(EV7, "_DB_PATH", db, raising=False)
    monkeypatch.setenv("V7_CODEGEN_FULL_POOL", "1")
    ctx = EV7.build_codegen_context("4h", limit=8)
    assert "黄金配方-0" in ctx, "全量模式下最高质量的老教训应被读到"


def test_f362_reachability_pin(tmp_path, monkeypatch):
    """把"可达数"钉住：默认模式下可达 = min(200, 匹配数)，其余结构不可达。"""
    db = _mkdb(tmp_path, n_old=5, n_new=205)      # 匹配 210 条
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    total = con.execute("SELECT COUNT(*) FROM v7_lessons WHERE status='active'").fetchone()[0]
    window = con.execute(
        "SELECT COUNT(*) FROM (SELECT id FROM v7_lessons WHERE status='active' "
        "AND (period='4h' OR cycle='L') ORDER BY id DESC LIMIT 200)").fetchone()[0]
    con.close()
    assert total == 210 and window == 200
    monkeypatch.setattr(EV7, "_DB_PATH", db, raising=False)
    # 默认：最多 200 条候选，其中 quality 最高的 8 条进 prompt；老教训不在候选里
    ev7_ctx = EV7.build_codegen_context("4h", limit=8)
    assert "黄金配方" not in ev7_ctx
    # 全量：210 条候选，老教训（quality 0.99）必然进 prompt
    monkeypatch.setenv("V7_CODEGEN_FULL_POOL", "1")
    assert "黄金配方-0" in EV7.build_codegen_context("4h", limit=8)


def test_saturation_is_logged_once(tmp_path, monkeypatch, caplog):
    import logging
    db = _mkdb(tmp_path, n_old=0, n_new=205)
    monkeypatch.setattr(EV7, "_DB_PATH", db, raising=False)
    with caplog.at_level(logging.WARNING):
        EV7.build_codegen_context("4h", limit=8)
        EV7.build_codegen_context("4h", limit=8)
    msgs = [r.message for r in caplog.records if "LIMIT 200" in r.message]
    assert len(msgs) == 1, f"同一 period/cycle 只应告警一次，实测 {len(msgs)} 条"
    assert "不可达" in msgs[0]


def test_no_saturation_warning_when_under_window(tmp_path, monkeypatch, caplog):
    import logging
    db = _mkdb(tmp_path, n_old=2, n_new=10)
    monkeypatch.setattr(EV7, "_DB_PATH", db, raising=False)
    with caplog.at_level(logging.WARNING):
        EV7.build_codegen_context("4h", limit=8)
    assert not [r for r in caplog.records if "LIMIT 200" in r.message]


def test_docstring_documents_the_window_and_flag():
    """口径必须写在函数 docstring 里（否则下一个人会把 200 窗口当成唯一排序口径）。"""
    doc = EV7.build_codegen_context.__doc__ or ""
    assert "LIMIT 200" in doc and "V7_CODEGEN_FULL_POOL" in doc
    assert "65.2%" in doc, "应写明实测不可达比例"
