"""h463 运行态采纳证明（只读）：用 ofi_flatten 出场年龄反推 `max_one_side_seconds`。

原理（runner.py:1364-1367）：
    ofi_flatten 触发条件是
        age > max_one_side_seconds × ofi_flatten_min_age_ratio(0.5)
    ⇒ **出场年龄的下界直接编码了 max_one_side_seconds**：
        若进程内是 90 ⇒ 年龄下界 ≈ 45s；
        若仍是 45（注册表改了但没被采用）⇒ 年龄下界 ≈ 22.5s。

这比"读注册表"强得多：注册表是**意图**，本脚本量的是**进程真实行为**。
（对照：h463 部署于 2026-09-28T18:20:35Z；02:32:15L 有一次重启，为保证读的是
 重启后、部署后的同一进程，默认只统计重启之后的窗口。）

配对口径：按 (position_id, symbol) 顺序状态机 —— 空 `exit_path` 的行为加仓/开仓腿，
首个非空 `exit_path` 的行为出场腿，hold = 出场ts − 首笔开仓ts。

用法：python scripts/h475_h463_runtime_proof.py [--since 2026-09-28T18:32:15+00:00]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import statistics as st
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402

from scripts.h422_weekly_scan import read_env_dsn  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h475_h463_runtime_proof.json"
RESTART_UTC = "2026-09-28T18:32:15+00:00"   # 02:32:15L


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=RESTART_UTC)
    a = ap.parse_args()
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            m = cur.fetchone()[0]
            p = dict(m.get("params") or {})
            print("registry: max_one_side_seconds =", p.get("max_one_side_seconds"),
                  "| ofi_flatten_min_age_ratio =",
                  p.get("ofi_flatten_min_age_ratio"),
                  "| ofi_flatten_threshold =", p.get("ofi_flatten_threshold"),
                  "| ofi_flatten_maker_only =", p.get("ofi_flatten_maker_only"))
            cur.execute(
                "SELECT ts, symbol, position_id, COALESCE(meta_json->>'exit_path','') "
                "FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz ORDER BY ts",
                (LANE, a.since))
            rows = cur.fetchall()
    print(f"窗口自 {a.since}：{len(rows)} 腿")
    seq: dict = {}
    trips = []
    for ts, sym, pid, ep in rows:
        d = seq.setdefault((pid, sym), {"open": None})
        if ep == "":
            if d["open"] is None:
                d["open"] = ts
        elif d["open"] is not None:
            trips.append({"path": ep, "sym": sym,
                          "hold": (ts - d["open"]).total_seconds()})
            d["open"] = None
    print(f"配平往返 {len(trips)} 次")
    if not trips:
        print("（窗口太短，尚无完整往返）")
        return 0
    by: dict = {}
    for t in trips:
        by.setdefault(t["path"], []).append(t["hold"])
    print("=" * 88)
    print(f"{'exit_path':>26s} {'n':>5s} {'min':>8s} {'p25':>8s} {'中位':>8s} {'max':>9s}")
    for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
        v = sorted(v)
        print(f"{k:>26s} {len(v):5d} {v[0]:8.0f} "
              f"{v[max(0, int(0.25*(len(v)-1)))]:8.0f} {st.median(v):8.0f} {v[-1]:9.0f}")
    ofi = [t["hold"] for t in trips if t["path"].startswith("ofi_flatten")]
    print("=" * 88)
    if len(ofi) >= 3:
        mn = min(ofi)
        print(f"ofi_flatten 出场 n={len(ofi)}  最小年龄={mn:.0f}s")
        if mn >= 42.0:
            verdict = (f"✓ 进程内 max_one_side_seconds ≈ 90（最小出场年龄 {mn:.0f}s ≥ 45s×容差；"
                       f"若仍是 45 则下界应 ≈22.5s）")
        elif mn >= 20.0:
            verdict = (f"✗ 进程内仍像 45（最小出场年龄 {mn:.0f}s ≈ 22.5s 下界）⇒ h463 未生效")
        else:
            verdict = f"？ 最小年龄 {mn:.0f}s，两档都不符，需人工核查"
        print("⇒", verdict)
    else:
        verdict = f"样本不足（ofi_flatten 出场 {len(ofi)} 笔 < 3），稍后再跑"
        print("⇒", verdict)
    OUT.write_text(json.dumps(
        {"since": a.since, "legs": len(rows), "trips": len(trips),
         "by_path": {k: {"n": len(v), "min": min(v), "median": st.median(v),
                         "max": max(v)} for k, v in by.items()},
         "ofi_min_age": min(ofi) if ofi else None, "verdict": verdict},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
