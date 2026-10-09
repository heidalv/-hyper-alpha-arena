"""H8 · 挂单进出的往返算术（决定性检验：点差能不能赚到？）

H7 建立的结构事实：
  - 逆失衡侧成交数是顺失衡侧的约 2 倍，而逆失衡侧每笔 markout 为负
  - 顺失衡侧 markout 好（bid +0.002~+0.009，ask +0.070~+0.193）但几乎不成交
  ⇒ 不存在"选对方向就赚钱"

H8 要回答的问题（纯算术，不需要新模拟器）：
  挂单进 + 挂单出 的往返，到底能不能赚到那 1.477bp 的点差？

推导（全部量都已在 H5/H7 实测）：
  设 β = 成交时相对 mid 的价格优势 = 该侧半价差（买单在 bid = mid − β；卖单在 ask = mid + β）
  设 mk(τ) = 成交后 τ 时刻的 mid 相对**成交时 mid** 的变化（H5 实测，已含逆选择）
  设 D(τ)  = 从建仓到平仓的 mid 漂移（τ 秒持仓期内）

  一笔完整往返（买挂成交 → 卖挂成交，持仓 τ）：
    净 = (卖出价 − 买入价) / mid
       = [(mid_τ + β) − (mid_0 − β)] / mid
       = 2β + D(τ)
  换成 markout 口径：mk(τ) = D(τ) ⇒
    净 = 2β + mk(τ)                       ... (买方向；卖方向符号对称)

  ⇒ 关键结论：**往返净 = 2×半价差 + 成交后 markout**
     注意是 **2β = 整个点差**，因为进场靠挂单赚了 β，出场靠挂单又赚了 β。

  然而 H5 的 mk 是"以挂单时刻 mid 为基准"还是"以成交时刻 mid 为基准"会差一个 β：
    - H5 存了两种。以成交时刻 tf 为基准的 mk_tf **不含** β（β 发生在成交之前）。
    - 因此上式成立：净 = 2β + mk_tf(τ)。

  但**这只有在买入腿与卖出腿都能成交时成立**。若只有一条腿成交，就变成单边持仓，
  必须按 mk_tf 承担逆选择，且最终要么等到反向腿成交、要么超时。
  ⇒ 必须同时给出"往返完成率"。

本脚本给出：
  1. 每标的的 2β（整个点差）与 mk_tf(τ)（多档 τ）
  2. 往返毛净 = 2β + mk_tf
  3. 净费率（Aster maker 0bp）
  4. **保守情景**：只用单边 markout（假设无法等到反向腿、只能被动持有到期再挂出）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parents[1] / "out"

HORIZONS = [1000, 5000, 30000]


def load(name):
    p = OUT / name
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def main():
    h5 = load("h5_adverse_selection.json")
    if not h5:
        print("missing h5_adverse_selection.json")
        return

    maker_bp = float(h5["fees_asterdex"]["maker_bp"])
    taker_bp = float(h5["fees_asterdex"]["taker_bp"])

    print(f"Aster 费率: maker={maker_bp}bp taker={taker_bp}bp")
    print(f"窗口 {h5['window_h']}h")
    print()
    print("往返净 = (整个点差 2β) + (成交后 markout) − (2×maker费率)")
    print("=" * 104)
    hdr = (f"{'sym':<11}{'spread':>8}" + "".join(f"{'mk' + str(h//1000) + 's':>9}" for h in HORIZONS)
           + "".join(f"{'RT' + str(h//1000) + 's':>9}" for h in HORIZONS) + f"{'fill':>8}")
    print(hdr)
    print("-" * 104)

    rows = []
    for sym, r in h5["symbols"].items():
        if "error" in r:
            continue
        sp = r["spread_bp"]["median"] if r.get("spread_bp") else None
        if sp is None:
            continue
        mks = {}
        for h in HORIZONS:
            st = r.get(f"mk_tf_{h}")
            mks[h] = st["mean"] if st else None
        rts = {}
        for h in HORIZONS:
            rts[h] = (sp + mks[h] - 2.0 * maker_bp) if mks[h] is not None else None
        fill = r.get("fill_rate")
        line = f"{sym:<11}{sp:>8.3f}"
        for h in HORIZONS:
            line += (f"{mks[h]:>9.3f}" if mks[h] is not None else f"{'NA':>9}")
        for h in HORIZONS:
            line += (f"{rts[h]:>9.3f}" if rts[h] is not None else f"{'NA':>9}")
        line += f"{fill:>8.4f}"
        print(line)
        rows.append({"symbol": sym, "spread_bp": sp, "mk_tf": mks, "rt_net": rts,
                     "fill_rate": fill})

    print()
    print("=" * 104)
    print("解读")
    print("=" * 104)
    pos5 = [x for x in rows if x["rt_net"].get(5000) is not None and x["rt_net"][5000] > 0]
    print(f"5s 往返净为正的标的: {[x['symbol'] for x in pos5] or '（无）'}")
    for h in HORIZONS:
        vals = [x["rt_net"][h] for x in rows if x["rt_net"].get(h) is not None]
        if vals:
            print(f"  {h//1000}s 往返净: 均值 {np.mean(vals):+.3f}bp  "
                  f"中位 {np.median(vals):+.3f}bp  为正 {sum(1 for v in vals if v>0)}/{len(vals)}")

    print()
    print("⚠️ 反查：这个算术假定了'买入腿与卖出腿都能成交'。")
    print("   但 H7 表明成交集中在逆失衡侧，两条腿同时成交的联合概率未测。")
    print("   故上表是**上界**；真实值需 H9 的双腿联合模拟。")

    rep = {"generated_at": None, "fees": {"maker_bp": maker_bp, "taker_bp": taker_bp},
           "formula": "RT_net = spread_bp(2*beta) + mk_from_filltime - 2*maker_fee_bp",
           "caveat": "assumes both legs fill; H7 shows fills concentrate on the contrarian "
                     "side, so joint two-leg probability is unmeasured => this is an UPPER BOUND",
           "horizons_ms": HORIZONS, "rows": rows}
    p = OUT / "h8_roundtrip_arithmetic.json"
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {p}")


if __name__ == "__main__":
    main()
