# -*- coding: utf-8 -*-
"""Z18：入场来源（模板/信号/决策源）到底记在哪张表？——为「入场来源层」归因做准备。

§27.3 曾用 `tpl_mid_reversio` 指认最大毒性来源，但 Z17 扫 `strategy_trades.decision_context`
的 template_id 大多为 NULL。本轮先把可用的来源字段全部找出来。
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import create_engine, text  # noqa: E402

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
eng = create_engine(URL)

TABLES = ["strategy_trades", "ai_decision_logs", "trade_facts", "hub_decision_log",
          "paper_positions", "midlong_theses", "theses"]


def cols(c, table):
    try:
        rows = c.execute(text("""
            select column_name, data_type from information_schema.columns
            where table_name = :t order by ordinal_position
        """), {"t": table}).fetchall()
        return [(r[0], r[1]) for r in rows]
    except Exception as e:
        c.rollback()
        print(f"  [{table}] 列查询失败: {str(e)[:100]}")
        return []


def json_keys(c, table, col, days=75, limit=400):
    for q in (
        f"select {col} from {table} where {col} is not null "
        f"and created_at >= now() - interval '{int(days)} days' limit {int(limit)}",
        f"select {col} from {table} where {col} is not null limit {int(limit)}",
        f"select {col} from {table} limit {int(limit)}",
    ):
        try:
            rows = c.execute(text(q)).fetchall()
            break
        except Exception:
            c.rollback()
    else:
        return None, "all queries failed"
    keys = Counter()
    for r in rows:
        v = r[0]
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                continue
        if isinstance(v, dict):
            keys.update(v.keys())
    return keys, None


with eng.connect() as c:
    c.execute(text("set app.is_admin='on'"))
    for t in TABLES:
        cs = cols(c, t)
        if not cs:
            print(f"\n### {t}: 不存在")
            continue
        print(f"\n### {t}  ({len(cs)} 列)")
        print("  列: " + ", ".join(n for n, _ in cs))
        for col in ("decision_context", "signal_context", "metadata_json", "context_json",
                    "extra_json", "payload_json", "decision_json", "reasoning_json"):
            if any(n == col for n, _ in cs):
                keys, err = json_keys(c, t, col)
                if err:
                    print(f"  [{col}] 读取失败: {err}")
                elif keys:
                    print(f"  [{col}] JSON 键 top20: "
                          + ", ".join(f"{k}({v})" for k, v in keys.most_common(20)))
                else:
                    print(f"  [{col}] 无 JSON 对象行")
