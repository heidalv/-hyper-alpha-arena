"""H99：F291（持有 60→120s）与敞口闸的相互作用 —— 净敞口可能成为新瓶颈。

# 现场观察（F291 上线约 2 分钟后）

```
同时持仓 5 个（此前 60s 上限时更少）：
  ASTER  qty=  183.7945  age=54s
  XRP    qty= -240.9639  age=54s
  SOL    qty=   -3.6897  age=54s
  DOGE   qty= -804.9000  age=38s
  ONDO   qty= -321.8463  age=54s
持仓总名义 = **$1084.72** = 4.01x equity($270.54)
```

而闸门上限：
```
max_gross_notional_ratio = 12.0  => $3246.48   （宽松）
max_net_exposure_ratio   =  4.0  => **$1082.16**（**已经顶到**）
max_net_directional_ratio=  1.0  => $270.54/币
skip_counts: net_exposure=35   ← 闸门已频繁触发
```

**⇒ 净敞口闸成了新瓶颈。** 持有变久 ⇒ 同时持仓变多 ⇒ 更容易触发。

# 关键区分（决定要不要放宽）

  · `gross`（总名义，多空**绝对值**之和）大是**做市的正常状态**（多空自然对冲）
  · `net`（净名义，多空**带符号**之和）大才是**方向性风险**
  · 若两者接近 ⇒ 组合几乎没有内部对冲 ⇒ 确实有方向性敞口 ⇒ **不该放宽**
  · 若 net ≪ gross ⇒ 大部分是内部对冲掉的 ⇒ **可以在守住 net 的前提下放宽 gross**

**本脚本先算清楚当前到底偏哪一边，再决定动不动参数。**

判据（事先定死）：
  · 若 |net| / gross < 0.5 ⇒ 内部对冲良好 ⇒ gross 可放宽、net 保持
  · 若 |net| / gross > 0.8 ⇒ 组合基本是单向的 ⇒ **不动参数**，
    并考虑收缩（因为 4x equity 的净敞口在 $300 账户上是过高风险）

用法：
    .venv\\Scripts\\python.exe scripts\\h99_exposure_interaction.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

STATUS = ROOT / "logs" / "mm_lane_status.json"


def main():
    try:
        j = json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"✗ 读心跳失败: {e}")
        return 1

    eq = float(j.get("equity") or 0.0)
    states = j.get("states") or {}
    print("=" * 92)
    print("H99  F291（持有 120s）与敞口闸的相互作用")
    print("=" * 92)
    print(f"\n  equity = ${eq:.2f}   同时持仓（|qty|>0 的币）：")

    gross = 0.0
    net = 0.0
    rows = []
    for sym, s in states.items():
        if not isinstance(s, dict):
            continue
        q = float(s.get("qty") or 0.0)
        px = float(s.get("avg_px") or 0.0)
        if abs(q) < 1e-9 or px <= 0:
            continue
        notl = q * px          # 带符号
        gross += abs(notl)
        net += notl
        rows.append((sym, q, px, notl))

    if not rows:
        print("    （当前无持仓）")
    else:
        print(f"\n  {'币':<10} {'qty':>14} {'avg_px':>12} {'带符号名义$':>14}")
        print("  " + "-" * 54)
        for sym, q, px, notl in sorted(rows, key=lambda x: -abs(x[3])):
            print(f"  {sym:<10} {q:>14.4f} {px:>12.6f} {notl:>+14.2f}")

    print(f"\n  **gross（多空绝对值之和）= ${gross:>10.2f}  = {gross/max(eq,1e-9):.2f}x equity**")
    print(f"  **net  （多空带符号之和）  = ${net:>+10.2f}  = {net/max(eq,1e-9):+.2f}x equity**")
    ratio = abs(net) / max(gross, 1e-9)
    print(f"  |net| / gross = **{ratio:.3f}**")

    # 闸门上限
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
        from backend.services import lane_registry as reg
        p = ((reg.get_lane("mm_asterdex") or {}).get("meta") or {}).get("params") or {}
    except Exception:
        p = {}
    g_cap = float(p.get("max_gross_notional_ratio") or 0) * eq
    n_cap = float(p.get("max_net_exposure_ratio") or 0) * eq
    print(f"\n  ── 当前闸门 ──")
    print(f"    max_gross_notional_ratio = {p.get('max_gross_notional_ratio')}"
          f"  ⇒ ${g_cap:,.2f}   使用率 {gross/max(g_cap,1e-9)*100:.1f}%")
    print(f"    max_net_exposure_ratio   = {p.get('max_net_exposure_ratio')}"
          f"  ⇒ ${n_cap:,.2f}   使用率 {abs(net)/max(n_cap,1e-9)*100:.1f}%")

    sk = j.get("skip_counts") or {}
    print(f"\n  ── 闸门触发（本进程）──")
    for k in ("net_exposure", "gross_exposure", "symbol_exposure", "vol_pause"):
        if k in sk:
            print(f"    {k:<20} {sk[k]}")

    print("\n" + "=" * 92)
    print("判据")
    print("=" * 92)
    if ratio < 0.5:
        print(f"\n  |net|/gross = {ratio:.3f} < 0.5 ⇒ **内部对冲良好**")
        print(f"     ⇒ gross 可放宽；net 上限应保持（它才是真实方向性风险）")
    elif ratio > 0.8:
        print(f"\n  |net|/gross = {ratio:.3f} > 0.8 ⇒ **组合基本单向**")
        print(f"     ⇒ **不动参数**，且 4x equity 的净敞口在 $300 账户上偏高")
        print(f"     ⇒ 应考虑**收缩**（降低 max_net_exposure_ratio）")
    else:
        print(f"\n  |net|/gross = {ratio:.3f}（0.5~0.8）⇒ 部分对冲")
        print(f"     ⇒ 谨慎处理，先观察再动")

    print(f"\n  ⚠️ 关键认识：**持有变久 ⇒ 同时持仓变多 ⇒ 净敞口闸更容易成为瓶颈**。")
    print(f"     这不是 F291 的 bug，而是它的**必然代价** —— 必须与敞口上限一起调。")
    print(f"     `skip_counts` 里 net_exposure 的次数就是「被这个代价挡掉多少报价」的量度。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
