"""H37：换所到底更好还是更差 —— 只算**已确知**的费率项（修正 H36 的符号错误）。

## H36 的错

H36 写成 `净额 = 捕获 + markout + maker_fee`，然后按净额排序。
**符号反了**：maker 费是**成本**（正=付钱，负=收返佣），
把它**加**进净额等于说"付更多手续费更好"⇒ 结果 Binance(+2.0bp) 排第一，荒谬。

## 正确的做法：以现状为基准，只比较**确知**的那一项

我们只有一个所（Aster）的**实测**微观结构（捕获率 26.7%、markout −0.08~−0.30bp）。
其他所的价差/深度/毒性**没有实测** ⇒ 任何跨所净额的绝对估计都是猜。

**但费率项是 100% 确知的**（官方文档），且它在各所之间差 3.5bp/往返。
所以正确的第一步是：

    Δ净额/往返 = (基准所 maker 费 − 目标所 maker 费) × 2   ← 只看费率

负=更差，正=更好。**费率越低（越负）越好。**

## 我们与各所的费率差（每往返，相对 Aster 零售 0.0bp）

    Binance   +2.0/腿 ⇒ −4.0 bp/往返   （明显更差）
    Bybit     +2.0    ⇒ −4.0
    OKX       +2.0    ⇒ −4.0
    Hyperliquid +1.5  ⇒ −3.0
    Lighter/Paradex 0.0 ⇒  0.0（但价差更窄 ⇒ 实际更差）
    Aster 零售  0.0    ⇒  0.0（现状）
    Hotstuff  −0.2    ⇒ +0.4
    Velocity  −0.25   ⇒ +0.5
    Aster 注册MM −0.5  ⇒ **+1.0**
    dYdX T6   −0.7    ⇒ +1.4
    DESK      −1.0    ⇒ +2.0
    dYdX T7   −1.1    ⇒ +2.2

## 第二个确知项：捕获上限随价差缩放

若捕获率 26.7% 跨所不变，则半价差 h 的所捕获上限 ≈ 0.267·h：

    Aster 零售    h=0.750bp ⇒ 0.200bp   （实测值，非估计）
    Hyperliquid   h=0.100bp ⇒ 0.027bp   （BTC 报价价差 0.20bp，arXiv 2608.04373v3）
    Binance       h≈0.015bp ⇒ 0.004bp   （一个 tick 宽）
    Lighter       h=0.160bp ⇒ 0.043bp

⇒ **换到 Binance/HL 会在"费率"和"捕获"两处同时变差。**

用法：
    .venv\\Scripts\\python.exe scripts\\h37_venue_delta.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OUT = ROOT / "research_l1" / "out" / "h37_venue_delta.json"

BASE_MAKER = 0.00              # Aster 零售
CAPTURE = 0.200                # 实测捕获（bp/笔）
NOMINAL_HALF = 0.750           # 实测名义半价差
RATIO = CAPTURE / NOMINAL_HALF

VENUES = [
    ("dYdX T7 (≥$200M/30d)",  -1.10, 2.50, None,  "门槛≈全所量的 10%"),
    ("DESK (ex-HMX)",         -1.00, 1.75, 0.375, "三度改名，深度未知"),
    ("dYdX T6 (≥$100M/30d)",  -0.70, 2.50, None,  "同上"),
    ("Aster 注册做市商",       -0.50, 1.60, 0.750, "**同所，无迁移风险**"),
    ("Velocity (ex-Drift)",   -0.25, 6.00, None,  "唯一最小档位就给返佣"),
    ("Hotstuff 标准档",        -0.20, 2.50, None,  "0–1M 14d 档"),
    ("Aster 零售（现状）",       0.00, 4.00, 0.750, "实测基线"),
    ("Lighter / Paradex",      0.00, 0.00, 0.160, "零费零返佣"),
    ("Hyperliquid 基础",        1.50, 4.50, 0.100, "官方费率，BTC 价差 0.20bp"),
    ("Binance VIP0",           2.00, 5.00, 0.015, "官方费率，一个 tick 宽"),
    ("Bybit VIP0",             2.00, 5.50, 0.020, "官方费率"),
    ("OKX VIP0",               2.00, 5.00, 0.020, "官方费率"),
]


def main() -> int:
    print("H37 换所净差（相对 Aster 零售；只算已确知项）")
    print(f"实测捕获 {CAPTURE:.3f}bp / 名义半价差 {NOMINAL_HALF:.3f}bp = 捕获率 {RATIO*100:.1f}%\n")

    rows = []
    for name, mk, tk, h, note in VENUES:
        fee_delta = (BASE_MAKER - mk) * 2                      # 每往返；正=更省
        cap = RATIO * h if h is not None else CAPTURE
        cap_delta = (cap - CAPTURE) * 2                         # 每往返
        if h is None:
            # 价差未知：不给合计，避免把猜测当结论
            total = None
        else:
            total = fee_delta + cap_delta
        rows.append({"venue": name, "maker_bp": mk, "taker_bp": tk,
                     "half_spread_bp": h, "spread_known": h is not None,
                     "capture_bp": cap, "fee_delta_rt": fee_delta,
                     "capture_delta_rt": cap_delta, "total_delta_rt": total,
                     "note": note})

    print("%-24s %7s %9s %12s %12s %14s" %
          ("venue", "maker", "半价差", "费率Δ/往返", "捕获Δ/往返", "合计Δ/往返"))
    for r in sorted(rows, key=lambda r: -(r["total_delta_rt"] if r["total_delta_rt"] is not None else -99)):
        hs = ("%.3f" % r["half_spread_bp"]) if r["half_spread_bp"] is not None else "未知"
        tot = ("%+12.3f" % r["total_delta_rt"]) if r["total_delta_rt"] is not None else "     待实测"
        print("%-24s %+7.2f %9s %+12.2f %+12.3f %14s"
              % (r["venue"], r["maker_bp"], hs, r["fee_delta_rt"],
                 r["capture_delta_rt"], tot))

    print("\n[判定]")
    print("  · 相对的**费率项是确知的**（官方文档），下面这一列可直接用：")
    for r in sorted(rows, key=lambda r: -r["fee_delta_rt"]):
        verdict = "更好" if r["fee_delta_rt"] > 0 else ("不变" if r["fee_delta_rt"] == 0 else "**更差**")
        print("      %-24s %+6.2f bp/往返   %s" % (r["venue"], r["fee_delta_rt"], verdict))

    print("\n  · 叠加捕获上限后（仅价差已知的所）：")
    known = [r for r in rows if r["total_delta_rt"] is not None]
    for r in sorted(known, key=lambda r: -r["total_delta_rt"]):
        print("      %-24s 合计 %+7.3f bp/往返" % (r["venue"], r["total_delta_rt"]))

    print("\n[结论]")
    bn = next(r for r in rows if r["venue"].startswith("Binance"))
    hl = next(r for r in rows if r["venue"].startswith("Hyperliquid"))
    aster_mm = next(r for r in rows if "注册做市商" in r["venue"])
    print("  ① **换到 Binance：明确更差 %+.1f bp/往返**（费率）+ %+.3f（捕获）= %+.1f"
          % (bn["fee_delta_rt"], bn["capture_delta_rt"], bn["total_delta_rt"]))
    print("  ② **换到 Hyperliquid：明确更差 %+.1f bp/往返**（费率）+ %+.3f（捕获）= %+.1f"
          % (hl["fee_delta_rt"], hl["capture_delta_rt"], hl["total_delta_rt"]))
    print("     —— 与直觉相反：HL 的 maker 费是 **+1.5bp 成本**，不是返佣。")
    print("  ③ **真正该做的不是换所，是拿 Aster 自己的注册做市商档：%+.1f bp/往返**"
          % aster_mm["fee_delta_rt"])
    print("     —— 同一个所、同一套微观结构、**零迁移风险**，却是最大的一块。")
    print("  ④ 返佣所（DESK/dYdX/Velocity）的**价差未知** ⇒ 合计不能算。")
    print("     它们的费率优势（+0.4~+2.2bp/往返）只有在价差不比 Aster 窄太多时才成立。")
    print("\n  ⚠️ 两个必须声明：")
    print("     · 捕获率 26.7% 跨所不变是**假设**。若窄价差市场捕获率更高，")
    print("       HL 的捕获Δ会从 −0.35 收窄，但 +1.5bp/腿的费率仍然压倒它。")
    print("     · 本研究**未能找到**任何 Aster 之外的实测 markout/毒性数据 ⇒")
    print("       跨所的「逆向选择」差异**完全未知**，本表不含该项。")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"base_maker_bp": BASE_MAKER, "capture_bp": CAPTURE,
                               "nominal_half_bp": NOMINAL_HALF, "ratio": RATIO,
                               "venues": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
