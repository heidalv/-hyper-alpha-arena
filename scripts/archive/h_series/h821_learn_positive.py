# -*- coding: utf-8 -*-
"""收集主动流往返样本，找出哪一种离场还能赚钱。

近端样本全部是「吃单进、时间到了再吃单出」，胜率 0。
本脚本把账本里的完整往返写成样本，并按离场方式、持仓时长、币种分组。
只有平均盈亏为正、而且样本不少于 8 趟的组，才记成可学习的正收益切片。
结果写到 data/flow_learn_samples.jsonl 和 data/flow_learn_last.json。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
HOURS = int(sys.argv[1]) if len(sys.argv) > 1 else 24
MIN_N = 8


def _dsn():
    spec = importlib.util.spec_from_file_location(
        "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.read_env_dsn()


def main() -> int:
    import psycopg

    sql = (
        "SELECT position_id, ts, COALESCE(meta_json->>'exit_path','maker'),"
        " net_bp, notional, symbol, COALESCE(meta_json->>'flatten','false')"
        " FROM lane_ledger WHERE lane_id=%s AND event='fill'"
        " AND ts > now() - make_interval(hours => %s)"
        " ORDER BY ts"
    )
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql, (LANE, HOURS))
        rows = cur.fetchall()

    trips = {}
    for pid, ts, path, nb, ntl, sym, flat in rows:
        bucket = trips.setdefault(str(pid or ""), {"sym": str(sym), "legs": []})
        bucket["legs"].append({
            "ts": float(ts.timestamp()),
            "path": str(path or "maker"),
            "nb": float(nb or 0.0),
            "ntl": float(ntl or 0.0),
            "flat": str(flat).lower() == "true",
        })

    samples = []
    for pid, trip in trips.items():
        legs = trip["legs"]
        if len(legs) < 2:
            continue
        entry = legs[0]
        exit_leg = legs[-1]
        if not exit_leg["flat"] and "taker" not in exit_leg["path"] and "maker" not in exit_leg["path"]:
            continue
        notional = max(entry["ntl"], 1e-9)
        net_bp = sum(leg["nb"] * leg["ntl"] for leg in legs) / notional
        hold = exit_leg["ts"] - entry["ts"]
        fee_legs = sum(1 for leg in legs if "taker" in leg["path"])
        samples.append({
            "ts": exit_leg["ts"],
            "position_id": pid,
            "symbol": trip["sym"],
            "entry_path": entry["path"],
            "exit_path": exit_leg["path"],
            "hold_sec": round(hold, 1),
            "entry_bp": round(entry["nb"], 2),
            "exit_bp": round(exit_leg["nb"], 2),
            "net_bp": round(net_bp, 2),
            "taker_legs": fee_legs,
            "win": net_bp > 0,
        })

    sample_path = ROOT / "data" / "flow_learn_samples.jsonl"
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    with sample_path.open("w", encoding="utf-8") as fh:
        for row in samples:
            row["era"] = "flow"
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    flow_only = [s for s in samples if s["entry_path"].startswith("flow_")
                 or s["exit_path"].startswith("flow_")
                 or "max_hold" in s["exit_path"] or "maker" in s["exit_path"]]
    orphan = [s for s in samples if "orphan" in s["exit_path"]]
    rest = [s for s in samples if s not in flow_only and s not in orphan]

    def _avg(xs):
        return sum(xs) / len(xs) if xs else 0.0

    def _pack(name, rows):
        if not rows:
            return {"name": name, "n": 0}
        nets = [r["net_bp"] for r in rows]
        return {
            "name": name, "n": len(rows),
            "win_rate": round(sum(1 for v in nets if v > 0) / len(nets), 3),
            "avg_net_bp": round(_avg(nets), 2),
            "avg_entry_bp": round(_avg([r["entry_bp"] for r in rows]), 2),
            "avg_exit_bp": round(_avg([r["exit_bp"] for r in rows]), 2),
            "avg_hold_sec": round(_avg([r["hold_sec"] for r in rows]), 1),
        }

    def _avg(xs):
        return sum(xs) / len(xs) if xs else 0.0

    cohorts = [
        _pack("全部", samples),
        _pack("主动流往返", flow_only),
        _pack("孤儿吃单强平", orphan),
        _pack("其余", rest),
        _pack("主动流且持仓不超过90秒", [s for s in flow_only if s["hold_sec"] <= 90]),
        _pack("主动流且没有吃单腿", [s for s in flow_only if s["taker_legs"] == 0]),
        _pack("主动流且两边都吃单", [s for s in flow_only if s["taker_legs"] >= 2]),
        _pack("持仓超过300秒后吃单离场", [
            s for s in samples if s["hold_sec"] > 300 and "taker" in s["exit_path"]]),
    ]
    groups = defaultdict(list)
    for row in samples:
        hold_bucket = "le90" if row["hold_sec"] <= 90 else (
            "le300" if row["hold_sec"] <= 300 else "gt300")
        taker_bucket = "all_maker" if row["taker_legs"] == 0 else (
            "one_taker" if row["taker_legs"] == 1 else "two_taker")
        key = (row["exit_path"], hold_bucket, taker_bucket)
        groups[key].append(row["net_bp"])
        groups[("symbol:" + row["symbol"], hold_bucket, taker_bucket)].append(row["net_bp"])

    slices = []
    for key, vals in groups.items():
        if len(vals) < MIN_N:
            continue
        mean = _avg(vals)
        wins = sum(1 for v in vals if v > 0) / len(vals)
        slices.append({
            "key": list(key),
            "n": len(vals),
            "mean_bp": round(mean, 2),
            "win_rate": round(wins, 3),
            "positive": mean > 0,
        })
    slices.sort(key=lambda s: s["mean_bp"], reverse=True)
    positive = [s for s in slices if s["positive"]]
    negative = [s for s in slices if not s["positive"]]

    lesson = {
        "ts": time.time(),
        "hours": HOURS,
        "n_roundtrips": len(samples),
        "win_rate": round(sum(1 for s in samples if s["win"]) / len(samples), 3) if samples else 0,
        "avg_net_bp": round(_avg([s["net_bp"] for s in samples]), 2) if samples else 0,
        "cohorts": cohorts,
        "positive_slices": positive[:12],
        "worst_slices": list(reversed(negative[-8:])),
        "rule": (
            "正收益只出现在挂单进、挂单出的少数往返里，样本还少，不能当成已经学会。"
            "持仓超过 300 秒后再吃单离场是亏损的主要来源，这种离场继续禁止。"
            "新样本继续写入 flow_learn_samples.jsonl，凑够 30 趟挂单往返再决定能不能放宽进场。"
        ),
    }
    (ROOT / "data" / "flow_learn_last.json").write_text(
        json.dumps(lesson, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"往返 {len(samples)} 趟  胜率 {lesson['win_rate']*100:.0f}%  "
          f"平均 {lesson['avg_net_bp']:+.2f}bp")
    for c in cohorts:
        if not c.get("n"):
            continue
        print(f"  {c['name']:<16} n={c['n']:>3} 净 {c['avg_net_bp']:+8.2f}bp "
              f"进 {c['avg_entry_bp']:+6.2f} 出 {c['avg_exit_bp']:+6.2f} "
              f"持 {c['avg_hold_sec']:.0f}s 胜率 {c['win_rate']*100:.0f}%")
    print(f"正收益切片 {len(positive)} 个，负收益切片 {len(negative)} 个")
    for s in (positive[:5] or negative[:5]):
        print(f"  {s['key']} n={s['n']} {s['mean_bp']:+.2f}bp 胜率 {s['win_rate']*100:.0f}%")
    print("已写 data/flow_learn_samples.jsonl 和 data/flow_learn_last.json")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
