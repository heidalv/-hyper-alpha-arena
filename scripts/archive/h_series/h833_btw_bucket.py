# -*- coding: utf-8 -*-
"""只重算 BTW 做多、前面 30 到 100 美元、价差 8 到 16bp 这一档。"""
import importlib.util
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("h829", ROOT / "scripts" / "h829_queue_split.py")
h829 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h829)

lo = int((time.time() - 12 * 3600) * 1000)
with __import__("psycopg").connect(
        __import__("backend.services.market_maker.attribution", fromlist=["_market_dsn"])._market_dsn(),
        autocommit=True) as conn:
    pack = h829.load(conn, "BTWUSDT", lo)
_a, rows = h829.simulate(pack, "buy")
part = [r for r in rows if 30 <= r[4] < 100 and 8 <= r[5] < 16]
print("n", len(part))
ys = np.array([r[2] for r in part])
ts = np.array([r[0] for r in part])
kinds = [r[6] for r in part]
print("mean", ys.mean(), "median", np.median(ys), "maker", kinds.count("maker"), "stop", kinds.count("stop"))
cuts = np.quantile(ts, [0.33, 0.66])
for name, mask in (
    ("前", ts <= cuts[0]),
    ("中", (ts > cuts[0]) & (ts <= cuts[1])),
    ("后", ts > cuts[1]),
):
    z = ys[mask]
    print(name, "n", len(z), "mean", z.mean(), "median", np.median(z))
