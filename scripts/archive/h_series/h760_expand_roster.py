# -*- coding: utf-8 -*-
"""[h760] 把"宽价差但不在数据管道名单"的币加进 user_trading_pairs。

根因:DC 快照市场名单 = system_configs 的 hyperliquid_selected_symbols ∪
user_trading_pairs(以及 running 会话)。宽价差币(SEI/PUMP/ADA/AAVE/LIT/ENA/XMR)
不在名单 ⇒ 选择器 `_snapshot_age_s` 门槛把它们全部挡下 ⇒ 合格池只有 5 个
(14 个宽价差币里 9 个缺 DC 快照)。
本脚本**只做加法**(保留原交易对),不改其它配置。
"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.database.connection import SessionLocal

ADD = ["SEI", "PUMP", "ADA", "AAVE", "LIT", "ENA", "XMR"]

with SessionLocal() as db:
    row = db.execute(text(
        "SELECT value FROM system_configs WHERE key='user_trading_pairs'")).first()
    cur = []
    if row and row[0]:
        try:
            v = row[0]
            cur = json.loads(v) if isinstance(v, str) else list(v)
        except Exception as e:
            print("解析失败,放弃写入:", e)
            sys.exit(1)
    before = list(cur)
    merged = sorted({str(x).upper() for x in cur} | set(ADD))
    db.execute(text(
        "UPDATE system_configs SET value=:v WHERE key='user_trading_pairs'"),
        {"v": json.dumps(merged)})
    db.commit()
    print("写入前:", before)
    print("写入后:", merged)
    print(f"新增 {sorted(set(merged) - set(str(x).upper() for x in before))}")
