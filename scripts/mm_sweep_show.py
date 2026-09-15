"""读取 `mm_clean_sweep.py` 的 JSON 输出并排版（避免在 shell 里写复杂引号）。

用法：
    python scripts/mm_sweep_show.py logs/mm_clean_sweep_gates.json [--top 20] [--filter k_vol]
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--top", type=int, default=0, help="只显示前 N 行（0=全部）")
    ap.add_argument("--filter", default="", help="只显示 diff 里含该子串的候选")
    args = ap.parse_args()

    for path in args.paths:
        if not os.path.exists(path):
            print(f"✗ 不存在: {path}")
            continue
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        delays = d.get("delays_ms") or []
        print("=" * 118)
        print(f"{path}  起点 {d.get('since_txt')}  标的 {d.get('symbols')}  "
              f"成交 {sum((d.get('n_trades') or {}).values())}  跨度 {(d.get('span_h') or 0):.2f}h")
        print(f"口径 {[f'{x/1000:.1f}s' for x in delays]}   基准(在位/覆盖) "
              + " ".join(f"{k}={v}" for k, v in sorted((d.get("incumbent") or {}).items())
                         if k in ("w_base_bp", "min_width_reduce_bp", "k_inv", "k_vol",
                                  "max_net_directional_ratio", "_leg_ratio", "compound_ratio")))
        hdr = f"{'候选':<40}"
        for x in delays:
            hdr += f"{'bp/' + f'{x/1000:.1f}s':>12}{'$-' + f'{x/1000:.1f}s':>10}"
        hdr += f"{'被动腿bp':>10}{'平仓腿bp':>10}{'名义$':>9}{'正币':>5}  判定"
        print(hdr)
        rows = d.get("rows") or []
        if args.filter:
            rows = [r for r in rows if args.filter in json.dumps(r.get("diff"), ensure_ascii=False)]
        if args.top:
            rows = rows[: args.top]
        for r in rows:
            tag = ",".join(f"{k}={v}" for k, v in (r.get("diff") or {}).items()) or "(基准)"
            line = f"{tag:<40}"
            for e in r.get("per") or []:
                line += f"{float(e.get('net_bp') or 0):>+12.3f}{float(e.get('net_usd') or 0):>+10.2f}"
            per = r.get("per") or [{}]
            mk = [float(e["maker_bp"]) for e in per if e.get("maker_bp") is not None]
            fl = [float(e["flatten_bp"]) for e in per if e.get("flatten_bp") is not None]
            ntl = [float(e.get("notional") or 0) for e in per]
            ps = [int(e.get("pos_sym") or 0) for e in per]
            line += (f"{(sum(mk)/len(mk) if mk else 0):>+10.3f}"
                     f"{(sum(fl)/len(fl) if fl else 0):>+10.3f}"
                     f"{(sum(ntl)/len(ntl) if ntl else 0):>9.0f}"
                     f"{(max(ps) if ps else 0):>5}  {r.get('verdict')}")
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
