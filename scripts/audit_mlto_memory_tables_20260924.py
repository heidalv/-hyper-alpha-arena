# -*- coding: utf-8 -*-
"""[R22] MLTO 各记忆表行数体检：判断"记忆为空"是死代码还是换了表。"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402

from backend.database.connection import analytics_engine, engine  # noqa: E402

TABLES = [
    "mlto_memory_events",
    "mlto_episodes",
    "mlto_thesis",
    "mlto_thesis_events",
    "mlto_signal_weights",
    "mlto_debate_logs",
    "mlto_hub_decisions",
    "mlto_committee_logs",
]

for label, eng in (("analytics", analytics_engine), ("main", engine)):
    print(f"=== {label} ===")
    for t in TABLES:
        try:
            with eng.connect() as c:
                n = c.execute(text(f"SELECT count(*) FROM {t}")).scalar()
                last = None
                for col in ("created_at", "updated_at", "ts", "event_ts"):
                    try:
                        last = c.execute(
                            text(f"SELECT max({col}) FROM {t}")
                        ).scalar()
                        if last is not None:
                            last = f"{col}={last}"
                            break
                    except Exception:
                        c.rollback()
                print(f"  {t:24s} n={n}  {last or ''}")
        except Exception as e:  # noqa: BLE001
            print(f"  {t:24s} ERR {str(e)[:90]}")
