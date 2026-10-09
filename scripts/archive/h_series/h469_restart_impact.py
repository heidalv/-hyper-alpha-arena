"""重启影响诊断（只读）：09-28 21:17:12L(13:17:12Z) worker 重启前后 6h 对照。

背景：h468 显示入场侧在 13:00Z 崩塌（入场 209→52/h、每腿名义 241$→104$、
每往返加仓次数 ≈13→≈4），出场侧基本不变；而心跳检测到 worker 恰在
**13:17:12Z 重启**（h442 部署 13:17:58Z 前 46 秒）。

重启与热采用的差别（代码核查）：
  · 参数：两者都经 `apply_env_param_overrides`（7 键白名单，**不含** max_one_side/stop_loss）
    ⇒ 参数口径不应变化；
  · 但重启会 `reload_states` 从 `lane_runtime_state` 恢复 SymbolState
    （qty / opened_ts / avg_px / mid_hist / spread_baseline / stop_since …）
    ⇒ 若恢复值失真，会立刻表现为"加仓被封锁 / 仓位异常"。

本脚本按 symbol 对照重启前 6h 与重启后 6h：
  入场腿数、入场名义分位、每往返加仓腿数、往返时长中位、出场路径构成。

用法：python scripts/h469_restart_impact.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import statistics as st
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h469_restart_impact.json"
RESTART = dt.datetime(2026, 9, 28, 13, 17, 12, tzinfo=dt.timezone.utc)
WIN = dt.timedelta(hours=6)


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


def load(dsn, t0, t1):
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT ts, symbol, position_id, "
                "COALESCE(meta_json->>'exit_path',''), notional::float8, "
                "COALESCE(meta_json->>'flatten',''), net_bp::float8, "
                "COALESCE(meta_json->>'side',''), COALESCE(meta_json->>'qty','') "
                "FROM lane_ledger WHERE lane_id=%s AND ts > %s AND ts <= %s ORDER BY ts",
                (LANE, t0, t1))
            return cur.fetchall()


def report(title, rows):
    ent = [r for r in rows if r[3] == ""]
    exi = [r for r in rows if r[3] != ""]
    print(f"── {title} ── 腿={len(rows)} 入场={len(ent)} 出场={len(exi)}")
    if not ent:
        return {}
    noti = sorted(r[4] for r in ent)
    def q(p):
        return noti[min(len(noti) - 1, int(p * (len(noti) - 1)))]
    per_sym: dict = {}
    for r in ent:
        per_sym.setdefault(r[1], []).append(r[4])
    print(f"   入场名义 p10/p50/p90 = {q(.1):.0f}/{q(.5):.0f}/{q(.9):.0f} $"
          f"   均值 {sum(noti)/len(noti):.0f}")
    for s, v in sorted(per_sym.items(), key=lambda kv: -len(kv[1])):
        v = sorted(v)
        print(f"     {s:5s} n={len(v):4d} p50={v[len(v)//2]:8.1f} "
              f"均值={sum(v)/len(v):8.1f}")
    # 每往返加仓腿数 + 往返时长（顺序状态机，按 position_id）
    seq: dict = {}
    trips = []
    for ts, sym, pid, ep, noti_, flat, net, side, qty in rows:
        d = seq.setdefault((pid, sym), {"open": None, "adds": 0})
        if ep == "":
            if d["open"] is None:
                d["open"] = ts
                d["adds"] = 1
            else:
                d["adds"] += 1
        else:
            if d["open"] is not None:
                trips.append({"hold": (ts - d["open"]).total_seconds(),
                              "adds": d["adds"]})
                d["open"] = None
                d["adds"] = 0
    if trips:
        adds = [t["adds"] for t in trips]
        holds = sorted(t["hold"] for t in trips)
        print(f"   往返 {len(trips)} 次  加仓腿/往返 中位={st.median(adds):.1f} "
              f"均值={sum(adds)/len(adds):.2f} max={max(adds)}")
        print(f"   往返时长 p50/p90 = {holds[len(holds)//2]:.0f}/"
              f"{holds[min(len(holds)-1, int(.9*(len(holds)-1)))]:.0f} s")
    paths: dict = {}
    for r in exi:
        paths[r[3]] = paths.get(r[3], 0) + 1
    print("   出场路径:", dict(sorted(paths.items(), key=lambda kv: -kv[1])))
    return {"legs": len(rows), "entries": len(ent), "exits": len(exi),
            "entry_notional_p50": q(.5), "entry_notional_mean": sum(noti) / len(noti),
            "trips": len(trips),
            "adds_per_trip_median": st.median([t["adds"] for t in trips]) if trips else None,
            "hold_p50": (sorted(t["hold"] for t in trips)[len(trips) // 2]
                         if trips else None),
            "paths": paths}


def main() -> int:
    dsn = read_env_dsn()
    out = {}
    out["pre"] = report(f"重启前 6h（{RESTART - WIN:%m-%d %H:%M}Z → {RESTART:%H:%M}Z）",
                        load(dsn, RESTART - WIN, RESTART))
    out["post"] = report(f"重启后 6h（{RESTART:%m-%d %H:%M}Z → {RESTART + WIN:%H:%M}Z）",
                         load(dsn, RESTART, RESTART + WIN))
    # 归一化到每小时
    a, b = out["pre"], out["post"]
    if a.get("legs") and b.get("legs"):
        print("═" * 78)
        print(f"{'指标':26s} {'重启前/h':>12s} {'重启后/h':>12s} {'倍数':>8s}")
        for k, lbl in (("entries", "入场腿"), ("exits", "出场腿"), ("trips", "往返")):
            x, y = a[k] / 6.0, b[k] / 6.0
            print(f"{lbl:26s} {x:12.1f} {y:12.1f} {y/max(x,1e-9):8.2f}×")
        for k, lbl in (("entry_notional_p50", "入场名义中位($)"),
                       ("adds_per_trip_median", "加仓腿/往返"),
                       ("hold_p50", "往返时长中位(s)")):
            x, y = a[k] or 0, b[k] or 0
            print(f"{lbl:26s} {x:12.2f} {y:12.2f} {y/max(x,1e-9):8.2f}×")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
