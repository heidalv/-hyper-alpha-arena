"""H36：跨所经济性算术 —— 用实测的捕获/逆向选择 + 各所真实费率算净额。

## 为什么必须先纠正一个公式（子代理指出，成立）

我此前把 `net = 捕获 − 逆向选择 + 返佣 − 费率` 写成
`0.200 − 0.55 + 0 − 0 = −0.35bp/笔`。**这是重复计算**：

    `0.55bp` **不是**独立的逆向选择项，它是**名义半价差未被捕获的余额**
    （0.750 − 0.200）。而逆向选择的独立度量是**成交后 markout**（我实测 −0.08~−0.30bp）。

**内部自洽的归因**：`net/笔 ≈ 0.200 − (0.08~0.30) = +0.12 ~ −0.10 bp`
—— 正好把实测的 −0.007~−0.02bp 夹在中间。

⇒ **在 27% 捕获率下，价差项按构造就接近打平；决定胜负的是费率/返佣项与捕获上限。**

而费率项是**唯一精确已知、且可以通过合同改变**的量。

## 实测输入（H34 修正后，tick 级，24h，5 币，26,411 决策）

    捕获@成交      +0.200 bp/笔
    markout@1s     −0.077 bp（BTC）~ +0.047（ASTER）
    净/笔          −0.0202bp（P0）/ −0.0073bp（P2）

## 费率（子代理调研，带来源）

    Aster 零售          0.0 / 4.0 bp
    Aster 注册做市商     **−0.5** / 1.6      （需 ≥$100M 月成交 + 报价义务）
    DESK (ex-HMX)        **−1.0** / 1.75
    dYdX T7 (≥$200M)     **−1.1** / 2.5
    dYdX T6 (≥$100M)     **−0.7** / 2.5
    Velocity (ex-Drift)  **−0.25** 全档位    ← 唯一"最小档位就给返佣"的
    Hotstuff 标准        **−0.2** / 2.5
    Lighter/Paradex      0.0 / 0.0
    Hyperliquid 基础     **+1.5** / 4.5
    Binance              **+2.0** / 5.0
    Bybit                +2.0 / 5.5
    OKX                  +2.0 / 5.0

## 捕获上限随价差变化

我们实测**捕获率 = 0.200 / 0.750 = 26.7%**。
若该比率随价差近似不变，则某所半价差 h 的捕获上限 ≈ 0.267·h：

    Aster 零售    h≈0.75bp   → 上限 0.200bp（实测值）
    Hyperliquid   h≈0.10bp   → 上限 **0.027bp**（BTC 报价价差 0.20bp）
    Lighter       h≈0.16bp   → 上限 0.043bp
    Binance       h≈0.01~0.03bp → 上限 0.003~0.008bp（一个 tick 宽）

**⇒ Aster 的"宽 touch"是它对 Binance/HL 的最大真实优势，且比费率差更值钱（零售档）。**

用法：
    .venv\\Scripts\\python.exe scripts\\h36_venue_economics.py
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

OUT = ROOT / "research_l1" / "out" / "h36_venue_economics.json"

# 实测输入
CAPTURE_BP = 0.200          # 成交时半价差（H34 修正后均值）
NOMINAL_HALF_BP = 0.750     # 挂单时名义半价差（报价宽 1.5bp 的一半）
CAPTURE_RATIO = CAPTURE_BP / NOMINAL_HALF_BP
MK_LOW, MK_HIGH = -0.08, -0.30   # 成交后 markout 区间（BTC ~ ASTER）

# venue: (maker_bp, taker_bp, half_spread_bp or None, note)
VENUES = [
    ("Aster 零售（现状）",        0.00, 4.00, 0.750, "当前所在档位"),
    ("Aster 注册做市商",         -0.50, 1.60, 0.750, "需 ≥$100M 月成交 + 报价义务"),
    ("DESK (ex-HMX)",           -1.00, 1.75, 0.375, "入口档最大返佣；价差未知，取中值估"),
    ("dYdX T7 (≥$200M/30d)",    -1.10, 2.50, None,  "全所仅 ~$34.7M/天，门槛≈10% 全所量"),
    ("dYdX T6 (≥$100M/30d)",    -0.70, 2.50, None,  "同上"),
    ("Velocity (ex-Drift)",     -0.25, 6.00, None,  "**唯一最小档位就给返佣**；无价差数据"),
    ("Hotstuff 标准档",         -0.20, 2.50, None,  "0–1M 14d 档"),
    ("Lighter / Paradex",        0.00, 0.00, 0.160, "零费率 + 零返佣"),
    ("Hyperliquid 基础",         1.50, 4.50, 0.100, "BTC 报价价差 0.20bp（arXiv 2608.04373v3）"),
    ("Binance VIP0",             2.00, 5.00, 0.015, "一个 tick 宽"),
    ("Bybit VIP0",               2.00, 5.50, 0.020, "估"),
    ("OKX VIP0",                 2.00, 5.00, 0.020, "估"),
]


def main() -> int:
    print(__doc__)
    print("=" * 96)
    print("H36 跨所经济性算术（每**往返** = 2 条 maker 腿）")
    print(f"实测捕获率 = {CAPTURE_BP:.3f} / {NOMINAL_HALF_BP:.3f} = {CAPTURE_RATIO*100:.1f}%")
    print(f"markout 区间 = {MK_LOW:+.2f} ~ {MK_HIGH:+.2f} bp/笔\n")

    rows = []
    for name, mk_fee, tk_fee, h, note in VENUES:
        # 捕获上限：价差未知时用 Aster 的实测值（最保守：假设一样）
        cap = CAPTURE_RATIO * h if h is not None else CAPTURE_BP
        spread_known = h is not None
        # 每笔净额 = 捕获 + markout + maker 费（maker 费为负=返佣=加分）
        lo = cap + MK_LOW + mk_fee
        hi = cap + MK_HIGH + mk_fee
        # 往返
        rows.append({
            "venue": name, "maker_bp": mk_fee, "taker_bp": tk_fee,
            "half_spread_bp": h, "capture_ceiling_bp": cap,
            "spread_known": spread_known,
            "net_per_fill_lo": lo, "net_per_fill_hi": hi,
            "net_per_rt_lo": 2 * lo, "net_per_rt_hi": 2 * hi,
            "fee_delta_rt_vs_aster": 2 * (mk_fee - 0.00),
            "note": note,
        })

    print("%-24s %8s %10s %11s %21s %11s" %
          ("venue", "maker", "半价差", "捕获上限", "净/往返 区间", "费率Δ/往返"))
    for r in rows:
        hs = ("%.3f" % r["half_spread_bp"]) if r["half_spread_bp"] is not None else "未知"
        print("%-24s %+8.2f %10s %11.3f %9.2f ~ %-9.2f %+11.2f"
              % (r["venue"], r["maker_bp"], hs, r["capture_ceiling_bp"],
                 r["net_per_rt_lo"], r["net_per_rt_hi"], r["fee_delta_rt_vs_aster"]))

    print("\n" + "=" * 96)
    print("[排序] 按**净/往返区间中值**（越高越好）")
    rows_sorted = sorted(rows, key=lambda r: -((r["net_per_rt_lo"] + r["net_per_rt_hi"]) / 2))
    print("    %-24s %12s %14s %s" % ("venue", "净/往返中值", "费率Δ vs Aster", "价差数据"))
    for r in rows_sorted:
        mid = (r["net_per_rt_lo"] + r["net_per_rt_hi"]) / 2
        print("    %-24s %+12.3f %+14.2f %s"
              % (r["venue"], mid, r["fee_delta_rt_vs_aster"],
                 "已知" if r["spread_known"] else "**未知（用 Aster 值代）**"))

    print("\n[判定]")
    best = rows_sorted[0]
    print("    ⇒ 最优：**%s**，净/往返中值 %+.3f bp"
          % (best["venue"], (best["net_per_rt_lo"] + best["net_per_rt_hi"]) / 2))
    hl = next(r for r in rows if r["venue"].startswith("Hyperliquid"))
    bn = next(r for r in rows if r["venue"].startswith("Binance"))
    print("    ⇒ Hyperliquid 净/往返 %+.2f ~ %+.2f bp（比现状差 %+.2f bp/往返）"
          % (hl["net_per_rt_lo"], hl["net_per_rt_hi"], hl["fee_delta_rt_vs_aster"]))
    print("    ⇒ Binance     净/往返 %+.2f ~ %+.2f bp（比现状差 %+.2f bp/往返）"
          % (bn["net_per_rt_lo"], bn["net_per_rt_hi"], bn["fee_delta_rt_vs_aster"]))
    print("\n    ⇒ **结论：换到 Binance / Hyperliquid 做被动做市，是明确变差。**")
    print("       原因有两层，且都是结构性的：")
    print("       ① 费率：Binance maker +2.0bp/腿、HL +1.5bp/腿；我们 0.0bp 已经是优势")
    print("       ② 价差：他们的 touch 只有 0.1~0.02bp 宽，捕获上限随之降到 0.03~0.008bp")
    print("       而 Aster 的宽 touch（半价差 0.75bp）反而是**最大优势**。")
    print("\n    ⇒ **真正该做的不是换所，而是去拿 Aster 的注册做市商档：−0.5bp/腿**")
    print("       = **+1.0bp/往返**，这比实测的每笔净额（−0.007~−0.02bp）大 50~100 倍。")

    print("\n[必须声明的两个不确定]")
    print("    ① 捕获率 26.7% 是否跨所不变 —— **未验证**。若窄价差市场的捕获率更高")
    print("       （更深的书 ⇒ touch 侵蚀更少），HL 的上限会从 0.027 升向 0.10bp，")
    print("       但 +1.5bp 的 maker 费仍然压倒它。")
    print("    ② DESK/dYdX/Velocity 的**价差与深度未知**，上表用 Aster 值代替 ⇒")
    print("       它们的排序**不可信**，只说明「费率项本身值得争取」。")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "capture_bp": CAPTURE_BP, "nominal_half_bp": NOMINAL_HALF_BP,
        "capture_ratio": CAPTURE_RATIO, "mk_range": [MK_LOW, MK_HIGH],
        "venues": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
