"""H98：把 max_one_side_seconds 从 60s 放宽到 300s —— 用耐心挂单替代 taker 强平。

# 依据链（全部实测，无模拟）

**H94 完整成本桥（残差 0.0%）**：
```
① 入场腿 pnl   +0.102 bp/周期    ← 赚
③ 强平腿 pnl   −1.825 bp/周期
④ 强平腿 fee   −1.014 bp/周期    ← taker
合计           −2.737 bp/周期
```

**H95 门槛**：打平需强平率 ≤ **0.84%**（当前 23.3%）；
且单次强平 12.2bp 中 **4.36bp 是 taker 费，消不掉**
⇒ 只要还用 taker 强平，**永远打不平**。

**H96/H97 出库能力**：
```
纯被动出库率 **76.7%**，且出库很快：
   ≤30s 59.9% | ≤60s 84.6% | **≤120s 98.5%** | ≤300s 99.6%
```

**H97 成本对照（决定性）**：
```
60s 市价强平  = taker 4.36bp + 穿越 7.85bp
死等到 300s   = 漂移从 −0.3018 恶化到 −0.9895bp ⇒ **仅多付 0.69bp**
⇒ **「多等」比「市价砸出去」便宜 +3.67bp/次**
```

# 预计效果

```
强平率   23.3% → 估 ~1.5%（用被动出库时长分布外推：≤300s 覆盖 99.6%）
每周期   −2.737bp → 估 +0.10 − 1.5%×12.2 ≈ **−0.08bp**  （接近打平）
```

**⚠️ 这是外推估算，不是实测** ⇒ 必须上线后实测确认。

# 风险（不软化）

  · 持仓变久 ⇒ **库存占用上升**（不再 60s 清掉）
    ⇒ 但 99.6% 在 300s 内被动出库 ⇒ 敞口有界
  · 用户给的窗口是 **30s~5min** ⇒ 300s **正好是上限**，符合约束 ✓
  · 若市场单边走，持仓会被带到 300s 才平 ⇒ 逆选择 0.69bp（已计入）
  · **回滚一条命令**

用法：
    .venv\\Scripts\\python.exe scripts\\mm_apply_hold_300.py --dry-run
    .venv\\Scripts\\python.exe scripts\\mm_apply_hold_300.py
    .venv\\Scripts\\python.exe scripts\\mm_apply_hold_300.py --rollback
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
ROLLBACK_KEY = "f291_hold_300_rollback"
TARGET = {
    "max_one_side_seconds": 300.0,   # 60 → 300（用户给定窗口的上限）
    "min_hold_seconds": 30.0,        # 不变
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--seconds", type=float, default=300.0)
    a = ap.parse_args()
    TARGET["max_one_side_seconds"] = float(a.seconds)

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

    print("=" * 90)
    print(f"F291 放宽持有上限：耐心挂单替代 taker 强平（车道 {LANE}）")
    print("=" * 90)
    changed, old_vals = {}, {}
    for k, v in TARGET.items():
        cur = params.get(k); old_vals[k] = cur
        flag = "" if cur == v else "  ← 改"
        print(f"  {k:<26} {str(cur):>10} -> {str(v):>10}{flag}")
        if cur != v:
            params[k] = v; changed[k] = v

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
        "reason": ("F291 hold 60->%g s: H94/H95 show taker flatten cost 4.36bp is "
                   "structural and unfixable; H97 shows waiting to 300s costs only "
                   "0.69bp extra adverse drift => +3.67bp/flatten saved" % a.seconds),
    }
    reg.update_meta(LANE, meta)
    print(f"\n已写入 {len(changed)} 项。回滚: --rollback")
    print("\n⚠️ runner 每 60s 热采用 ⇒ 通常无需重启；但建议重启一次并验证。")
    print("\n**上线后必看**（用 h93/h80 按周期口径实测）：")
    print("   · 强平率（目标 23.3% → <5%）")
    print("   · 每周期完整口径 pnl+fee（目标 −2.737bp → 接近 0）")
    print("   · 库存占用是否失控（states 里的 qty）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
