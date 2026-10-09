"""h463 前提检查 ②（只读）：按 position_id 配对 entry/exit，重建持仓时长直方图。

原理：`max_one_side_seconds`（45s）是**无形态标记**状态的持仓上界，
P1 → `p1_hold_sec`(60s)，P45 → `p45_hold_sec`(300s)。
因此持仓时长直方图会在 **≈45 / ≈60 / ≈300** 三处出现尖峰，
尖峰质量就分别是三类腿的占比 ⇒ 直接判定 45→90 这个改动约束了多少腿。

用法：python scripts/h463_premise2.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h463_premise2.json"


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
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT position_id, symbol, ts, "
                "COALESCE(meta_json->>'exit_path','') AS ep, net_bp "
                "FROM lane_ledger WHERE lane_id=%s AND ts > now() - interval '30 hours' "
                "ORDER BY ts", (LANE,))
            rows = cur.fetchall()
    print(f"近 30h 腿数: {len(rows)}")
    pos: dict = {}
    for pid, sym, ts, ep, net_bp in rows:
        d = pos.setdefault((pid, sym), {"entry": None, "exit": None, "ep": "",
                                        "net": 0.0, "first_ts": ts})
        if ts < d["first_ts"]:
            d["first_ts"] = ts
        if ep == "":
            if d["entry"] is None or ts < d["entry"]:
                d["entry"] = ts
        else:
            if d["exit"] is None or ts < d["exit"]:
                d["exit"] = ts
                d["ep"] = ep
        d["net"] += float(net_bp or 0.0)
    pairs = [d for d in pos.values() if d["entry"] and d["exit"]]
    print(f"可配对往返（有 entry+exit）: {len(pairs)} / 分组 {len(pos)}")
    if not pairs:
        return 1

    buckets = [(0, 15), (15, 30), (30, 40), (40, 50), (50, 60), (60, 75), (75, 90),
               (90, 120), (120, 180), (180, 270), (270, 330), (330, 10**9)]
    hist = {b: {"n": 0, "net": 0.0} for b in buckets}
    for d in pairs:
        h = (d["exit"] - d["entry"]).total_seconds()
        for b in buckets:
            if b[0] <= h < b[1]:
                hist[b]["n"] += 1
                hist[b]["net"] += d["net"]
                break
    print("=" * 72)
    print(f"{'持仓秒数桶':>16s} {'腿数':>7s} {'占比':>7s} {'净额bp/腿':>11s}")
    tot = len(pairs)
    for b in buckets:
        d = hist[b]
        if d["n"] == 0:
            continue
        label = f"{b[0]}-{b[1]}" if b[1] < 10**8 else f">={b[0]}"
        print(f"{label:>16s} {d['n']:7d} {100.0*d['n']/tot:6.1f}% "
              f"{d['net']/d['n']:11.2f}")
    print("=" * 72)
    # 尖峰分析：45s(=max_one_side, 无标记) / 60s(P1) / 300s(P45)
    def band(lo, hi):
        sel = [d for d in pairs
               if lo <= (d["exit"] - d["entry"]).total_seconds() < hi]
        return len(sel), (sum(d["net"] for d in sel) / len(sel) if sel else 0.0)
    for name, lo, hi in (("≈45s 上限带 (43-48)", 43, 48),
                         ("≈60s 上限带 (58-63)", 58, 63),
                         ("≈300s 上限带 (295-310)", 295, 310),
                         ("<45s 自然出场", 0, 43)):
        n, net = band(lo, hi)
        print(f"{name:24s} n={n:5d} ({100.0*n/tot:5.1f}%)  净/腿={net:+.2f}bp")
    print("=" * 72)
    # 各 exit_path 的持仓时长分布（判断哪些路径是"上界触发"）
    by_ep: dict = {}
    for d in pairs:
        h = (d["exit"] - d["entry"]).total_seconds()
        e = by_ep.setdefault(d["ep"], {"n": 0, "hs": [], "net": 0.0})
        e["n"] += 1
        e["hs"].append(h)
        e["net"] += d["net"]
    print(f"{'exit_path':>26s} {'n':>6s} {'hold中位':>9s} {'p90':>8s} "
          f"{'净/腿bp':>9s} {'hold>=44s占比':>13s}")
    for ep, e in sorted(by_ep.items(), key=lambda kv: -kv[1]["n"]):
        hs = sorted(e["hs"])
        med = hs[len(hs) // 2]
        p90 = hs[min(len(hs) - 1, int(0.9 * (len(hs) - 1)))]
        ge44 = sum(1 for x in hs if x >= 44.0)
        print(f"{ep or '(空)':>26s} {e['n']:6d} {med:9.0f} {p90:8.0f} "
              f"{e['net']/e['n']:9.2f} {100.0*ge44/e['n']:12.1f}%")

    summary = {
        "legs_30h": len(rows), "pairs": tot,
        "hist": {f"{b[0]}-{b[1]}" if b[1] < 10**8 else f">={b[0]}":
                 {"n": d["n"], "net_per_leg_bp": round(d["net"] / d["n"], 3)}
                 for b, d in hist.items() if d["n"]},
        "band_43_48": band(43, 48), "band_58_63": band(58, 63),
        "band_295_310": band(295, 310), "band_lt43": band(0, 43),
        "by_exit_path": {k or "(空)": {"n": v["n"],
                                       "net_per_leg_bp": round(v["net"] / v["n"], 3),
                                       "ge44_share": round(
                                           sum(1 for x in v["hs"] if x >= 44.0) / v["n"], 3)}
                         for k, v in by_ep.items()},
    }
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=" * 72)
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
