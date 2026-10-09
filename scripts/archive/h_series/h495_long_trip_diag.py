"""h495：诊断 h494 里那些 >320s 的"往返"到底是什么（逐腿明细）。

背景：`h494` 用带符号 qty 累计归零重建往返，已把旧口径的 27 个异常往返
（max 11082s）压到 17 个 >320s；但合规硬顶是 300s ⇒ 仍应≈0。
本脚本把最长的那几个往返**逐腿打出来**，判断是：
  · 真·长期持仓（引擎没按 300s 硬顶执行）——那是一个**风控缺陷**；
  · 还是累计未归零导致的**跨仓粘连**（部分减仓/漏行）——那是口径问题。
判据：看 flatten 行处的 `|cum| / peak|cum|`（正常收口应 ≈0）。

用法：python scripts/h495_long_trip_diag.py [--hours 6] [--top 5]
"""
from __future__ import annotations

import argparse
import collections
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


def read_env_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--top", type=int, default=4)
    a = ap.parse_args()
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT ts, symbol, COALESCE(meta_json->>'side',''), "
                "COALESCE((meta_json->>'qty')::float8,0)::float8, "
                "COALESCE(meta_json->>'exit_path',''), net_bp::float8, "
                "COALESCE((meta_json->>'fill_px')::float8,0)::float8 "
                "FROM lane_ledger WHERE lane_id=%s "
                "AND ts > now() - make_interval(hours => %s::int) "
                "ORDER BY symbol, ts", (LANE, int(a.hours)))
            rows = cur.fetchall()
    by_sym = collections.OrderedDict()
    for r in rows:
        by_sym.setdefault(r[1], []).append(r)
    trips = []
    for sym, rs in by_sym.items():
        cum = 0.0
        cur_t = None
        peak = 0.0
        for ts, _s, side, qty, ep, net, px in rs:
            signed = abs(qty) if str(side).lower() == "buy" else -abs(qty)
            if cur_t is None:
                cur_t = {"sym": sym, "open": ts, "legs": [], "peak": 0.0}
                peak = 0.0
            cum += signed
            peak = max(peak, abs(cum))
            cur_t["legs"].append({"ts": ts, "side": side, "qty": abs(qty), "ep": ep,
                                  "net": net, "px": px, "cum": cum})
            if ep != "" and abs(cum) < 1e-3:
                cur_t["hold"] = (ts - cur_t["open"]).total_seconds()
                cur_t["peak"] = peak
                trips.append(cur_t)
                cur_t = None
        if cur_t is not None:
            cur_t["hold"] = (rs[-1][0] - cur_t["open"]).total_seconds()
            cur_t["peak"] = peak
            trips.append(cur_t)
    longs = sorted([t for t in trips if t["hold"] > 320],
                   key=lambda t: -t["hold"])[:a.top]
    print(f"近 {a.hours:.0f}h：{len(trips)} 个往返，其中 >320s 的 {len([t for t in trips if t['hold']>320])} 个")
    for t in longs:
        print("=" * 92)
        print(f"{t['sym']} 持仓 {t['hold']:.0f}s  腿数 {len(t['legs'])}  peak|cum|={t['peak']:.4f}")
        for lg in t["legs"]:
            print(f"   {lg['ts']:%H:%M:%S} {lg['side']:4s} qty={lg['qty']:12.4f} "
                  f"px={lg['px']:10.5f} cum={lg['cum']:12.4f} "
                  f"{'EXIT(' + lg['ep'] + ')' if lg['ep'] else 'entry/reduce'} "
                  f"net={lg['net']:+7.2f}bp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
