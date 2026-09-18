"""Migration: period_daily_reports 新增 lane 列并回填历史行（轮63，2026-09-18）。

背景：旧表只有 `horizon`（scalp/midlong/long），而这三个值与交易引擎的车道不是同一套东西
（详见 `backend/config/lane_semantics.py` 开头的对照表）。报告层收敛为双车道后，
历史行必须能被新口径读出来，否则「改完之后旧报告全查不到」比不改更糟。

本迁移做三件事，全部幂等：

1. 加列 `lane`（String(16)，带索引）；
2. 回填 `lane = normalize(horizon)`：  scalp/midlong→intraday、long→trend、research→research；
3. 加唯一索引 `uq_period_daily_reports_acct_date_lane`——**仅当不存在重复行时创建**。
   唯一索引的意义：旧 upsert 靠"先查后插"，一旦并发或历史脏数据留下重复行，
   报告就会出现同名双行（实测 2026-09-17 曾出现重复 horizon），本次顺手把它堵住。

不重写 `horizon` 列的值（列名保留作兼容别名，写入端已改为写规范车道名）。
跨库安全：只用 SQLAlchemy 的 DDL/ORM，不写方言专用原生 SQL。
"""

import logging

logger = logging.getLogger(__name__)

_TABLE = "period_daily_reports"
_INDEX = "uq_period_daily_reports_acct_date_lane"


def _lane_for_horizon(raw) -> str:
    from backend.config.lane_semantics import DEFAULT_LANE, lane_for_label

    return lane_for_label(raw) or DEFAULT_LANE


def upgrade():
    from sqlalchemy import inspect, text

    from backend.database.connection import engine

    inspector = inspect(engine)
    if _TABLE not in set(inspector.get_table_names()):
        logger.info("%s 不存在，跳过 lane 迁移（建表迁移会带上该列）", _TABLE)
        return

    # ── 1. 加列 ────────────────────────────────────────────────────────
    cols = {c["name"] for c in inspector.get_columns(_TABLE)}
    if "lane" not in cols:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {_TABLE} ADD COLUMN lane VARCHAR(16)"))
            logger.info("Added %s.lane", _TABLE)
        except Exception as e:
            logger.warning("Add %s.lane failed (will retry next startup): %s", _TABLE, e)
            return
    else:
        logger.debug("%s.lane 已存在", _TABLE)

    # ── 2. 回填（按 horizon 分组，逐组一条 UPDATE，避免逐行往返）──────────
    try:
        with engine.begin() as conn:
            rows = conn.execute(text(
                f"SELECT DISTINCT horizon FROM {_TABLE} "
                f"WHERE lane IS NULL AND horizon IS NOT NULL"
            )).fetchall()
            for (h,) in rows:
                lane = _lane_for_horizon(h)
                res = conn.execute(text(
                    f"UPDATE {_TABLE} SET lane = :lane WHERE lane IS NULL AND horizon = :h"
                ), {"lane": lane, "h": h})
                logger.info("回填 lane=%s ← horizon=%s（%s 行）", lane, h, res.rowcount)
            # 没有任何 horizon 的孤儿行也要归位，否则新口径读不到
            res = conn.execute(text(
                f"UPDATE {_TABLE} SET lane = :lane WHERE lane IS NULL"
            ), {"lane": _lane_for_horizon(None)})
            if res.rowcount:
                logger.info("回填无标签行 lane=%s（%s 行）", _lane_for_horizon(None), res.rowcount)
    except Exception as e:
        logger.warning("回填 %s.lane 失败（可重跑本迁移）: %s", _TABLE, e)
        return

    # ── 3. 让旧列 horizon 与 lane 对齐 ──────────────────────────────────
    # 两列取值不一致会让「按 lane 查」与「按 horizon 查」给出不同结果 —— 正是本轮要消除的歧义。
    # 写入端已改为两列同值，这里把存量行补齐。
    try:
        with engine.begin() as conn:
            res = conn.execute(text(
                f"UPDATE {_TABLE} SET horizon = lane "
                f"WHERE horizon IS DISTINCT FROM lane AND lane IS NOT NULL"
            ))
            if res.rowcount:
                logger.info("对齐 horizon ← lane（%s 行）", res.rowcount)
    except Exception as e:
        logger.warning("对齐 %s.horizon 失败（不影响读写，可重跑）: %s", _TABLE, e)

    # ── 4. 唯一索引（先查重；有重复则只加普通索引，不阻断启动）─────────────
    try:
        with engine.begin() as conn:
            dup = conn.execute(text(
                f"SELECT account_id, report_date, lane, COUNT(*) c FROM {_TABLE} "
                f"GROUP BY account_id, report_date, lane HAVING COUNT(*) > 1 LIMIT 1"
            )).fetchone()
        if dup:
            logger.warning(
                "%s 存在重复行 (account_id=%s, report_date=%s, lane=%s, n=%s)，"
                "跳过唯一索引；请先清理重复再重跑本迁移",
                _TABLE, dup[0], dup[1], dup[2], dup[3],
            )
            return
        existing = {i["name"] for i in inspect(engine).get_indexes(_TABLE)}
        if _INDEX in existing:
            logger.debug("%s 已存在", _INDEX)
            return
        with engine.begin() as conn:
            conn.execute(text(
                f"CREATE UNIQUE INDEX {_INDEX} ON {_TABLE} (account_id, report_date, lane)"
            ))
        logger.info("Created unique index %s", _INDEX)
    except Exception as e:
        logger.warning("创建 %s 失败（不影响读写，可重跑）: %s", _INDEX, e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    upgrade()
