# -*- coding: utf-8 -*-
"""多头准入条件细化探针（第十七轮 W7）：动量背景 vs 入场尖峰。

深度二发现：入场 bar 满足 chg24≥3%（fresh）的历史成交 72h 前向 **-2.04%**，
而「条件 >72h 前满足、现在才入场」（stale）却 +1.51%——hub 的 fresh 入场
可能是买在 spike 之后（chg24 过大 + 入场溢价）。本探针回答：

  Q1: fresh 桶按 chg24 大小切分（3-6 / 6-10 / ≥10%），是否 spike 越大越差？
  Q2: fresh 桶的入场溢价（entry_price vs 入场 bar close）有多大？
  Q3: 备选准入条件在**历史成交样本**上的表现（部署相关性口径）：
      G0=现行门(up+chg24≥3)  G1=up+chg24∈[3,10)  G2=up+72h动量背景+当前chg<6
      G3=up+动量背景+pos<60(回调低吸)  G4=up+chg∈[-2,6)+pos<60
      G5=chop+pos≥60+chg∈[2,10)  G6=chop+动量背景+pos≥60
  每个条件报：n / 实际净 / 72h 前向 / 新出场反事实。
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 复用深度二的加载与特征管线
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from deep_long_freshness import (  # noqa: E402
    load_longs, load_klines, pick, build_bar_features, learned_ok,
    sim_new_exit, cost_pct,
)

OUT = ROOT / "data" / "long_gate_refine.json"


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
        # 72h 动量背景：过去 72 根的最大 chg24（近似"最近建立过动量"）
        bg72 = max(chg_arr[max(0, i - 72): i + 1], default=0.0)
        # 入场溢价：entry vs 入场 bar close
        premium = (entry - s[i][4]) / s[i][4] * 100 if s[i][4] > 0 else 0.0
        recs.append({
            "symbol": sym, "regime": reg_arr[i], "chg": chg_arr[i], "pos": pos_arr[i],
            "bg72": round(bg72, 2), "premium": round(premium, 3),
            "actual": round(actual, 3), "fwd72": round(fwd72, 3),
            "new_net": round(new_r[0], 3) if new_r[0] is not None else None,
            "fresh": learned_ok(reg_arr[i], pos_arr[i], chg_arr[i]),
        })

    def agg(rows_, label):
        if not rows_:
            print(f"  {label:<34} 无样本")
            return
        n = len(rows_)
        a = sum(x["actual"] for x in rows_) / n
        f72 = sum(x["fwd72"] for x in rows_) / n
        nn = [x["new_net"] for x in rows_ if x["new_net"] is not None]
        nn_s = f"{sum(nn)/len(nn):+.2f}" if nn else "-"
        print(f"  {label:<34} n={n:>4} 实际={a:>+8.2f}% 72h前向={f72:>+8.2f}% 新出场={nn_s}%")

    print("=== Q1/Q2: fresh（入场满足现行门）按 chg24 大小与入场溢价 ===")
    fresh = [x for x in recs if x["fresh"]]
    print(f"  fresh 桶入场溢价均值 = {sum(x['premium'] for x in fresh)/len(fresh):+.3f}%（正=买在 bar close 上方）")
    for lo, hi, lab in [(3, 6, "chg∈[3,6)"), (6, 10, "chg∈[6,10)"), (10, 1e9, "chg≥10")]:
        agg([x for x in fresh if lo <= x["chg"] < hi], f"fresh+{lab}")
    print(f"  非 fresh 溢价均值 = {sum(x['premium'] for x in recs if not x['fresh'])/max(1,sum(1 for x in recs if not x['fresh'])):+.3f}%")

    print("\n=== Q3: 备选准入条件（历史成交样本，全部 regime 混合后按条件筛选）===")
    conds = {
        "G0 现行门 up+chg≥3": lambda x: x["regime"] == "up" and x["chg"] >= 3,
        "G1 up+chg∈[3,10)": lambda x: x["regime"] == "up" and 3 <= x["chg"] < 10,
        "G2 up+72h动量背景+当前chg<6": lambda x: x["regime"] == "up" and x["bg72"] >= 3 and x["chg"] < 6,
        "G3 up+动量背景+pos<60": lambda x: x["regime"] == "up" and x["bg72"] >= 3 and x["pos"] < 60,
        "G4 up+chg∈[-2,6)+pos<60": lambda x: x["regime"] == "up" and -2 <= x["chg"] < 6 and x["pos"] < 60,
        "G5 chop+pos≥60+chg∈[2,10)": lambda x: x["regime"] == "chop" and x["pos"] >= 60 and 2 <= x["chg"] < 10,
        "G6 chop+动量背景+pos≥60": lambda x: x["regime"] == "chop" and x["bg72"] >= 2 and x["pos"] >= 60,
        "G7 up 无条件": lambda x: x["regime"] == "up",
        "G8 chop 无条件": lambda x: x["regime"] == "chop",
        "G9 down 无条件（应拦）": lambda x: x["regime"] == "down",
    }
    for name, fn in conds.items():
        agg([x for x in recs if fn(x)], name)

    # 说明：时间切分已在 deep_long_freshness（深度二）以新鲜度分层覆盖
    # （fresh/recent/stale/never 本身就是时间结构），此处不再重复。

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "n": len(recs),
        "fresh_premium_mean": (sum(x["premium"] for x in fresh) / len(fresh) if fresh else None),
        "nonfresh_premium_mean": (
            sum(x["premium"] for x in recs if not x["fresh"])
            / max(1, sum(1 for x in recs if not x["fresh"]))),
        "recs": recs,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
