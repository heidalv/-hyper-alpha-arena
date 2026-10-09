"""h584 — 查行情数据链的**写入职责**（只读，R101）。

目的：采集器脚本已被误删且无法重启 ✗。要判断"能否准备一个**待命**替换品"，
必须先弄清数据链的写入分工：
  · 原始逐笔（`asterdex_trades`）由谁写？
  · 聚合表（`market_trades_aggregated`，**判定的成交依据**）由谁写（采集器？还是另有聚合器）？
  · 当前有哪些进程在写（看是否有独立的聚合器存活）？

用法：python scripts/h584_market_write_duties.py
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import datetime as dt
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402

TABLES = ("asterdex_trades", "market_trades_aggregated", "asterdex_stream_health",
          "asterdex_depth")


def main() -> int:
    mk = h.read_env_dsn().replace("/alpha_arena", "/alpha_market")
    with psycopg.connect(mk) as c, c.cursor() as cur:
        print("=" * 96)
        print("行情库各表的行数 + 最新时间戳（判断「谁在写、写得还新不新」）")
        print("=" * 96)
        for t in TABLES:
            try:
                cur.execute(f"SELECT count(*) FROM {t}")
                n = cur.fetchone()[0]
                # 找出时间列
                cur.execute(
                    "SELECT column_name, data_type FROM information_schema.columns"
                    " WHERE table_name=%s ORDER BY ordinal_position", (t,))
                cols = cur.fetchall()
                print(f"\n  [{t}]  行数={n}")
                print("    列: " + ", ".join(f"{c0}:{c1}" for c0, c1 in cols[:12])
                      + (" …" if len(cols) > 12 else ""))
                tcol = None
                for c0, c1 in cols:
                    if c0 in ("recv_ts_ns", "timestamp", "ts", "created_at", "event_ms"):
                        tcol = c0
                        break
                if tcol and n:
                    cur.execute(f"SELECT max({tcol}) FROM {t}")
                    mx = cur.fetchone()[0]
                    if isinstance(mx, (int, float)) and mx > 1e12:      # epoch ms/ns
                        _ms = mx / 1e6 if mx > 1e15 else mx
                        age = dt.datetime.now().timestamp() - _ms / 1000.0
                        print(f"    最新 {tcol}={mx} ⇒ 滞后 {age:.0f}s")
                    else:
                        print(f"    最新 {tcol}={mx}")
            except Exception as exc:  # noqa: BLE001
                print(f"\n  [{t}] 查询失败: {type(exc).__name__}: {str(exc)[:80]}")
    print("\n" + "=" * 96)
    print("当前进程（谁可能在写行情库）")
    print("=" * 96)
    import subprocess
    q = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\""
         " | Select-Object ProcessId,CommandLine | Format-List"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    for blk in (q.stdout or "").split("\n\n"):
        if "CommandLine" in blk:
            cl = " ".join(l.strip() for l in blk.splitlines() if l.strip().startswith("CommandLine"))
            pid = " ".join(l.strip() for l in blk.splitlines() if l.strip().startswith("ProcessId"))
            if any(k in blk.lower() for k in ("ingest", "aggregat", "market", "aster", "worker")):
                print(f"  {pid.split(':',1)[-1].strip():>7}  {cl.split(':',1)[-1].strip()[:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
