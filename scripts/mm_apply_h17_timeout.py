"""H17 应用：`max_one_side_seconds` 300 → 900（依据同窗口回放扫描）。

## 依据（`research_l1/out/h17_max_one_side_sweep.json`，2026-09-20 19:15）

同一段最近 4h 真实快照（10 币 × ~830 快照），只改超时上限，其余参数取
`lane_registry.meta.params` 的**线上真实值**（腿量 $30）：

    超时上限   成交   强平    净额      价差     逆选择   平仓费
    120s      2576   223   -2.247bp  +1.031  -2.661  -0.616
    300s      2242    89   -1.567bp  +1.200  -2.500  -0.266   ← 改前
    600s      2113    35   -1.661bp  +1.308  -2.863  -0.107
    900s      2089    24   -1.446bp  +1.335  -2.719  -0.062   ← 改后
    1800s     2068    15   -1.481bp  +1.351  -2.784  -0.048
    3600s     2059    11   -1.408bp  +1.365  -2.736  -0.037

## 为什么选 900s 而不是扫出来的最优 3600s

1. **900/1800/3600 三档净额差异 ≤0.07bp，在噪声内**（3600s 的 "-1.408 最优"
   并不比 900s 的 -1.446 显著）。挑 3600s 属于挑噪声。
2. **900s 是"平仓费已基本出清"的拐点**：费用项从 300s 的 -0.266bp 降到
   -0.062bp，再往后（1800s -0.048 / 3600s -0.037）边际收益 <0.03bp，
   却要多承担 2~4 倍的持仓风险暴露。
3. 120s 档的 -2.247bp 是最差的一档 ⇒ **超时上限绝不能调短**，这条边界很硬。

## 必须同时声明的**负面结论**（不得淡化）

**六档全部为负**。放宽超时改善的是"强平费"这一项，改善不了主体：
价差捕获只有 +1.2~1.37bp，而逆选择稳定在 -2.5~-2.8bp。
**逆选择是价差的约 2 倍**，这个结构在任何超时档位下都不变。
⇒ 本项改动是**减少一个已知出血点**，不是把负期望改成正期望。
真正的结论仍是：Aster 被动做市在现有挂宽下**无正期望证据**。

用法：
    .venv\\Scripts\\python.exe scripts\\mm_apply_h17_timeout.py [--dry-run] [--rollback]
"""
from __future__ import annotations

import argparse
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

# 只改这一项。刻意不动 stop_loss_bp（60bp）：它是尾部保险，且被
# `stop_loss_vol_min=1.0` 的波动闸挡住（实测 σ_norm≈0.06，本就没武装）。
TARGET = {
    "max_one_side_seconds": 900.0,
}
ROLLBACK_KEY = "h17_rollback"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true", help="撤销上一次本脚本的改动")
    args = ap.parse_args()

    lane = reg.get_lane(LANE)
    if not lane:
        print(f"车道不存在: {LANE}")
        return 1
    meta = dict(lane.get("meta") or {})
    params = dict(meta.get("params") or {})

    if args.rollback:
        prev = (meta.get(ROLLBACK_KEY) or {}).get("params")
        if not prev:
            print(f"没有可回滚的记录（{ROLLBACK_KEY}.params 不存在）")
            return 1
        params.update(prev)
        meta["params"] = params
        meta[ROLLBACK_KEY] = {}
        reg.update_meta(LANE, meta)
        print(f"已回滚 {len(prev)} 个参数: {json.dumps(prev, ensure_ascii=False)}")
        return 0

    print(f"车道 {LANE}   改前 -> 改后")
    changed, old_vals = {}, {}
    for k, v in TARGET.items():
        cur = params.get(k)
        old_vals[k] = cur
        if cur == v:
            print(f"  {k:<26} = {v}  （已是目标值，跳过）")
            continue
        print(f"  {k:<26} {cur} -> {v}")
        params[k] = v
        changed[k] = v

    if not changed:
        print("无需改动。")
        return 0

    if args.dry_run:
        print("\n(--dry-run，未写入)")
        return 0

    meta["params"] = params
    # 保留一次可回滚快照（与 mm_apply_h16_params.py 同一模式）
    meta[ROLLBACK_KEY] = {"params": old_vals, "applied_at": __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat(), "reason": "H17 timeout sweep"}
    reg.update_meta(LANE, meta)
    print(f"\n已写入 {len(changed)} 项。回滚: --rollback")
    print("⚠️ runner 每 60s 检查一次 meta 指纹，自动热采用；无需重启 worker。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
