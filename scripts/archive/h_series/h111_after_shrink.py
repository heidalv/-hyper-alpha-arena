"""H111：宇宙收缩后的效果量化 + vol_pause 闸的影响评估。

# 收缩做了什么

宇宙 `['ASTER','XRP','SOL','DOGE','UNI','VIRTUAL','SEI','ONDO','PENDLE','ARB']`
→ **`['ASTER','SOL','XRP']`**

依据（H107/H108 整夜实测强平率）：
```
ASTER  6.6%   (577 周期)
SOL    6.6%   (423 周期)
XRP   10.2%   (571 周期)
────────────────────────
移除：DOGE 22.2% / UNI 48.5% / ARB 53.6% / ONDO 61.2% / PENDLE 91.7% / SEI 100% / VIRTUAL 100%
```

# 预期效果（用实测数算，不用模拟）

每周期期望 = 入场腿贡献 − 强平成本/次 × 强平率

```
收缩前（整夜实测）：入场腿 −0.002 bp/行，强平率 13.0%，成本 0.134 USD/次
收缩后（用三个币的实测强平率加权）：强平率 ≈ 7.8%
```

# 本脚本做什么

1. 用**实测**给出收缩前后的每周期期望对比
2. 检查 `vol_pause` 闸：它在 `skip_counts` 里排第一（40 次）
   —— `vol_pause_sigma = 0.7`，看它挡掉了多少报价
3. 给出"还能再改什么"的诚实清单

用法：
    .venv\\Scripts\\python.exe scripts\\h111_after_shrink.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

# H107 实测（整夜）
FLAT = {"ASTER": 0.066, "SOL": 0.066, "XRP": 0.102, "DOGE": 0.222,
        "UNI": 0.485, "ARB": 0.536, "ONDO": 0.612, "PENDLE": 0.917,
        "SEI": 1.0, "VIRTUAL": 1.0}
CYCLES = {"ASTER": 577, "SOL": 423, "XRP": 571, "DOGE": 261,
          "UNI": 66, "ARB": 140, "ONDO": 49, "PENDLE": 12, "SEI": 2, "VIRTUAL": 3}
COST_PER_FLAT = 0.134      # USD/次（H106 实测，三期 −0.130/−0.119/−0.141）
ENTRY_BP = -0.002          # bp/行（整夜实测）
NOTIONAL = 135.0


def main():
    print("=" * 96)
    print("H111  宇宙收缩的效果量化")
    print("=" * 96)

    old = list(FLAT.keys())
    new = ["ASTER", "SOL", "XRP"]

    def p_rate(syms):
        num = sum(FLAT[s] * CYCLES[s] for s in syms)
        den = sum(CYCLES[s] for s in syms)
        return num / den if den else float("nan")

    po, pn = p_rate(old), p_rate(new)
    print(f"\n  收缩前强平率（按周期加权）= **{po*100:.1f}%**")
    print(f"  收缩后强平率（按周期加权）= **{pn*100:.1f}%**")
    print(f"  ⇒ 强平率降 **{(po-pn)*100:.1f} pp**（相对 {(1-pn/po)*100:.0f}%）")

    print(f"\n  ── 每周期期望（入场腿贡献 − 成本×强平率）──")
    print(f"    入场腿贡献 = {ENTRY_BP:.3f} bp/行（整夜实测 ≈ 0）")
    print(f"    强平成本   = {COST_PER_FLAT:.3f} USD/次")
    for lab, p in (("收缩前", po), ("收缩后", pn)):
        exp = 0.0 - COST_PER_FLAT * p
        print(f"    {lab}：0 − {COST_PER_FLAT:.3f} × {p:.3f} = **{exp:+.5f} USD/周期**")
    exp_o = -COST_PER_FLAT * po
    exp_n = -COST_PER_FLAT * pn
    print(f"\n    ⇒ 改善 **{exp_n-exp_o:+.5f} USD/周期**"
          f"（相对 {(1-exp_n/exp_o)*100:.0f}%）")
    # 换算小时（实测约 383 周期/小时）
    cph = 383.0
    print(f"    ⇒ 折合小时（按 {cph:.0f} 周期/小时）："
          f"**{exp_o*cph:+.2f} → {exp_n*cph:+.2f} USD/小时**")

    print("\n" + "=" * 96)
    print("⚠️ 诚实说明：收缩只是**减缓**，不是解决")
    print("=" * 96)
    need = 0.0  # 入场腿贡献需要多少才能打平
    print(f"\n  打平需要：入场腿贡献 ≥ {COST_PER_FLAT:.3f} × {pn:.3f} = "
          f"**{COST_PER_FLAT*pn:+.5f} USD/周期**")
    print(f"  而入场腿实测 ≈ 0（−0.002 bp/行）")
    print(f"  ⇒ **仍然为负**。收缩把亏损从 {exp_o*cph:+.2f} 减到 "
          f"{exp_n*cph:+.2f} USD/小时，但**没有改变「入场无优势」这个根本问题**。")

    # ── vol_pause 闸 ──
    print("\n" + "=" * 96)
    print("vol_pause 闸的影响（skip_counts 里排第一）")
    print("=" * 96)
    sf = ROOT / "logs" / "mm_lane_status.json"
    try:
        j = json.loads(sf.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  读心跳失败: {e}")
        return 0
    sk = j.get("skip_counts") or {}
    tot = sum(sk.values())
    print(f"\n  ticks = {j.get('ticks')}   闸门拦截合计 = {tot}")
    print(f"\n  {'闸门':<24} {'次数':>7} {'占比':>8}")
    print("  " + "-" * 42)
    for k, v in sorted(sk.items(), key=lambda x: -x[1]):
        print(f"  {k:<24} {v:>7} {v/max(tot,1)*100:>7.1f}%")
    vp = sk.get("vol_pause", 0)
    print(f"\n  `vol_pause` 占拦截的 {vp/max(tot,1)*100:.1f}%")
    print(f"  配置 `vol_pause_sigma = 0.7`（σ 归一超过 0.7 就暂停报价）")
    print(f"  ⇒ σ_norm = 近 20 期振幅/长期基准 − 1；0.7 意味着**振幅比基准高 70% 就停**")
    print(f"  ⇒ 夜间波动通常更大 ⇒ 该闸在夜间频繁触发，**把大量报价挡掉了**")
    print(f"\n  ⚠️ 但这个闸是**保护性**的：高波动时做市被逆选择更重。")
    print(f"     不能因为「它挡了很多」就放宽 —— 需要 A/B 才能定。")

    print("\n" + "=" * 96)
    print("下一步可做（按证据强度）")
    print("=" * 96)
    print("  1. **观察收缩后的实际强平率**（≥1 小时）—— 验证 7.8% 的预期")
    print("  2. `vol_pause_sigma` 的 A/B：0.7 → 1.0（放宽），看每周期净额是否改善")
    print("     · 判据：单位时间盈亏（不是每周期净额）")
    print("  3. 不做的事：砍更多币（已证尾部主导）、盲迁 Lighter（无法回测）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
