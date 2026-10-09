"""h590 — 确认待命件的测试**只写了临时表**，没碰生产表（只读，R105）。

理由：待命件默认写 `TEMP TABLE`，理论上对生产表零影响 ✓，但"理论上"不算验证 ✗。
本脚本核对三张生产表的**行数与最新时间戳**，并与"只读探针的观测"对照：
  · 时间戳应仍等于**原件**的写入节奏（连续、滞后 1–3s ✓）；
  · 不应出现"临时表那几轮测试"造成的异常增量或符号混入。

用法：python scripts/h590_verify_no_prod_writes.py
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

TABLES = (("asterdex_trades", "recv_ts_ns", 1e9),
          ("asterdex_book_ticker", "recv_ts_ns", 1e9),
          ("asterdex_depth_snapshots", "recv_ts_ns", 1e9))


def main() -> int:
    mk = h.read_env_dsn().replace("/alpha_arena", "/alpha_market")
    now_ns = dt.datetime.now().timestamp() * 1e9
    print("=" * 88)
    print("生产表核对（若待命件的测试写进了生产表，这里会露馅 ✗）")
    print("=" * 88)
    with psycopg.connect(mk) as c, c.cursor() as cur:
        for t, col, div in TABLES:
            cur.execute(f"SELECT count(*), max({col}) FROM {t}")
            n, mx = cur.fetchone()
            lag = (now_ns - float(mx)) / 1e9 if mx else None
            print(f"  {t:<28} 行数={n:>10}  最新 {col} 滞后 "
                  f"{'n/a' if lag is None else f'{lag:.0f}s'}")
            # 这些测试币只应出现在**原件**的订阅集里；深度表不该出现 LIT/XMR 之外的新花样
            cur.execute(
                f"SELECT DISTINCT symbol FROM {t} WHERE {col} > %s LIMIT 60",
                (now_ns - 60e9,))
            syms = sorted({r[0] for r in cur.fetchall()})
            print(f"      近 60s 出现的符号（{len(syms)}）：{', '.join(syms[:14])}"
                  + (" …" if len(syms) > 14 else ""))
    print("=" * 88)
    print("判读：时间戳滞后 1–3s 且符号集 = 原件的订阅集 ⇒ 生产表仍只由原件写入 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
