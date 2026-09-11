# -*- coding: utf-8 -*-
"""Z11b：延迟出场反事实的逐笔核对（防止 Z11 出现模拟 bug）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "_audit_ml"))

import Z11_exit_delay as Z  # noqa: E402

recs = Z.build(75)
print(f"n={len(recs)}")
diffs = []
for r in recs:
    d = Z.delayed(r, 2) - r["base_usd"]
    diffs.append((d, r))
diffs.sort(key=lambda x: x[0])
print("\n最差 12 笔（延迟 2h 相对实际）：")
print(f"{'symbol':<10}{'tier':<6}{'通道':<18}{'实际USD':>10}{'延迟2h':>10}{'差':>9}"
      f"{'实际平仓价':>12}{'延迟价':>12}")
for d, r in diffs[:12]:
    # 找延迟出场价
    s, i0 = r["s"], r["i"]
    exit_ts = r["close_ts"] + 7200
    px = None
    for k in range(i0, len(s)):
        ts, _o, h, l, c = s[k]
        if r["sl"] > 0 and ((l <= r["sl"]) if r["side"] == "long" else (h >= r["sl"])):
            px = ("SL", r["sl"])
            break
        if ts >= exit_ts:
            px = ("close", c)
            break
    print(f"{r['symbol']:<10}{r['tier']:<6}{r['ch']:<18}{r['base_usd']:>+10.2f}"
          f"{Z.delayed(r,2):>+10.2f}{d:>+9.2f}{r['close']:>12.6g}"
          f"{(px[1] if px else float('nan')):>12.6g}  {px[0] if px else '?'}")

print("\n最好 8 笔：")
for d, r in diffs[-8:]:
    print(f"{r['symbol']:<10}{r['tier']:<6}{r['ch']:<18}{r['base_usd']:>+10.2f}"
          f"{Z.delayed(r,2):>+10.2f}{d:>+9.2f}")

pos = sum(d for d, _ in diffs if d > 0)
neg = sum(d for d, _ in diffs if d <= 0)
print(f"\n改善合计=${pos:+.2f}（{sum(1 for d,_ in diffs if d>0)} 笔） "
      f"恶化合计=${neg:+.2f}（{sum(1 for d,_ in diffs if d<=0)} 笔） 净=${pos+neg:+.2f}")
win = [r for r in recs if r["base_usd"] > 0]
los = [r for r in recs if r["base_usd"] <= 0]
print(f"盈利笔 n={len(win)} 延迟差=${sum(Z.delayed(r,2)-r['base_usd'] for r in win):+.2f}")
print(f"亏损笔 n={len(los)} 延迟差=${sum(Z.delayed(r,2)-r['base_usd'] for r in los):+.2f}")
# SL 是否被从开头就强制生效（可能是伪影）
n_sl_early = 0
for r in recs:
    if r["sl"] <= 0:
        continue
    s, i0 = r["s"], r["i"]
    for k in range(i0, len(s)):
        ts, _o, h, l, c = s[k]
        if ts >= r["close_ts"]:
            break
        if (l <= r["sl"]) if r["side"] == "long" else (h >= r["sl"]):
            n_sl_early += 1
            break
print(f"\n注意：真实持有期内曾触及 DB 当前 SL 的笔数={n_sl_early}"
      f"（若 >0，说明 DB sl_price 是活体值，用它做延迟期保护会提前离场）")
