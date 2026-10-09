# -*- coding: utf-8 -*-
"""[P0 大轮回 2026-09-27] 持仓逐 5 分钟快照流（§10.1/§10.3「持仓快照断档」门禁的数据源）。

设计（大轮回 §10.1）：持仓每 1 分钟快照（可降采样到 5 分钟），含 MFE/MAE。
本实现取 5 分钟档：每轮把全部 open 仓的 (mark, 浮动盈亏%, mfe, mae) 落一行，
供出场质量分析（MFE 实现率）、断档门禁与回测成本对账使用。

幂等：唯一约束 (position_id, ts_bucket)（ts_bucket = unix 秒 // 300），重跑同桶不重复。
保留：仅 open 仓；平仓后不再写。清理：>90 天的行每日随快照轮次顺带删除。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

_ensured = False
_ensure_lock = threading.Lock()
_BUCKET_S = 300


def ensure_table() -> None:
    global _ensured
    if _ensured:
        return
    with _ensure_lock:
        if _ensured:
            return
        try:
            from sqlalchemy import text
            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    db.execute(text(
                        "CREATE TABLE IF NOT EXISTS position_snapshots ("
                        " id BIGSERIAL PRIMARY KEY,"
                        " position_id INTEGER NOT NULL,"
                        " account_id INTEGER,"
                        " symbol VARCHAR(32),"
                        " ts_bucket BIGINT NOT NULL,"
                        " ts TIMESTAMPTZ NOT NULL DEFAULT now(),"
                        " mark_price DOUBLE PRECISION,"
                        " unrealized_pnl DOUBLE PRECISION,"
                        " unrealized_pnl_pct DOUBLE PRECISION,"
                        " mfe_pct DOUBLE PRECISION,"
                        " mae_pct DOUBLE PRECISION,"
                        " CONSTRAINT uq_psnap_pos_bucket UNIQUE (position_id, ts_bucket)"
                        ")"
                    ))
                    db.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_psnap_pos_ts"
                        " ON position_snapshots (position_id, ts DESC)"
                    ))
                    db.commit()
            _ensured = True
        except Exception as exc:
            logger.warning("[PositionSnapshotter] 建表失败: %s", exc)


def snapshot_open_positions() -> Dict[str, Any]:
    """快照当前全部 open 仓（5 分钟桶幂等）。返回本轮统计。"""
    ensure_table()
    from sqlalchemy import text
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal

    bucket = int(time.time()) // _BUCKET_S
    out: Dict[str, Any] = {"bucket": bucket, "rows": 0, "positions": 0, "error": None}
    try:
        with system_identity():
            with SessionLocal() as db:
                rows = db.execute(text(
                    "SELECT id, account_id, symbol, mark_price, unrealized_pnl, "
                    "       entry_price, size, peak_pnl_pct, trough_pnl_pct "
                    "FROM paper_positions WHERE status='open'"
                )).fetchall()
                out["positions"] = len(rows)
                for r in rows:
                    pid, acct, sym, mark, upnl, entry, size, peak, trough = r
                    pct = None
                    if entry and size:
                        notional = float(entry) * float(size)
                        if notional > 0:
                            pct = round(float(upnl or 0) / notional * 100.0, 6)
                    db.execute(text(
                        "INSERT INTO position_snapshots"
                        " (position_id, account_id, symbol, ts_bucket, ts, mark_price,"
                        "  unrealized_pnl, unrealized_pnl_pct, mfe_pct, mae_pct)"
                        " VALUES (:pid,:acct,:sym,:b,now(),:mk,:up,:pct,:mfe,:mae)"
                        " ON CONFLICT (position_id, ts_bucket) DO NOTHING"
                    ), {
                        "pid": pid, "acct": acct, "sym": sym, "b": bucket,
                        "mk": float(mark or 0) if mark else None,
                        "up": float(upnl or 0) if upnl is not None else None,
                        "pct": pct,
                        "mfe": float(peak or 0) if peak is not None else None,
                        "mae": float(trough or 0) if trough is not None else None,
                    })
                    out["rows"] += 1
                # 顺带清理 >90 天
                db.execute(text(
                    "DELETE FROM position_snapshots WHERE ts < now() - interval '90 days'"
                ))
                db.commit()
    except Exception as exc:
        out["error"] = str(exc)[:160]
        logger.warning("[PositionSnapshotter] 快照失败: %s", exc)
    return out


def register() -> None:
    """注册 5 分钟间隔任务（由 v3_jobs 调用）。"""
    from backend.services.ops.job_registry import register_job
    register_job(
        "position_snapshot_5m", "interval 300s",
        "持仓逐 5 分钟快照（position_snapshots：mark/浮盈%/MFE/MAE，幂等桶）——§10.1/§10.3 断档门禁数据源",
        owner="p0", runner=snapshot_open_positions, expected_interval_sec=300,
    )
