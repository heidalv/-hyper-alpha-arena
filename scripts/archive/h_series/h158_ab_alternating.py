# -*- coding: utf-8 -*-
"""[H158 2026-09-21] 交替区块 A/B：spread_mult 0.2 vs 0.5（降噪版）。

# 为什么要重做

两次单窗口扫描给出**不一致**的排序：

    第一次（11:34~12:11）  0.5 > 1.3 > 1.8   净 +0.377 / −0.245 / −0.552 bp
    第二次（13:17~13:38）  0.2 > 0.3         净 +0.728 / +0.085 bp

第二次里 **0.3 反而比 0.2 差** ⇒ 非单调 ⇒ **单窗口测量被"那一时段的行情"污染**。
一个 9 分钟窗口只有 ~90 笔，而每笔净额的噪声远大于档位间的差异。

# 做法：交替区块

把 0.2 与 0.5 **在同一时段内交替**跑，每档 6 分钟、共 5 个区块（10 次切换，约 62 分钟）。
这样"行情漂移"对两档的影响**同期对称**，差分即可消掉时段效应：

    Δ = mean(净bp @ 0.2) − mean(净bp @ 0.5)

# 判据（事先定死）

  · 用**每区块的配对差**做统计：5 对差值 → 均值与标准误
  · `|Δ| > 2×SE` ⇒ 判为真实差异，采用更优档
  · `|Δ| ≤ 2×SE` ⇒ **判为无法区分**，此时选更窄的档（0.2），理由是
    窄档的**行情项更不负**（两次扫描都观察到：0.2 → +0.534、0.5 → +0.107/+0.242），
    且窄档成交笔数不低。**"不亏在行情上"比"多赚点差"更可靠。**

# 成本

每档需要重启 worker（`MM_SPREAD_MULT` 在 env 白名单里）⇒ 每次切换约 1 分钟开销，
10 次切换 ≈ 10 分钟。总计约 72 分钟。

用法：
    .venv\\Scripts\\python.exe scripts\\h158_ab_alternating.py --blocks 5 --minutes 6
    .venv\\Scripts\\python.exe scripts\\h158_ab_alternating.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from h146_width_sweep import (  # noqa: E402
    DEFAULT_ENV, DEFAULT_KEY, dsn, get_params, restart_worker, set_param,
    wait_hot_reload, window_stats,
)

ARMS = [0.2, 0.5]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=5, help="每个档位的区块数")
    ap.add_argument("--minutes", type=float, default=6.0, help="每个区块分钟数")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    orig = get_params().get(DEFAULT_KEY)
    orig = float(orig) if orig is not None else 0.5

    print("=" * 96)
    print("H158  交替区块 A/B：spread_mult 0.2 vs 0.5")
    print("=" * 96)
    print(f"  区块数/档 {a.blocks}   每区块 {a.minutes} 分钟   起始值 {orig}")
    print(f"  顺序：{' → '.join(str(ARMS[i % 2]) for i in range(a.blocks * 2))}")
    est = a.blocks * 2 * (a.minutes + 1.5)
    print(f"  预计耗时 ≈ {est:.0f} 分钟（含每次切换的重启开销）")

    if a.dry_run:
        print("\n  （dry-run）不执行")
        return 0

    pairs = []          # 每区块 (arm, dict)
    try:
        for b in range(a.blocks):
            for arm in ARMS:
                print(f"\n{'─'*96}")
                print(f"  区块 {b+1}/{a.blocks}  arm={arm}")
                set_param(DEFAULT_KEY, DEFAULT_ENV, arm)
                restart_worker()
                if not wait_hot_reload(arm, DEFAULT_KEY, timeout_s=120.0):
                    print("    ✗ 生效失败，跳过本区块")
                    continue
                time.sleep(120)          # 冷启动丢弃
                t0 = datetime.now().astimezone()
                t1 = t0 + timedelta(minutes=a.minutes)
                print(f"    窗口 {t0.strftime('%H:%M:%S')} ~ {t1.strftime('%H:%M:%S')}")
                while datetime.now().astimezone() < t1:
                    time.sleep(20)
                s = window_stats(t0, t1)
                s["arm"] = arm
                s["block"] = b + 1
                s["t0"] = t0.isoformat()
                s["t1"] = t1.isoformat()
                pairs.append(s)
                print(f"    成交 {s['fills']:>4}  价差 {s['spread_bp_w']:+.4f}  "
                      f"行情 {s['price_bp_w']:+.4f}  净 {s['net_bp_w']:+.4f} bp  "
                      f"({s['net_usd']:+.4f} USD)")
    finally:
        print(f"\n{'─'*96}\n  恢复 {DEFAULT_KEY}={orig}")
        set_param(DEFAULT_KEY, DEFAULT_ENV, orig)
        restart_worker()
        print(f"  已恢复：{wait_hot_reload(orig, DEFAULT_KEY, timeout_s=120.0)}")

    print("\n" + "=" * 96)
    print("结果")
    print("=" * 96)
    print(f"  {'区块':>4} {'arm':>5} {'成交':>5} {'价差bp':>9} {'行情bp':>9} "
          f"{'净bp':>9} {'净$':>9}")
    print("  " + "-" * 58)
    for s in pairs:
        print(f"  {s['block']:>4} {s['arm']:>5} {s['fills']:>5} {s['spread_bp_w']:>+9.4f} "
              f"{s['price_bp_w']:>+9.4f} {s['net_bp_w']:>+9.4f} {s['net_usd']:>+9.4f}")

    # 配对差
    by = {}
    for s in pairs:
        by.setdefault(s["block"], {})[s["arm"]] = s
    diffs, sp_d, pr_d = [], [], []
    for blk, d in sorted(by.items()):
        if len(d) == 2:
            diffs.append(d[0.2]["net_bp_w"] - d[0.5]["net_bp_w"])
            sp_d.append(d[0.2]["spread_bp_w"] - d[0.5]["spread_bp_w"])
            pr_d.append(d[0.2]["price_bp_w"] - d[0.5]["price_bp_w"])
    if len(diffs) >= 2:
        m = st.mean(diffs)
        se = st.pstdev(diffs) / (len(diffs) ** 0.5)
        print(f"\n  配对差（0.2 − 0.5），{len(diffs)} 对：")
        for i, x in enumerate(diffs, 1):
            print(f"    区块 {i}: Δ净 {x:+.4f} bp")
        print(f"    ⇒ 均值 Δ = **{m:+.4f} bp**   标准误 SE = {se:.4f}   "
              f"t = {m/se if se else 0:+.2f}")
        print(f"    分解：Δ价差 {st.mean(sp_d):+.4f}bp   Δ行情 {st.mean(pr_d):+.4f}bp")
        if se and abs(m) > 2 * se:
            win = 0.2 if m > 0 else 0.5
            print(f"\n  ⇒ **|Δ| > 2×SE，判为真实差异** ⇒ 采用 spread_mult = {win}")
        else:
            print(f"\n  ⇒ **|Δ| ≤ 2×SE，无法区分** ⇒ 取更窄档 0.2")
            print(f"     理由：两次扫描都观察到窄档的**行情项更不负**")
            print(f"     （0.2 → +0.534bp；0.5 → +0.107/+0.242bp）⇒ "
                  f"「不亏在行情上」比「多赚点差」更可靠")
    else:
        print("\n  ⚠️ 配对样本不足（<2 对），不下结论")

    out = ROOT / "research_l1" / "out" / "h158_ab.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"arms": ARMS, "pairs": pairs,
                               "diffs": diffs, "restored_to": orig},
                              ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    print(f"\n  写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
