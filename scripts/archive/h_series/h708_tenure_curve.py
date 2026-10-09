# -*- coding: utf-8 -*-
"""[h708 2026-10-02] 币龄衰减曲线:每腿净 bp 随"币入宇宙时长"的变化。

用户观察:"每次大规模换币后立即收益加速,之后衰减,后期亏"。
方法:从 lane_registry.ops_changes 重建每币的**在槽窗口**(entry/exit 时刻),
把每条开仓腿按 `腿时刻 − 币入槽时刻` 分桶,看每桶的每腿净 bp。
若曲线单调衰减且存在零交叉 ⇒ 最优轮换点 = 交叉时刻。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LANE = "mm_asterdex"


def _dsn() -> str:
    _spec = importlib.util.spec_from_file_location("h425", ROOT / "scripts" / "h425_repair_trial.py")
    _h = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_h)
    return _h.read_env_dsn()


def main() -> int:
    import psycopg
    from datetime import datetime, timezone

    from backend.services import lane_registry as reg

    meta = (reg.get_lane(LANE) or {}).get("meta") or {}
    ops = [o for o in (meta.get("ops_changes") or [])
           if o.get("op") == "set_symbols" and o.get("after") and o.get("ts")]
    ops.sort(key=lambda o: o["ts"])
    print(f"set_symbols 事件 {len(ops)} 条")

    # 每币在槽窗口列表 [(entry_ts, exit_ts|now)]
    def _p(t: str) -> float:
        try:
            return datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

    now = time.time()
    tenure_windows: Dict[str, List[tuple]] = {}
    active: Dict[str, float] = {}
    for o in ops:
        t = _p(o["ts"])
        after = {str(s).upper() for s in (o.get("after") or [])}
        for s in after:
            if s not in active:
                active[s] = t        # 入槽
        for s in list(active):
            if s not in after:
                tenure_windows.setdefault(s, []).append((active.pop(s), t))
    for s, t0 in active.items():      # 仍在槽的,窗口到 now
        tenure_windows.setdefault(s, []).append((t0, now))

    total_wins = sum(len(v) for v in tenure_windows.values())
    print(f"币在槽窗口总数: {total_wins}")

    with psycopg.connect(_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT symbol, extract(epoch from ts)::double precision, net_bp, notional"
            " FROM lane_ledger WHERE lane_id=%s AND event='fill'"
            " AND ts >= '2026-10-01 00:00+08'"
            " AND COALESCE(meta_json->>'exit_path','') = ''", (LANE,))
        legs = [(str(r[0]).upper(), float(r[1]), float(r[2]), float(r[3]))
                for r in cur.fetchall()]
    print(f"开仓腿 {len(legs)} 条(10-01 起)")

    # 腿 → 币龄(分钟)
    rows: List[dict] = []
    for sym, ts, netbp, notional in legs:
        wins = tenure_windows.get(sym)
        if not wins:
            continue
        ten_min = None
        for t0, t1 in wins:
            if t0 - 5 <= ts <= t1 + 5:
                ten_min = (ts - t0) / 60.0
                break
        if ten_min is None:
            continue
        rows.append({"sym": sym, "ten": ten_min, "net": netbp, "notional": notional})
    print(f"可归入币龄的腿 {len(rows)} 条\n")

    def bucket(label: str, lo: float, hi: float) -> None:
        sel = [r for r in rows if lo <= r["ten"] < hi]
        if len(sel) < 15:
            return
        n = len(sel)
        net_bp = sum(r["net"] for r in sel) / n
        net_u = sum(r["net"] * r["notional"] / 1e4 for r in sel)
        print(f"  币龄 {label:<12} n={n:>4}  每腿净 {net_bp:+.2f}bp  净 {net_u:+.3f}U")

    print("== 币龄 → 每腿净(10-01 以来全量)==  ")
    for label, lo, hi in (("0-15min", 0, 15), ("15-30min", 15, 30),
                          ("30-60min", 30, 60), ("1-2h", 60, 120),
                          ("2-4h", 120, 240), ("4-8h", 240, 480),
                          ("8-16h", 480, 960), (">16h", 960, 1e9)):
        bucket(label, lo, hi)

    # 分时段对照:10-01(低频换币)vs 10-02(高频换币)
    d1 = datetime.fromisoformat("2026-10-01T00:00:00+08:00").timestamp()
    d2 = datetime.fromisoformat("2026-10-02T00:00:00+08:00").timestamp()
    with psycopg.connect(_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT symbol, extract(epoch from ts)::double precision, net_bp, notional"
            " FROM lane_ledger WHERE lane_id=%s AND event='fill'"
            " AND ts >= '2026-10-01 00:00+08'"
            " AND COALESCE(meta_json->>'exit_path','') = ''", (LANE,))
        legs2 = cur.fetchall()
    for label, lo, hi in (("10-01", d1, d2), ("10-02", d2, now)):
        sel = []
        for sym, ts, netbp, notional in legs2:
            if not (lo <= ts < hi):
                continue
            wins = tenure_windows.get(str(sym).upper())
            if not wins:
                continue
            for t0, t1 in wins:
                if t0 - 5 <= ts <= t1 + 5:
                    sel.append(((ts - t0) / 60.0, float(netbp), float(notional)))
                    break
        if len(sel) < 15:
            continue
        print(f"\n== {label}({len(sel)} 腿)== ")
        for bl, b_lo, b_hi in (("0-30min", 0, 30), ("30-120min", 30, 120),
                               ("2-6h", 120, 360), (">6h", 360, 1e9)):
            s2 = [x for x in sel if b_lo <= x[0] < b_hi]
            if len(s2) < 10:
                continue
            n = len(s2)
            net_bp = sum(x[1] for x in s2) / n
            net_u = sum(x[1] * x[2] / 1e4 for x in s2)
            print(f"  币龄 {bl:<10} n={n:>4}  每腿净 {net_bp:+.2f}bp  净 {net_u:+.3f}U")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
