"""F282：加杠杆放大本金 —— 用户要求「加杠杆测试，放大本金，不要怕亏」。

## 必须先说清楚的机制（这是数学，不是保守）

`runner.py:2358`：

    self.fill_notional = max(10.0, compound_ratio * self.equity)

⇒ 杠杆是**线性缩放因子**。设 R = 策略的 bp 边际（与仓位大小无关），则：

    每笔盈亏(USD) = R/1e4 × fill_notional = R/1e4 × (compound_ratio × equity)

**⇒ 杠杆把美元盈亏按比例放大，但完全不改变 bp 边际。**

这意味着：
  · 策略**负边际**时，杠杆 = 等比例**加速亏损**，且因为 compound 联动，
    权益下降会同步缩小腿量（**亏损是复利的**，比线性更快触及 10% 日亏闸）
  · 只有在边际转正后，杠杆才产生正收益

**那为什么还值得做**：用户要的是**信息**，而样本积累速度与腿量无关、
只与"每 tick 挂单数"有关 ⇒ **杠杆不会加快学习**（这点我一开始想错了，如实纠正）。
真正的收益是：**在同样的观测时间内，美元盈亏的信噪比更高**，
且能验证"大仓位下的队列冲击"（$28 的腿在 Aster 上是尘埃，$140 才可能有真实排队效应）。

## 改什么

| 参数 | 改前 | 改后 | 含义 |
|---|---|---|---|
| `compound_ratio` | 0.1 | **0.5** | 每笔腿名义 = 50% 权益（$280 → $140/腿） |
| `max_gross_notional_ratio` | 1.0 | **4.0** | 组合总名义上限 = 4 × 权益（10 币 × $140 × 2 腿 = $2800） |
| `max_net_exposure_ratio` | 0.6 | **2.0** | 净敞口上限放宽（否则 2 个同向腿就被顶穿） |
| `max_net_directional_ratio` | 0.3 | **1.0** | 单币方向上限 |
| `daily_loss_stop_pct` | 10.0 | **25.0** | 日亏闸相应放宽（**但绝不关闭** —— 这是唯一的硬止损） |
| `stop_loss_bp` | 60.0 | **60.0 不变** | 尾部保护不动 |

**风险声明（写给用户，不软化）**：
  · 当前实测边际约 **−1.84bp/笔**（H69，lag=15s）。按此速率：
    每笔 ≈ −1.84/1e4 × $140 = **−$0.026**；约 20 笔/小时 ⇒ **−$0.51/小时**
  · 若边际维持负值，$280 权益约 **23 天**耗尽（且 compound 会让它更快）
  · 日亏闸 25% ⇒ 单日最大回撤约 **$70**，会**自动停机**
  · 本轮修复（F280 挂宽 / F281 新鲜 mid / 60s 持有）尚未验证是否把边际翻正
    ⇒ **这是一个"边跑边看"的实验，不是"已经能赚所以放大"**

用法：
    .venv\\Scripts\\python.exe scripts\\mm_apply_leverage.py --dry-run
    .venv\\Scripts\\python.exe scripts\\mm_apply_leverage.py --ratio 0.5
    .venv\\Scripts\\python.exe scripts\\mm_apply_leverage.py --rollback
"""
from __future__ import annotations

import argparse
import datetime
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

from backend.services import lane_registry as reg  # noqa: E402

LANE = os.getenv("MM_LANE_ID", "mm_asterdex")
ROLLBACK_KEY = "f282_leverage_rollback"


