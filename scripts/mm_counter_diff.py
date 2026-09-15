"""实盘 vs 模型：同窗口**计数器对拍**（把"模型少判 N 倍成交"分解到具体环节）。

[F212 2026-09-15] 为什么要这个工具
--------------------------------------------------
已确认的事实：同窗口、同配置、挂宽可比时，模型判出的成交远少于实盘
（12:57~14:02：模型 46 笔 vs 实盘 203 笔 ✗）。而两侧**走的是同一个 `plan_tick`** ✓，
所以差异只可能来自"喂进去的东西"或"判定的对象"：
    · 决策次数（快照数 × 币数）
    · 每次决策里**是否有可判挂单**（报价被闸门挡掉 ⇒ 无可判对象）
    · 判定区间是否为空（`win_empty`）
    · 穿越次数（区间价是否触及挂单）
    · 穿越→成交的转化（`nofill_*`）
    · 量聚合（`seg_sell/seg_buy`）与队列份额造成的腿量
本脚本把这些逐项并排，用**比值**指出分歧落在哪一环 ✓ —— 比值 ≈1 的环节是健康的，
偏离最大的那一个就是根因所在 ✓。

用法：
    python scripts/mm_counter_diff.py logs/live_shadow_now.json logs/f212_model_same.json
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional


def _load(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    live = _load(sys.argv[1])
    model_doc = _load(sys.argv[2])
    rows = model_doc.get("rows") or []
    if not rows:
        print("✗ 模型 JSON 里没有 rows")
        return 2
    per = rows[0].get("per") or []
    if not per:
        print("✗ 模型 JSON 里没有 per")
        return 2
    mdl = per[0]

    def g(d: Dict[str, Any], k: str, sub: str = "") -> Any:
        v = d.get(k)
        if sub:
            return (v or {}).get(sub)
        return v

    span_l = None
    span_m = None
    try:
        span_m = float(model_doc.get("span_h") or 0.0)
    except Exception:
        pass

    print("=" * 104)
    print(f"实盘 vs 模型 同窗口对拍")
    print(f"  实盘: {sys.argv[1]}")
    print(f"  模型: {sys.argv[2]}   窗口跨度 {span_m if span_m else '?'} h"
          f"  口径 {model_doc.get('delays_ms')}")
    print("=" * 104)

    def line(name: str, lv: Any, mv: Any, ratio: bool = True) -> None:
        def fmt(v: Any) -> str:
            if v is None:
                return "—"
            if isinstance(v, float):
                return f"{v:,.3f}" if abs(v) < 1000 else f"{v:,.0f}"
            return str(v)
        try:
            lvf, mvf = float(lv), float(mv)
            r = f"{mvf/lvf:>8.2f}×" if (ratio and lvf) else " " * 9
        except Exception:
            r = " " * 9
        print(f"  {name:<26}{fmt(lv):>18}{fmt(mv):>18}{r}")

    print(f"  {'项目':<26}{'实盘':>18}{'模型':>18}{'模型/实盘':>9}")
    print("  " + "-" * 98)
    line("成交笔数 fills", g(live, "fills"), g(mdl, "fills"))
    line("名义 notional", g(live, "notional"), g(mdl, "notional"))
    try:
        line("每笔名义", float(live.get("notional") or 0) / max(1, int(live.get("fills") or 1)),
             float(mdl.get("notional") or 0) / max(1, int(mdl.get("fills") or 1)))
    except Exception:
        pass
    line("报价决策 quoted_decisions", g(live, "quoted_decisions"), g(mdl, "quoted_decisions"))
    line("冻结档占比", g(live, "frozen_share"), g(mdl, "frozen_share"))
    awl, awm = g(live, "avg_width_bp") or {}, g(mdl, "avg_width_bp") or {}
    line("挂宽(bid)", awl.get("bid"), awm.get("bid"))
    line("挂宽(ask)", awl.get("ask"), awm.get("ask"))
    line("平均σ", g(live, "avg_sigma"), g(mdl, "avg_sigma"))
    print("  " + "-" * 98)
    for k in ("both", "one", "none"):
        line(f"报价侧 {k}", (g(live, "side_counts") or {}).get(k),
             (g(mdl, "side_counts") or {}).get(k))
    print("  " + "-" * 98)
    lc = live.get("cross_counts") or {}
    mc = mdl.get("cross_counts") or {}
    keys = sorted(set(lc) | set(mc))
    for k in keys:
        line(f"cross {k}", lc.get(k, "—"), mc.get(k, "—"))
    print("  " + "-" * 98)
    ls = live.get("skip_counts") or {}
    ms = mdl.get("skip_counts") or {}
    for k in sorted(set(ls) | set(ms), key=lambda x: -(ms.get(x, 0) + ls.get(x, 0)))[:10]:
        line(f"skip {k}", ls.get(k, "—"), ms.get(k, "—"))

    # 关键派生比值
    print("\n【派生指标】")
    try:
        lb = int(lc.get("win_judged") or 0) + int(lc.get("win_empty") or 0)
        mb = int(mc.get("win_judged") or 0) + int(mc.get("win_empty") or 0)
        print(f"  判定次数(win_judged+win_empty): 实盘 {lb}  模型 {mb}  比 {mb/max(1,lb):.2f}×")
    except Exception:
        pass
    try:
        lcr = int(lc.get("cross_buy") or 0) + int(lc.get("cross_sell") or 0)
        mcr = int(mc.get("cross_buy") or 0) + int(mc.get("cross_sell") or 0)
        lfl = int(lc.get("fill_buy") or 0) + int(lc.get("fill_sell") or 0)
        mfl = int(mc.get("fill_buy") or 0) + int(mc.get("fill_sell") or 0)
        print(f"  穿越次数: 实盘 {lcr}  模型 {mcr}  比 {mcr/max(1,lcr):.2f}×")
        print(f"  穿越→成交转化: 实盘 {100*lfl/max(1,lcr):.0f}%  模型 {100*mfl/max(1,mcr):.0f}%")
    except Exception:
        pass
    lo_l = (g(live, "side_counts") or {}).get("one", 0)
    bo_l = (g(live, "side_counts") or {}).get("both", 0)
    lo_m = (g(mdl, "side_counts") or {}).get("one", 0)
    bo_m = (g(mdl, "side_counts") or {}).get("both", 0)
    print(f"  单边率: 实盘 {100*lo_l/max(1,lo_l+bo_l):.0f}%  模型 {100*lo_m/max(1,lo_m+bo_m):.0f}%")

    print("\n读法：比值最偏离 1.00× 的那一行，就是「模型少判成交」的根因所在 ✓")
    print("  · fills 比 ≈ crosses 比 ⇒ 差异在「看到多少穿越」（区间/可见性/挂单是否存在）")
    print("  · crosses 比 ≈ decisions 比 ⇒ 差异在「做了多少次决策」")
    print("  · 其余 ⇒ 差异在「每次决策里是否有可判挂单」（闸门）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
