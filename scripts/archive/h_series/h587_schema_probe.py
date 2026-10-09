"""h587 — 为待命采集器**从真实数据反推字段语义**（只读，R102）。

不能靠猜 ✗：写生产数据写入器前，必须知道
  · `asterdex_trades` 每列对应 WS 事件的哪个字段（aggTrade 的 a/p/q/T/m/E…）；
  · `asterdex_book_ticker` / `asterdex_depth_snapshots` 的列与样本形态；
  · 心跳表 `asterdex_stream_health` 的更新语义（msgs_total 增量？reconnects 语义？）。

用法：python scripts/h587_schema_probe.py
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

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

TABLES = ("asterdex_trades", "asterdex_book_ticker", "asterdex_depth_snapshots")


def main() -> int:
    mk = h.read_env_dsn().replace("/alpha_arena", "/alpha_market")
    with psycopg.connect(mk) as c, c.cursor() as cur:
        for t in TABLES:
            print("=" * 100)
            print(f"[{t}]")
            print("=" * 100)
            cur.execute(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
                " WHERE table_name=%s ORDER BY ordinal_position", (t,))
            cols = cur.fetchall()
            print("  列定义：")
            for name, typ, nul in cols:
                print(f"    {name:<28}{typ:<28}{'' if nul == 'NO' else 'NULL'}")
            if t == "asterdex_depth_snapshots":
                cur.execute(f"SELECT * FROM {t} ORDER BY id DESC LIMIT 1")
            elif t == "asterdex_trades":
                cur.execute(f"SELECT * FROM {t} ORDER BY id DESC LIMIT 2")
            else:
                cur.execute(f"SELECT * FROM {t} ORDER BY id DESC LIMIT 1")
            names = [d[0] for d in cur.description]
            for row in cur.fetchall():
                print("  样本行：")
                for n, v in zip(names, row):
                    sv = str(v)
                    print(f"    {n:<28}{sv[:110]}")
                print("  " + "-" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
