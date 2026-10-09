# -*- coding: utf-8 -*-
"""[h839] 追 DB I/O:连接开销 vs 查询开销,以及每个 tick 的 DB 调用点。"""
import io
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
from backend.services.market_maker.attribution import _market_dsn  # noqa: E402
import psycopg  # noqa: E402

# ① 建连开销(市场库)
t0 = time.perf_counter()
for _ in range(10):
    with psycopg.connect(_market_dsn(), autocommit=True) as c:
        pass
print(f"① 市场库 建连+关闭: {(time.perf_counter() - t0) / 10 * 1000:.1f} ms/次")

# ② 建连开销(账本库)
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
t0 = time.perf_counter()
for _ in range(10):
    with psycopg.connect(h.read_env_dsn(), autocommit=True) as c:
        pass
print(f"② 账本库 建连+关闭: {(time.perf_counter() - t0) / 10 * 1000:.1f} ms/次")

# ③ 典型查询开销(市场库 fetch_market 的同款)
with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute("SELECT count(*) FROM asterdex_trades WHERE event_ts_ms > %s",
                (int((time.time() - 60) * 1000),))
    _ = cur.fetchone()
    t0 = time.perf_counter()
    for _ in range(20):
        cur.execute(
            "SELECT symbol, min(price), max(price),"
            " sum(CASE WHEN is_buyer_maker THEN qty*price ELSE 0 END),"
            " sum(CASE WHEN NOT is_buyer_maker THEN qty*price ELSE 0 END)"
            " FROM asterdex_trades WHERE event_ts_ms > %s GROUP BY symbol",
            (int((time.time() - 20) * 1000),))
        cur.fetchall()
    print(f"③ 同连接下 批量聚合查询: {(time.perf_counter() - t0) / 20 * 1000:.1f} ms/次")

# ④ tick 里的 DB 调用点统计(源码扫描)
import re  # noqa: E402
src = (ROOT / "backend" / "services" / "market_maker" / "runner.py").read_text(
    encoding="utf-8", errors="replace")
n_market = len(re.findall(r"_market_dsn\(", src))
n_conn = len(re.findall(r"psycopg\.connect\(", src))
n_ledger = len(re.findall(r"lang_ledger|lane_ledger", src))
print(f"④ runner 源码统计: _market_dsn( {n_market} 处 | psycopg.connect( {n_conn} 处 | "
      f"lane_ledger 出现 {n_ledger} 次")
import subprocess  # noqa: E402
r = subprocess.run(["powershell", "-NoProfile", "-Command",
                    "(Get-Process -Id (Get-CimInstance Win32_Process "
                    "-Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine "
                    "-match 'mm_lane_worker' } | Select-Object -First 1 -ExpandProperty ProcessId)"
                    " -ErrorAction SilentlyContinue).CPU"],
                   capture_output=True, text=True)
print(f"⑤ worker 进程累计 CPU 秒: {(r.stdout or '').strip() or 'n/a'}"
      f"(对比墙钟:若远小于墙钟 ⇒ 在等 I/O)")
