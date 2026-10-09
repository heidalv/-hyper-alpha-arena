"""h588 — 反推采集器**实际订阅的符号集**（只读，R102）。

原件已删、其 `--symbols / --depth-symbols` 参数不可知 ✗ ⇒ 从**表里的近期写入**反推：
  · trades/book 用一张表的近 10 分钟 distinct symbol；
  · depth 用 depth 表的近 1 小时（深度更新频率低）；
  · 三者取并集与交集，并与"35 个"等历史说法对照 ✓。

用法：python scripts/h588_live_symbols.py
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


def main() -> int:
    mk = h.read_env_dsn().replace("/alpha_arena", "/alpha_market")
    sets = {}
    with psycopg.connect(mk) as c, c.cursor() as cur:
        for tag, tbl, win in (("trades", "asterdex_trades", "10 minutes"),
                              ("book", "asterdex_book_ticker", "10 minutes"),
                              ("depth", "asterdex_depth_snapshots", "60 minutes")):
            cur.execute(
                f"SELECT DISTINCT symbol FROM {tbl}"
                f" WHERE ingest_ts > now() - interval '{win}' ORDER BY 1")
            syms = [r[0] for r in cur.fetchall()]
            sets[tag] = set(syms)
            print(f"[{tag}] 近 {win} 有写入的符号 = {len(syms)}")
            print("   " + ", ".join(syms))
        print("\n" + "=" * 90)
        print("并集 / 交集")
        print("=" * 90)
        u = sets["trades"] | sets["book"] | sets["depth"]
        i = sets["trades"] & sets["book"] & sets["depth"]
        print(f"  并集 {len(u)}：{', '.join(sorted(u))}")
        print(f"  三流都有 {len(i)}：{', '.join(sorted(i))}")
        if sets["trades"] - sets["depth"]:
            print(f"  仅 trades/book 有、depth 无（{len(sets['trades'] - sets['depth'])}）："
                  f"{', '.join(sorted(sets['trades'] - sets['depth']))}")
        print("\n" + "=" * 90)
        print("写入频率（近 2 分钟，按流）——用于估算待命件的写入负载")
        print("=" * 90)
        for tag, tbl in (("trades", "asterdex_trades"), ("book", "asterdex_book_ticker"),
                         ("depth", "asterdex_depth_snapshots")):
            cur.execute(f"SELECT count(*) FROM {tbl}"
                        f" WHERE ingest_ts > now() - interval '2 minutes'")
            n = cur.fetchone()[0]
            print(f"  {tag:<8} {n:>8} 行 / 2 分钟  ≈ {n/120:>8.1f} 行/秒")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
