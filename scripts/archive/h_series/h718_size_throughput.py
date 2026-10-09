# -*- coding: utf-8 -*-
"""[h718 阶段3b 2026-10-02] 规模-吞吐-净收益三维:哪个名义档贡献最大的日净 USD?

设计文档 §阶段3:①逐币曲面 ②队列份额第三维。本脚本把第三维从**已实现数据**
里量出来:每个名义档的 ①腿频(队列份额效应)②每腿净 bp(h717 已量)③日净 USD
贡献。三者的乘积顶点 = 规模的最优落点(而不是放大或缩小到极端)。

输出 data/size_throughput_last.json + 控制台。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
from pathlib import Path

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

    now = time.time()
    since = now - 3 * 86400
    days = 3.0
    with psycopg.connect(_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT notional, net_bp FROM lane_ledger"
            " WHERE lane_id=%s AND event='fill' AND ts >= to_timestamp(%s)"
            " AND COALESCE(meta_json->>'exit_path','') = ''", (LANE, since))
        rows = cur.fetchall()
    print(f"近 3 天入场腿 {len(rows)} 条")

    bands = [(0, 20, "s<20"), (20, 40, "s20-40"), (40, 80, "s40-80"),
             (80, 160, "s80-160"), (160, 1e9, "s>=160")]
    out = {"ts": now, "bands": []}
    print(f"  {'档':<10}{'腿数':>6}{'腿/日':>7}{'每腿bp':>8}{'每腿$':>8}{'日净$':>9}")
    for lo, hi, name in bands:
        sel = [(float(n), float(b)) for n, b in rows if lo <= float(n) < hi]
        if not sel:
            continue
        n = len(sel)
        per_day = n / days
        mean_bp = sum(b for _, b in sel) / n
        mean_notional = sum(nn for nn, _ in sel) / n
        day_usd = mean_bp * mean_notional * per_day / 1e4
        print(f"  {name:<10}{n:>6}{per_day:>7.0f}{mean_bp:>+8.2f}{mean_notional:>8.1f}{day_usd:>+9.3f}")
        out["bands"].append({"band": name, "n": n, "per_day": round(per_day, 1),
                             "mean_bp": round(mean_bp, 3),
                             "mean_notional": round(mean_notional, 1),
                             "day_usd": round(day_usd, 4)})
    # 最优档 = 日净 USD 最高且为正
    pos = [b for b in out["bands"] if b["day_usd"] > 0]
    if pos:
        best = max(out["bands"], key=lambda b: b["day_usd"])
        out["conclusion"] = {
            "best_band": best["band"], "best_day_usd": best["day_usd"],
            "verdict": f"规模最优落点 {best['band']}(日净 {best['day_usd']:+.3f}U)"
        }
        print(f"\n结论:日净 USD 最高的档 = {best['band']}({best['day_usd']:+.3f}U/日)")
    (ROOT / "data" / "size_throughput_last.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("✓ 已写 data/size_throughput_last.json")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
