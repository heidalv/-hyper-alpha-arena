"""F16 ORM 列宽一致性测试（2026-09-02 因子系统闭环修复）。

原病症：``SignalTradeFeedback.signal_type`` 的 ORM 声明是 ``String(30)``，而线上
数据库列早在迁移 0008 就扩到了 ``VARCHAR(100)``。

这不是"看着不一致"的小事 —— 迁移 0008 自己的注释记录了 30 宽时的后果：
「AI 生成因子名（如 factor:cloud_microstructure_kyle）超长 → bulk_save 整批回滚
→ 开仓零快照 → IC 闭环收不到样本」。而所有走 ``Base.metadata.create_all`` 的建表
路径（0001 baseline、init_db.py、init_postgresql.py、api/paper_trading_routes.py
以及大量 SQLite 单测）都按 ORM 声明建列，会把这个已修过的 bug 重新建出来。
SQLite 不强制 VARCHAR 长度，所以单测也发现不了 —— 更需要显式的不变式测试。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from backend.database.models import SignalTradeFeedback

_DB_COLUMN_WIDTH = 100  # 迁移 0008 落定的实际列宽


def _signal_type_width() -> int:
    return int(SignalTradeFeedback.__table__.c.signal_type.type.length)


def test_orm_matches_database_column_width():
    """ORM 声明必须等于数据库实际列宽（模型即真相）。"""
    assert _signal_type_width() == _DB_COLUMN_WIDTH, (
        f"ORM signal_type 宽度 {_signal_type_width()} != 数据库 {_DB_COLUMN_WIDTH}；"
        "create_all 路径会建出错误宽度，重新引入 bulk_save 整批回滚"
    )


def test_writer_truncation_matches_orm_width():
    """写入端的截断长度必须与 ORM 列宽一致。

    ``record_entry_signals`` 里硬编码了截断阈值。两处一旦错位：截断值大于列宽 →
    写入报 StringDataRightTruncation 整批回滚；小于列宽 → 因子名被无谓截断，
    产生查不到的孤儿 key（B4 清理掉的 29 个截断残留就是这么来的）。
    """
    import inspect

    from backend.services import signal_feedback_tracker as sft

    src = inspect.getsource(sft.SignalFeedbackTracker.record_entry_signals)
    live = "\n".join(
        ln for ln in src.splitlines() if not ln.strip().startswith("#")
    )
    assert f"> {_DB_COLUMN_WIDTH}" in live, (
        f"写入端截断阈值与列宽 {_DB_COLUMN_WIDTH} 不一致"
    )
    assert f"[:{_DB_COLUMN_WIDTH}]" in live


def test_real_factor_names_fit_in_column():
    """迁移 0008 举证过的超长因子名，以及本仓库最长的实际因子名都必须放得下。"""
    from pathlib import Path

    samples = ["cloud_microstructure_kyle"]
    _root = Path(__file__).resolve().parents[3]
    for _d in (
        _root / "backend/services/factor_engine/factors/ai_generated",
        _root / "backend/services/factor_engine/factors/_ai_gen_archive",
    ):
        if _d.is_dir():
            samples += [p.stem for p in _d.glob("*.py") if p.stem != "__init__"]

    longest = max(samples, key=len)
    stype = f"factor:{longest}"
    assert len(stype) <= _DB_COLUMN_WIDTH, (
        f"最长因子名仍会被截断: {stype!r} 长度 {len(stype)} > {_DB_COLUMN_WIDTH}"
    )


def test_create_all_builds_expected_width():
    """按 ORM 建表后，列宽就是期望值（覆盖 create_all 建表路径）。"""
    from sqlalchemy import create_engine, inspect as _sa_inspect

    engine = create_engine("sqlite://", future=True)
    try:
        SignalTradeFeedback.__table__.create(engine)
        cols = {
            c["name"]: c for c in _sa_inspect(engine).get_columns(
                "signal_trade_feedback")
        }
        assert f"{_DB_COLUMN_WIDTH}" in str(cols["signal_type"]["type"]).upper()
    finally:
        engine.dispose()
