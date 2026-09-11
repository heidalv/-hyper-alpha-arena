# -*- coding: utf-8 -*-
"""scalp_signal_log 加 triple-barrier 标签列（幂等，2026-09-02 P1.1）。

为什么加列而不是改写旧列：原 net_ret/win 是"固定 30 分钟后收盘价"口径，已积累
12.8 万条样本。直接改语义会让新旧标签混在同一列里，模型训练时无从区分。故新增
tb_* 一套并行列，两套标签共存，由 SCALP_META_LABEL 决定 meta 模型采信哪个。

用法：python scripts/migrate_triple_barrier_columns.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from backend.database.connection import engine

# (列名, 类型) —— 与 models.ScalpSignalLog 的 tb_* 字段保持一致
_COLUMNS = [
    ("tb_tp_pct", "DOUBLE PRECISION"),
    ("tb_sl_pct", "DOUBLE PRECISION"),
    ("tb_max_hold_sec", "INTEGER"),
    ("tb_kind", "VARCHAR(12)"),
    ("tb_hold_sec", "INTEGER"),
    ("tb_fwd_ret", "DOUBLE PRECISION"),
    ("tb_net_ret", "DOUBLE PRECISION"),
    ("tb_win", "BOOLEAN"),
    ("tb_settled", "BOOLEAN"),
]


def main() -> int:
    with engine.connect() as db:
        db.execute(text("COMMIT"))  # 退出隐式事务，DDL 逐条自动提交
        for name, sql_type in _COLUMNS:
            db.execute(text(
                f"ALTER TABLE scalp_signal_log "
                f"ADD COLUMN IF NOT EXISTS {name} {sql_type}"
            ))
            db.execute(text("COMMIT"))
        db.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_scalp_signal_tb "
            "ON scalp_signal_log (tb_settled, signal_ts)"
        ))
        db.execute(text("COMMIT"))

        got = [r[0] for r in db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='scalp_signal_log' AND column_name LIKE 'tb_%' "
            "ORDER BY column_name"
        ))]
        print("tb_* columns:", got)
        missing = {c for c, _ in _COLUMNS} - set(got)
        if missing:
            print("MISSING:", sorted(missing))
            return 1

        n = db.execute(text("SELECT count(*) FROM scalp_signal_log")).scalar()
        n_old = db.execute(text(
            "SELECT count(*) FROM scalp_signal_log WHERE settled = true"
        )).scalar()
        print(f"rows={n} old_settled={n_old} (旧标签保持不变)")
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
