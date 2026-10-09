"""当前敞口 vs 风控上限（一屏看清车道被什么卡住）。

背景（2026-09-20 19:03）：
  `max_gross_notional_ratio = 1.0` × 权益 $300 ⇒ 总敞口上限 **$300**。
  单腿名义 = 权益 × compound_ratio(0.1) = $30 ⇒ 上限只容得下 **10 条腿**。
  实测 10 个币已顶满（$318），加仓腿全被 `gross_exposure` 拒（日志 skip 计数第一）。

输出三个口径，与 `core.check_side_allowed` 的判据一一对应：
    gross  = Σ|qty × mid|                 vs  equity × max_gross_notional_ratio
    net    = |Σ qty × mid|                vs  equity × max_net_exposure_ratio
    symbol = |qty_i × mid_i| + add        vs  equity × max_net_directional_ratio

用法：
    .venv\\Scripts\\python.exe scripts\\diag_hft_exposure_budget.py
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

load_dotenv(ROOT / ".env")

STATUS = ROOT / "logs" / "mm_lane_status.json"

# `lane_registry.meta.params` 的目标值（读注册表，缺省用当前实测值）
DEFAULTS = {
    "max_gross_notional_ratio": 1.0,
    "max_net_exposure_ratio": 0.6,
    "max_net_directional_ratio": 0.3,
    "compound_ratio": 0.1,
}


def main() -> int:
    if not STATUS.exists():
        print(f"缺少 {STATUS}（车道未运行？）")
        return 1
    snap = json.loads(STATUS.read_text(encoding="utf-8"))

    equity = float(snap.get("equity") or 0.0)
    fill_notional = float(snap.get("fill_notional") or 0.0)
    states = dict(snap.get("states") or {})

    # 尝试从注册表读权威 limits（失败则用 DEFAULTS）
    lim = dict(DEFAULTS)
    try:
        from backend.services import lane_registry as reg  # type: ignore

        lane = reg.get_lane(os.getenv("MM_LANE_ID", "mm_asterdex"))
        params = dict((lane or {}).get("meta", {}).get("params") or {})
        for k in DEFAULTS:
            if params.get(k) is not None:
                lim[k] = float(params[k])
    except Exception as e:  # pragma: no cover
        print(f"(注册表读取失败，用默认上限: {e})")

    print(f"权益 equity          = ${equity:,.2f}")
    print(f"单腿名义 fill_notional = ${fill_notional:,.2f}  (compound_ratio={lim['compound_ratio']})")
    print()

    rows = []
    gross = 0.0
    net_signed = 0.0
    for sym, st in states.items():
        qty = float(st.get("qty") or 0.0)
        mid = float(st.get("quote_mid") or 0.0)
        if abs(qty) < 1e-12 or mid <= 0:
            continue
        n = qty * mid
        gross += abs(n)
        net_signed += n
        rows.append((sym, qty, mid, n, st))

    rows.sort(key=lambda r: -abs(r[3]))
    print("%-10s %16s %12s %12s %8s" % ("symbol", "qty", "mid", "notional$", "方向"))
    for sym, qty, mid, n, _ in rows:
        print("%-10s %16.6f %12.6f %+12.2f %8s" % (sym, qty, mid, n, "多" if n > 0 else "空"))
    print("%-10s %16s %12s %+12.2f" % ("合计", "", "", net_signed))
    print()

    # ── 三个口径 vs 上限 ──
    cap_gross = equity * lim["max_gross_notional_ratio"]
    cap_net = equity * lim["max_net_exposure_ratio"]
    cap_dir = equity * lim["max_net_directional_ratio"]

    print("口径            当前值        上限      余量     单腿(含新腿)")
    print("总敞口 gross   %10.2f %10.2f %9.2f   %s"
          % (gross, cap_gross, cap_gross - gross,
             "已满 ✗" if gross + fill_notional > cap_gross else "可加 ✓"))
    print("净敞口 net     %10.2f %10.2f %9.2f   %s"
          % (abs(net_signed), cap_net, cap_net - abs(net_signed),
             "已满 ✗" if abs(net_signed) + fill_notional > cap_net else "可加 ✓"))
    print("单币最大 symbol %9.2f %10.2f %9.2f"
          % (max((abs(r[3]) for r in rows), default=0.0), cap_dir,
             cap_dir - max((abs(r[3]) for r in rows), default=0.0)))
    print()

    print("── 结论 ──")
    legs_max = int(cap_gross // fill_notional) if fill_notional > 0 else 0
    legs_now = len(rows)
    print("上限容量 ≈ %d 条腿（$%.0f / $%.0f）；当前持仓 %d 个币"
          % (legs_max, cap_gross, fill_notional, legs_now))
    if legs_now >= legs_max:
        print("⇒ **敞口已顶满**，加仓腿会被 `gross_exposure` 全部拒绝，只剩减仓腿能挂。")
        print("  此时车道能做的事只有『等库存被对手腿吃掉』或『超时 taker 强平』。")
    else:
        print("⇒ 尚有 %d 条腿余量。" % (legs_max - legs_now))

    sc = snap.get("skip_counts") or {}
    if sc:
        tot = sum(int(v) for v in sc.values()) or 1
        print("\n── 本进程跳单原因（占比）──")
        for k, v in sorted(sc.items(), key=lambda kv: -kv[1])[:8]:
            print("   %-20s %5d  %5.1f%%" % (k, v, 100.0 * int(v) / tot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
