# -*- coding: utf-8 -*-
"""[2026-09-09 根因修复] brain_attribution 幂等写穿契约测试。

数据依据（alpha_arena 实测）：修复前 1323 行 vs 721 个唯一
`(position_id, src, tier)`，单仓最多重复 18 行；`tier=mid src=llm` 的 238 行
`pnl` 恒 -1.0 占位行集中在 2026-08-29 14:59–18:34，导致按表归因得出
「LLM mid 全亏 -238」的错误结论。迁移 0024 建唯一索引 + 写入端 ON CONFLICT。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

pytestmark = pytest.mark.usefixtures()

_PID = 999_991


def _db_ready() -> bool:
    try:
        from sqlalchemy import text

        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text("select 1 from brain_attribution limit 1"))
        finally:
            db.close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_ready(), reason="需要 alpha_arena 库与 brain_attribution 表")
def test_attribution_row_is_idempotent():
    from sqlalchemy import text

    from backend.database.connection import SessionLocal
    from backend.services.source_attribution import _record_attribution_row

    def _rows():
        db = SessionLocal()
        try:
            return db.execute(text(
                "select pnl, net, close_reason from brain_attribution where position_id=:p"
            ), {"p": _PID}).mappings().all()
        finally:
            db.close()

    def _cleanup():
        db = SessionLocal()
        try:
            db.execute(text("delete from brain_attribution where position_id=:p"), {"p": _PID})
            db.commit()
        finally:
            db.close()

    _cleanup()
    try:
        for i in range(3):
            _record_attribution_row(
                position_id=_PID, src="llm", nature="swing", symbol="TEST",
                pnl=-1.0 + i, fee=0.0, net=-1.0 + i, win=False,
                close_reason="unit_test", tier="mid",
            )
        rows = _rows()
        assert len(rows) == 1, f"应幂等只保留 1 行，实际 {len(rows)}"
        # 三次写入 pnl = -1.0 / 0.0 / 1.0，最后一次生效
        assert float(rows[0]["pnl"]) == 1.0
    finally:
        _cleanup()
