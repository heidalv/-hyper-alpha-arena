"""验证：runner 是否真的热采用了新的持有窗口（F189 教训：改了但没生效）。

不信任"注册表写了就会生效"，直接构造 runner 读它的 limits 实况。
"""
from __future__ import annotations

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

LANE = os.getenv("MM_LANE_ID", "mm_asterdex")


def main():
    from backend.services import lane_registry as reg
    from backend.services.market_maker.runner import get_runner

    lane = reg.get_lane(LANE)
    stored = dict((lane.get("meta") or {}).get("params") or {})
    print("=" * 78)
    print("注册表里的值（源头）")
    print("=" * 78)
    for k in ("max_one_side_seconds", "min_hold_seconds", "stop_loss_bp",
              "spread_mult", "spread_cross_margin", "w_base_bp", "min_width_bp"):
        print(f"  {k:<26} {stored.get(k)!r}")

    r = get_runner(LANE)
    if r is None:
        print("\n⚠️ get_runner 返回 None —— 无法在此进程构造 runner（需 worker 上下文）")
        return 1
    print("\n" + "=" * 78)
    print("runner 实际生效的值（消费端）")
    print("=" * 78)
    lim = r.limits
    par = r.params
    ok = True
    # ⚠️ 这些"期望值"必须跟着**当前决策**更新，否则报告会误导
    #    （实测过一次：F291 把 max_one_side 60→120 后，本脚本仍报"未生效"。
    #      H94/H95 证明 taker 强平成本 4.36bp 是结构性的、消不掉，
    #      H97 证明"多等到 120s"只多付 ~0.3bp 漂移 ⇒ 放宽上限是有依据的决策）
    # 证据：H94 成本桥 / H95 打平门槛 / H97 被动出库时长分布
    for k, want in (("max_one_side_seconds", 120.0), ("min_hold_seconds", 30.0)):
        got = getattr(lim, k, None)
        flag = "✓" if got == want else "✗ 与当前决策不符"
        if got != want:
            ok = False
        print(f"  limits.{k:<22} {got!r}   期望 {want!r}  {flag}")
    for k, want in (("spread_mult", 0.9), ("spread_cross_margin", 0.05)):
        got = getattr(par, k, None)
        flag = "✓" if got == want else "✗ 未生效"
        if got != want:
            ok = False
        print(f"  params.{k:<22} {got!r}   期望 {want!r}  {flag}")

    # ── F282 杠杆：compound_ratio → fill_notional = ratio × equity ──
    _cr = getattr(par, "compound_ratio", None)
    print(f"\n  params.compound_ratio        {_cr!r}   （F282 杠杆）")
    try:
        import json as _j
        sf = ROOT / "logs" / "mm_lane_status.json"
        if sf.exists():
            _s = _j.loads(sf.read_text(encoding="utf-8"))
            _fn = float(_s.get("fill_notional") or 0)
            _eq = float(_s.get("equity") or 0)
            print(f"  实盘 fill_notional           ${_fn:,.2f}"
                  f"   （equity ${_eq:,.2f} × {_cr} = ${(_cr or 0)*_eq:,.2f}）")
            if _cr and _eq and abs(_fn - _cr * _eq) > max(1.0, _cr * _eq * 0.05):
                print(f"  ✗ fill_notional 与 compound_ratio × equity 不符 ⇒ 杠杆可能未生效")
                ok = False
            elif _cr:
                print(f"  ✓ 杠杆已生效（腿量 {_fn / max(1.0, 0.1 * _eq):.1f}x 于 compound_ratio=0.1）")
    except Exception as _e:
        print(f"  （读 status 失败：{_e}）")
    # 承销参数只做展示，不参与 ok（它们随杠杆档位变化）
    print(f"\n  limits.max_gross_notional_ratio  {getattr(lim, 'max_gross_notional_ratio', None)!r}")
    print(f"  limits.daily_loss_stop_pct       {getattr(lim, 'daily_loss_stop_pct', None)!r}"
          f"   ← 唯一的硬停机闸，必须 >0")

    # ── [F293 2026-09-21] **三方一致性校验：注册表 / env / 消费端** ──────────
    # 教训 65：F280 引入"env 显式优先"后，F287 把值写进 .env，
    # 导致 F292 只改注册表**静默无效**（消费端仍读旧值，重启也没用）✗
    # ⇒ 任何"同时存在于两个来源"的参数，改完必须校验三方一致。
    import os as _os

    print("\n  ── 三方一致性（注册表 / .env / 消费端）──")
    lane = reg.get_lane(LANE) if "reg" in dir() else None
    stored = {}
    try:
        from backend.services import lane_registry as _reg
        stored = dict(((_reg.get_lane(LANE) or {}).get("meta") or {}).get("params") or {})
    except Exception:
        pass
    _pairs = (("spread_mult", "MM_SPREAD_MULT"),
              ("spread_mult_reduce", "MM_SPREAD_MULT_REDUCE"),
              ("spread_cross_margin", "MM_SPREAD_CROSS_MARGIN"),
              ("min_edge_frac", "MM_MIN_EDGE_FRAC"))
    print(f"  {'参数':<24} {'注册表':>10} {'env':>10} {'消费端':>10}")
    print("  " + "-" * 58)
    mismatch = []
    for k, e in _pairs:
        rv, ev, cv = stored.get(k), _os.getenv(e), getattr(par, k, None)
        print(f"  {k:<24} {str(rv):>10} {str(ev):>10} {str(cv):>10}")
        # 只在 env 与注册表**都有值且不同**时告警（那正是静默失效的成因）
        if rv is not None and ev not in (None, "") and cv is not None:
            try:
                if abs(float(rv) - float(cv)) > 1e-9:
                    mismatch.append(f"{k}(注册表{rv}≠消费端{cv})")
            except Exception:
                pass
    if mismatch:
        ok = False
        print(f"\n  ✗ **参数不一致**：{mismatch}")
        print(f"     ⇒ env 覆盖了注册表（F280 的显式优先机制）")
        print(f"     ⇒ 修法：把 `mm_apply_*.py` 的改动**同时写 .env**，或删掉 .env 里那一行")
    else:
        print("\n  ⇒ 三方一致 ✓")

    print("\n" + "=" * 78)
    print("结论：" + ("全部生效 ✓" if ok else "**有未生效项** ✗ —— 需查热采用链路"))
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
