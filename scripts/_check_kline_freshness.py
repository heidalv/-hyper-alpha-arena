# -*- coding: utf-8 -*-
"""核查：市场库 K 线数据新鲜度（轻量版，因子回测数据供给）。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import create_engine, text

_env = {}
for _line in Path(__file__).resolve().parents[1].joinpath(".env").read_text(encoding="utf-8").splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        _env[_k] = _v

eng = create_engine(_env["MARKET_DATABASE_URL"], pool_pre_ping=True)
now = int(time.time())
with eng.connect() as c:
    # 每个周期取 max(timestamp)（走索引，轻量）
    for tf in ("5m", "15m", "1h", "4h", "1d"):
        r = c.execute(text(
            "SELECT max(timestamp) FROM crypto_klines WHERE symbol='BTC' AND period=:tf"
        ), {"tf": tf}).scalar()
        age_min = (now - int(r)) // 60 if r else -1
        print(f"BTC {tf:4s} max_ts_age_min={age_min}")
    # 覆盖币数（避免全表 count）
    n_sym = c.execute(text(
        "SELECT count(DISTINCT symbol) FROM crypto_klines WHERE period='5m'"
    )).scalar()
    print(f"5m 覆盖币数={n_sym}")
