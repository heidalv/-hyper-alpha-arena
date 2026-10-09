# -*- coding: utf-8 -*-
"""[F249] 第 2 干净日观测：深度数据持续落库 + 看板服务健康。"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, "D:/001Alpha/Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402
from backend.database.connection import MarketSessionLocal  # noqa: E402

BASE = "http://127.0.0.1:8000"


def t(ms):
    return time.strftime("%H:%M:%S", time.localtime(ms / 1000))


with MarketSessionLocal() as db:
    r = db.execute(text(
        "SELECT MAX(timestamp) m, COUNT(*) FILTER (WHERE raw_levels IS NOT NULL) rl"
        " FROM market_orderbook_snapshots WHERE exchange='asterdex'"
        " AND timestamp >= (SELECT MAX(timestamp) FROM market_orderbook_snapshots) - 600000"
    )).mappings().first()
    print(f"近10分钟快照: 最新={t(r['m']) if r['m'] else '?'}  含raw_levels={r['rl']}/{r['m'] and '全部' or 0}")
    print(f"              最新深度时间 = {t(r['m'])}")

d = json.load(urllib.request.urlopen(BASE + "/api/trading/lanes/mm_asterdex/board", timeout=30))
n_depth = sum(1 for it in d["items"] if it.get("depth"))
print(f"看板: {n_depth}/{len(d['items'])} 个币有深度数据")
