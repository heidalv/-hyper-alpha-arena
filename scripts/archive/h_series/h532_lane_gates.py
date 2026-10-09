"""h532：**当前 tick 的拦截原因**——停摆 4.2h 的直接原因是什么？

只读 `logs/mm_lane_status.json`（worker 每 tick 自己写的生效状态），不做任何 SQL，
因此不会因表名/权限问题失败。输出顺序按"最可能直接封死全车道"到"只封单币单侧"：
  1. `lane_pause_*`（车道级暂停：波动/日亏/毒性 ⇒ 一停全停）；
  2. `vol_pause` / `vol_regime`（逐币波动闸，依赖 `vol_baseline_bp`）；
  3. 趋势闸（`trend_only_flat` / `trend_up` / `trend_down`）；
  4. 逐币 sigma 与基准（判断基准是否陈旧到把 sigma 顶穿阈值）。

用法：python scripts/h532_lane_gates.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATUS = ROOT / "logs" / "mm_lane_status.json"
OUT = ROOT / "research_l1" / "out" / "h532_lane_gates.json"

SINGLE = ["lane_pause_counts", "lane_pause_last", "reason", "day_pnl_usd",
          "day_pnl_limit_usd", "avg_sigma", "avg_sigma_all", "frozen_share",
          "quote_modes", "side_counts", "sigma_decisions", "avg_width_bp",
          "avg_base_bp", "fills", "flattens", "fills_per_hour", "ticks",
          "quoted_decisions", "gap_repair", "equity", "last_tick_ts"]


def main() -> int:
    d = json.loads(STATUS.read_text(encoding="utf-8"))
    import datetime as dt
    age = dt.datetime.now().timestamp() - STATUS.stat().st_mtime
    print(f"状态文件年龄 {age:.1f}s   ok={d.get('ok')}   ticks={d.get('ticks')}")
    print("=" * 78)
    for k in SINGLE:
        if k in d:
            v = d[k]
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            print(f"  {k:24s} = {str(v)[:180]}")
    print("\nskip_counts（拦截构成，降序）")
    print("=" * 78)
    sc = d.get("skip_counts") or {}
    tot = sum(v for v in sc.values() if isinstance(v, (int, float))) or 1
    for k, v in sorted(sc.items(), key=lambda kv: -(kv[1] if isinstance(kv[1], (int, float)) else 0)):
        print(f"  {k:36s} {str(v):>8s}  ({100.0*v/tot:5.1f}%)")
    print(f"  {'【合计】':36s} {tot:8d}")
    print("\n逐币状态（qty / sigma / 基准 / 最后 skip）")
    print("=" * 78)
    st = d.get("states") or {}
    vb = ((d.get("limits") or {}).get("vol_baseline_bp")
          or d.get("vol_baseline_bp") or {})
    print(f"  登记表 vol_baseline_bp = {json.dumps(vb, ensure_ascii=False)[:200]}")
    for s, v in sorted(st.items()):
        if not isinstance(v, dict):
            continue
        keys = ("qty", "sigma", "sigma_norm", "vol_baseline_bp", "skip", "last_skip",
                "toxic_streak", "last_stop_ts")
        row = {k: v.get(k) for k in keys if k in v}
        print(f"  {s:>6s} {json.dumps(row, ensure_ascii=False, default=str)[:190]}")
    print("\n其余键：" + "、".join(sorted(set(d) - set(SINGLE) - {"skip_counts", "states"})))
    OUT.write_text(json.dumps(d, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
