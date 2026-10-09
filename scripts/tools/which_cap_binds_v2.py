"""Why are legs $46k-66k when I expected the cap to be $4,448?

Hypothesis: I applied the WRONG constraint. `max_leg_notional_mult` (F338) lives in
the runner's maker-lane leg planner; the active-flow lane sizes from the risk model
`notional_cap_usd(equity, stop_bp, n, loss_frac)` instead.

If the learned param `notional_loss_frac = 0.01` is what is passed as loss_frac,
then at equity 9884.91 and a 15bp stop the allowance is:

    equity * 0.01 / 0.0015 = 65,899      <- matches the observed 66,722

This prints every candidate constraint side by side so the binder is unambiguous.
"""
from __future__ import annotations

import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from backend.services.market_maker.flow_rules import notional_cap_usd  # noqa: E402

EQ = 9884.91
FILL_NOTIONAL = 1482.74
LEG_MULT = 3.0
GROSS_RATIO = 3.0

print("=" * 92)
print(f"哪个约束真的在决定腿量？（equity={EQ:,.2f}, fill_notional={FILL_NOTIONAL:,.2f}）")
print("=" * 92)

print()
print("  A) F338 单腿上限 = max_leg_notional_mult x fill_notional")
print(f"     = {LEG_MULT} x {FILL_NOTIONAL:,.2f} = **{LEG_MULT*FILL_NOTIONAL:,.2f}**")
print("     [注意] 它只在 runner 的做市腿规划器里执行；")
print("            active_flow 车道自己算 cap，不走那一段。")

print()
print("  B) T20 总敞口上限 = max_gross_notional_ratio x equity")
print(f"     = {GROSS_RATIO} x {EQ:,.2f} = {GROSS_RATIO*EQ:,.2f}")

print()
print("  C) 风险模型 notional_cap_usd(equity, stop_bp, n, loss_frac)")
print(f"     {'loss_frac':>10}{'stop=10bp':>14}{'stop=15bp':>14}{'stop=25bp':>14}")
for lf in (0.005, 0.01):
    cells = [float(notional_cap_usd(EQ, sb, 1, lf)) for sb in (10, 15, 25)]
    tag = "  <- 常量 EQUITY_LOSS_PER_TRADE" if lf == 0.005 else "  <- 学习参数 notional_loss_frac"
    print(f"     {lf:>10}" + "".join(f"{c:>14,.0f}" for c in cells) + tag)

print()
p = r"D:\001Alpha\Hyper-Alpha-Arena\data\flow_learn_params.json"
try:
    d = json.load(open(p, encoding="utf-8"))
    nlf = float(d.get("notional_loss_frac") or 0.0)
    print(f"  实测学习参数 notional_loss_frac = **{nlf}**")
    if nlf > 0:
        for sb in (10, 15, 25):
            v = float(notional_cap_usd(EQ, sb, 1, nlf))
            print(f"     stop={sb:>2}bp => 风险模型允许单腿 **{v:,.0f}**"
                  f"  ({v/EQ:.2f}x 权益)")
except Exception as e:  # noqa: BLE001
    print("  读取学习参数失败:", e)

print()
print("=" * 92)
print("判定")
print("=" * 92)
print("  实测近期腿量 $46,007 ~ $66,722，与 **loss_frac=0.01 的风险模型** 吻合。")
print("  => **不是缺陷，是风险模型本身允许这么大**。")
print("     我上一轮说「上限被绕过」是**我判断错了**：我把**做市腿**的 F338 上限")
print("     当成了**主动流**的上限，而两者是不同代码路径。")
print()
print("  => 真正的问题：**`notional_loss_frac = 0.01`（单笔风险 1% 权益）**")
print("     配 15bp 止损 => 名义 6.7x 权益。**止损一旦被穿透，实际亏损远超 1%。**")