def build_target(ratio: float) -> dict:
    # ⚠️ 上限必须**真正容得下**目标仓位，否则敞口闸会把腿拦掉一半，
    # 名义放大不生效而"看起来改过了"（F189 那一类）。实算：
    #   10 币 × (ratio×equity) × 2 腿 = 20×ratio×equity
    #   ⇒ gross 上限至少 = 20×ratio + 余量 ⇒ ratio=0.5 时需 ≥10
    _need = 20.0 * float(ratio)
    return {
        "compound_ratio": float(ratio),
        "max_gross_notional_ratio": round(max(4.0, _need * 1.2), 2),
        "max_net_exposure_ratio": round(max(2.0, 8.0 * float(ratio)), 2),
        "max_net_directional_ratio": round(max(1.0, 2.0 * float(ratio)), 2),
        # 日亏闸：放宽到 25%，但**绝不设为 0/关闭**
        "daily_loss_stop_pct": 25.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--ratio", type=float, default=0.5,
                    help="compound_ratio（每笔腿名义 / 权益）。0.5 = 5x 于原 0.1")
    a = ap.parse_args()

    lane = reg.get_lane(LANE)
    if not lane:
        print(f"车道不存在: {LANE}")
        return 1
    meta = dict(lane.get("meta") or {})
    params = dict(meta.get("params") or {})

    if a.rollback:
        prev = (meta.get(ROLLBACK_KEY) or {}).get("params")
        if not prev:
            print(f"没有可回滚记录（{ROLLBACK_KEY}）")
            return 1
        for k, v in prev.items():
            if v is None:
                params.pop(k, None)
            else:
                params[k] = v
        meta["params"] = params
        meta[ROLLBACK_KEY] = {}
        reg.update_meta(LANE, meta)
        print("已回滚: " + json.dumps(prev, ensure_ascii=False))
        return 0

    target = build_target(a.ratio)
    print("=" * 88)
    print(f"F282 杠杆放大（车道 {LANE}）  compound_ratio={a.ratio}")
    print("=" * 88)
    print(f"\n  {'参数':<30} {'改前':>10} {'改后':>10}")
    print("  " + "-" * 54)
    changed, old_vals = {}, {}
    for k, v in target.items():
        cur = params.get(k)
        old_vals[k] = cur
        flag = "" if cur == v else "  ← 改"
        print(f"  {k:<30} {str(cur):>10} {str(v):>10}{flag}")
        if cur != v:
            params[k] = v
            changed[k] = v

    equity = 280.5
    try:
        sf = ROOT / "logs" / "mm_lane_status.json"
        if sf.exists():
            equity = float(json.loads(sf.read_text(encoding="utf-8")).get("equity") or equity)
    except Exception:
        pass
    leg = max(10.0, a.ratio * equity)
    print(f"\n  ⇒ 当前权益 ${equity:.2f} ⇒ 每笔腿名义 **${leg:.2f}**"
          f"（原 ${max(10.0, 0.1*equity):.2f}，放大 {leg/max(10.0,0.1*equity):.1f}x）")
    print(f"  ⇒ 组合总名义上限 ${target['max_gross_notional_ratio']*equity:,.0f}"
          f"  （10 币 × $ {leg:.0f} × 2 腿 = ${10*leg*2:,.0f}）")

    if not changed:
        print("\n无需改动。")
        return 0
    if a.dry_run:
        print("\n(--dry-run，未写入)")
        return 0

    meta["params"] = params
    meta[ROLLBACK_KEY] = {
        "params": old_vals,
        "applied_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "reason": f"F282 leverage {a.ratio} (user request: amplify capital, accept losses)",
    }
    reg.update_meta(LANE, meta)
    print(f"\n已写入 {len(changed)} 项。回滚: --rollback")
    print("\n⚠️ runner 每 60s 热采用 ⇒ 无需重启（但 `compound_ratio` 在 __init__ 读，")
    print("   若 status 的 fill_notional 未变化则需重启 worker）。")
    print("\n⚠️ 风险（不软化）：")
    print(f"   · 当前实测边际约 −1.84bp/笔（H69 lag=15s）")
    print(f"   · 按此速率每笔 ≈ −$ {abs(-1.84)/1e4*leg:.4f}，约 20 笔/小时 ⇒ −$ {abs(-1.84)/1e4*leg*20:.2f}/小时")
    print(f"   · 日亏闸 {target['daily_loss_stop_pct']:.0f}% ⇒ 单日最大回撤约 $ {equity*target['daily_loss_stop_pct']/100:.0f}，触发即自动停机")
    print(f"   · **杠杆不改变 bp 边际**，只等比例放大盈亏；边际为负时它加速亏损")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
