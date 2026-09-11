# -*- coding: utf-8 -*-
"""多头准入细化·时间切分验证（第十七轮 W8）。

W7 发现：现行门（up+chg≥3）在 hub 历史成交样本上 72h -3.07%，毒性来自
chg≥6 的 spike 追入（[6,10) -3.19% / ≥10 -5.05%），而 [3,6) 是 +1.26%。
本探针对候选条件做**时间切分**（样本中位 opened_at 前后）：
  - G0  现行门：up + chg24≥3
  - G0b 细化：up + chg24∈[3,6)
  - G0c 细化：up + chg24∈[3,10)
  - G4  up + chg∈[-2,6) + pos<60（动量背景回调低吸）
  - G2  up + 72h 动量背景 + 当前 chg<6
  - G6  chop + 72h 动量背景 + pos≥60
  - C2  现行 chop 分支：chop + pos≥60 + chg≥2
  - G8  chop 无条件（基线）
  - G7  up 无条件（基线）
报 72h 前向 / 实际 / 新出场反事实，前后段必须方向一致才可采纳。
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from deep_long_freshness import (  # noqa: E402
    load_longs, load_klines, pick, build_bar_features,
    sim_new_exit, cost_pct,
)

OUT = ROOT / "data" / "long_gate_refine_split.json"


def main() -> int:
    rows = load_longs()
    h1, d1 = load_klines({r["symbol"] for r in rows})
    feats = {}
    for sym in {r["symbol"] for r in rows}:
        s = pick(h1, sym)
        ds = pick(d1, sym)
        if s is None or ds is None or len(s) < 300 or len(ds) < 70:
            continue
        feats[sym] = (s, build_bar_features(s, ds))

    recs = []
    for r in rows:
        sym = r["symbol"]
        if sym not in feats:
            continue
        s, (reg_arr, pos_arr, chg_arr) = feats[sym]
        ts = int(r["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i < 25 or i >= len(s) - 2:
            continue
        entry = float(r["entry_price"] or 0)
        close = float(r["close_price"] or 0)
        if entry <= 0 or close <= 0:
            continue
        hold_h = (r["closed_at"] - r["opened_at"]).total_seconds() / 3600 if r["closed_at"] else 0
        actual = (close - entry) / entry * 100 - cost_pct(hold_h)
        k72 = min(i + 72, len(s) - 1)
        fwd72 = (s[k72][4] - entry) / entry * 100 - cost_pct(72)
        new_r = sim_new_exit(s, i, entry)
        bg72 = max(chg_arr[max(0, i - 72): i + 1], default=0.0)
        recs.append({
            "opened": str(r["opened_at"]),
            "regime": reg_arr[i], "chg": chg_arr[i], "pos": pos_arr[i], "bg72": round(bg72, 2),
            "actual": round(actual, 3), "fwd72": round(fwd72, 3),
            "new_net": round(new_r[0], 3) if new_r[0] is not None else None,
        })

    _sorted = sorted(r["opened"] for r in recs)
    med = _sorted[len(_sorted) // 2]
    print(f"样本 n={len(recs)}，中位 opened_at={med}")

    conds = {
        "G0 现行门 up+chg≥3": lambda x: x["regime"] == "up" and x["chg"] >= 3,
        "G0b up+chg∈[3,6)": lambda x: x["regime"] == "up" and 3 <= x["chg"] < 6,
        "G0c up+chg∈[3,10)": lambda x: x["regime"] == "up" and 3 <= x["chg"] < 10,
        "G4 up+chg∈[-2,6)+pos<60": lambda x: x["regime"] == "up" and -2 <= x["chg"] < 6 and x["pos"] < 60,
        "G2 up+72h动量背景+chg<6": lambda x: x["regime"] == "up" and x["bg72"] >= 3 and x["chg"] < 6,
        "G6 chop+72h动量背景+pos≥60": lambda x: x["regime"] == "chop" and x["bg72"] >= 2 and x["pos"] >= 60,
        "C2 现行chop分支 pos≥60+chg≥2": lambda x: x["regime"] == "chop" and x["pos"] >= 60 and x["chg"] >= 2,
        "G8 chop 无条件": lambda x: x["regime"] == "chop",
        "G7 up 无条件": lambda x: x["regime"] == "up",
        "G9 down 无条件": lambda x: x["regime"] == "down",
    }

    def cell(rows_):
        if not rows_:
            return None
        n = len(rows_)
        return {
            "n": n,
            "fwd72": round(sum(x["fwd72"] for x in rows_) / n, 2),
            "actual": round(sum(x["actual"] for x in rows_) / n, 2),
            "new": (round(sum(x["new_net"] for x in rows_) / n, 2)
                    if all(x["new_net"] is not None for x in rows_) else None),
        }

    print(f"\n{'条件':<30}{'前段n':>6}{'前段72h':>10}{'后段n':>6}{'后段72h':>10}"
          f"{'全样本72h':>10}{'新出场':>9}")
    out = {}
    for name, fn in conds.items():
        sub = [x for x in recs if fn(x)]
        a = cell([x for x in sub if x["opened"] < med])
        b = cell([x for x in sub if x["opened"] >= med])
        c = cell(sub)
        out[name] = {"early": a, "late": b, "all": c}
        if not c:
            print(f"{name:<30} 无样本")
            continue
        a_s = f"{a['fwd72']:+.2f}" if a else "-"
        b_s = f"{b['fwd72']:+.2f}" if b else "-"
        print(f"{name:<30}{(a['n'] if a else 0):>6}{a_s:>10}{(b['n'] if b else 0):>6}{b_s:>10}"
              f"{c['fwd72']:>+10.2f}{(f'{c['new']:+.2f}' if c['new'] is not None else '-'):>9}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "median_opened": med, "n": len(recs), "conditions": out,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
