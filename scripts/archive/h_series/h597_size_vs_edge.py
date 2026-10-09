"""h597 — "仓位越大越亏"？按**名义分位**看净 bp/腿（只读，R141）。

动机：R141 复核发现**名义加权口径比简单平均更负**（多数日期如此 ✗），暗示
"大仓位的腿更亏" ✗。若成立 ⇒ 规模逻辑（④ h527 缩规模）不只是"等比压小尾部"，
而是**直接削掉最亏的那一段** ✓✓。

做法：把纪元内的腿按 `notional` 分成 5 档（quintile），各档给出：
  · 腿数、名义区间、净 bp/腿（简单与加权）、止损腿占比、taker 占比；
  · 并做**币内分层**（去掉"大仓位恰好集中在某币"这一混淆 ✗ —— 与 R71 的教训一致 ✓）。

用法：python scripts/h597_size_vs_edge.py [--since ISO]
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import argparse
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

ERA_SINCE = "2026-09-28T05:00:00+00:00"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=ERA_SINCE)
    ap.add_argument("--until", default="",
                    help="窗口终点（ISO，留空=现在）；用于**体制对比**（45s 窗 vs 90s 窗）")
    a = ap.parse_args()
    until_sql = "AND ts <= %s::timestamptz" if a.until else ""
    params_tail = ([a.until] if a.until else [])
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        cur.execute(
            "WITH q AS (SELECT ntile(5) OVER (ORDER BY notional) AS b, net_bp, notional,"
            "  meta_json->>'exit_path' AS ep, symbol"
            f"  FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz {until_sql})"
            " SELECT b, count(*), min(notional)::float8, max(notional)::float8,"
            "  avg(net_bp)::float8,"
            "  (sum(net_bp*notional)/NULLIF(sum(notional),0))::float8,"
            "  count(*) FILTER (WHERE ep LIKE 'stop_loss%%') AS stops,"
            "  count(*) FILTER (WHERE ep LIKE '%%taker') AS taker,"
            "  count(DISTINCT symbol) AS nsym"
            " FROM q GROUP BY b ORDER BY b",
            (h.LANE, a.since, *params_tail))
        rows = cur.fetchall()
        print("=" * 104)
        print(f"按名义分位看净额（窗口 {a.since} → {a.until or '现在'}）——「大仓位是否更亏」")
        print("=" * 104)
        print(f"  {'档':<4}{'腿':>6}{'名义区间':>20}{'bp/腿(简单)':>13}"
              f"{'bp/腿(加权)':>13}{'止损率':>9}{'taker率':>9}{'币数':>6}")
        for b, n, lo, hi, m, mw, stops, taker, nsym in rows:
            print(f"  {b:<4}{n:>6}{f'{lo:.0f}–{hi:.0f}':>20}{m:>+13.2f}{mw:>+13.2f}"
                  f"{stops/n:>9.1%}{taker/n:>9.1%}{nsym:>6}")
        print("-" * 104)
        # 币内分层：每币内再分两档（大/小），看差值方向是否一致
        cur.execute(
            "WITH q AS (SELECT symbol, net_bp, notional,"
            "  ntile(2) OVER (PARTITION BY symbol ORDER BY notional) AS half"
            f"  FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz {until_sql})"
            " SELECT symbol, half, count(*), avg(net_bp)::float8,"
            "  (sum(net_bp*notional)/NULLIF(sum(notional),0))::float8"
            " FROM q GROUP BY 1,2 ORDER BY 1,2",
            (h.LANE, a.since, *params_tail))
        per = {}
        for sym, half, n, m, mw in cur.fetchall():
            per.setdefault(sym, {})[int(half)] = (n, m, mw)
        print("  币内分层（每币按名义中位分两半；'小半 − 大半' > 0 表示小仓位更不亏 ✓）：")
        print(f"    {'币':<8}{'小半 bp/腿':>13}{'大半 bp/腿':>13}{'差(小−大)':>13}")
        diffs = []
        for sym, d in sorted(per.items()):
            if 1 in d and 2 in d:
                small, big = d[1][1], d[2][1]
                diffs.append(small - big)
                print(f"    {sym:<8}{small:>+13.2f}{big:>+13.2f}{small-big:>+13.2f}")
        if diffs:
            pos = sum(1 for x in diffs if x > 0)
            print(f"\n  ⇒ {pos}/{len(diffs)} 个币的'小仓位更不亏' ⇒ "
                  f"{'方向一致，**规模与边际负相关成立** ✓' if pos == len(diffs) else '方向不一致 ✗（需谨慎）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
