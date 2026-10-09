"""F280：把挂宽从「绝对 bp」改成「价差倍数」—— 修复实盘每笔 −3.24bp 的主因之一。

## 依据（可复现的实测链）

| 步骤 | 脚本 | 结论 |
|---|---|---|
| 1. 真实价差 | `h53b_diag_book_ticker_spread.py` | BTC 价差 **0.0124bp**（$0.10/80k），book_ticker 与 20 档 depth 快照**交叉核对一致** |
| 2. 价差跨度 | `h55_spread_scaled_width.py` | Aster 32 币价差 p50 从 0.0124bp 到 20.19bp ⇒ **1629 倍**；同一宽度下 13 币可竞争、**16 币太贴、3 币太远** |
| 3. δ 单位错 | `h54_delta_crossing_check.py` | δ 用绝对 bp 时 BTC 上穿越率 **86.89%** ⇒ H52 的「进价差内 +0.39bp」是**穿越伪影**，已撤回 |
| 4. 反事实对拍 | `h56_width_mode_counterfactual.py` | 6 币**全部**改善，跨币均值 **−1.4167 → −0.7792bp**（+0.638bp），穿越率 0% |

## 改什么

| 项 | 改前 | 改后 |
|---|---|---|
| 挂宽口径 | `w = max(min_width_bp=3.0, w_base_bp=1.425)` 绝对 bp | `w = spread_mult × 半价差`（按币自适应） |
| spread_mult | 不存在（旧路径） | **0.9** |
| 不穿越钳制 | 无（报价可越过对侧最优价） | `spread_cross_margin=0.05` 硬钳制 |

`spread_mult=0.9 < 1` 的物理含义：报价**进到价差内侧 10%** ⇒
我们**就是**最优价 ⇒ 在一个**新建的队列**里位于**队首（QP≈0）**。
这是 15 秒节奏的参与者**唯一**能结构性拿到队首的机制（Arroyo QF 2024 Table 3：成交概率 8.2 倍）。

## 诚实声明（不许软化）

反事实显示：**改善后仍然全为负**（6/6 币），只是亏损速率减半。
**本改动不使策略盈利**，它是"止血"，不是"治病"。

用法：
    .venv\\Scripts\\python.exe scripts\\mm_apply_spread_mult.py --dry-run
    .venv\\Scripts\\python.exe scripts\\mm_apply_spread_mult.py
    .venv\\Scripts\\python.exe scripts\\mm_apply_spread_mult.py --rollback
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
TARGET = {
    "spread_mult": 0.9,
    "spread_cross_margin": 0.05,
}
ROLLBACK_KEY = "f280_spread_mult_rollback"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--spread-mult", type=float, default=0.9)
    args = ap.parse_args()
    TARGET["spread_mult"] = float(args.spread_mult)

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
        for k, v in prev.items():
            if v is None:
                params.pop(k, None)
            else:
                params[k] = v
        meta["params"] = params
        meta[ROLLBACK_KEY] = {}
        reg.update_meta(LANE, meta)
        print("已回滚:" + json.dumps(prev, ensure_ascii=False))
        return 0

    print(f"车道 {LANE}   F280 挂宽口径改造")
    print(f"  改前 params: spread_mult={params.get('spread_mult')!r} "
          f"spread_cross_margin={params.get('spread_cross_margin')!r} "
          f"w_base_bp={params.get('w_base_bp')!r} min_width_bp={params.get('min_width_bp')!r}")
    changed, old_vals = {}, {}
    for k, v in TARGET.items():
        cur = params.get(k)
        old_vals[k] = cur
        if cur == v:
            print(f"  {k:<22} = {v}  （已是目标值，跳过）")
            continue
        print(f"  {k:<22} {cur} -> {v}")
        params[k] = v
        changed[k] = v

    if not changed:
        print("无需改动。")
        return 0
    if args.dry_run:
        print("\n(--dry-run，未写入)")
        return 0

    meta["params"] = params
    meta[ROLLBACK_KEY] = {
        "params": old_vals,
        "applied_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "reason": "F280 spread-relative width; evidence: h53b/h54/h55/h56",
    }
    reg.update_meta(LANE, meta)
    print(f"\n已写入 {len(changed)} 项。回滚: --rollback")

    # 同步 .env（新增的键用 **ASCII 追加**，不触碰既有内容 —— 该文件混编码，
    # 改写会破坏中文注释）。`apply_env_param_overrides` 让 env 显式优先，
    # 否则一旦 registry 里出现同名 key，dataclass 的 os.getenv 默认值不会求值 ✗。
    env_path = ROOT / ".env"
    try:
        txt = env_path.read_text(encoding="utf-8", errors="replace")
        adds = []
        if "MM_SPREAD_MULT=" not in txt:
            adds.append("MM_SPREAD_MULT=%.4g" % float(TARGET["spread_mult"]))
        if "MM_SPREAD_CROSS_MARGIN=" not in txt:
            adds.append("MM_SPREAD_CROSS_MARGIN=%.4g" % float(TARGET["spread_cross_margin"]))
        if adds:
            with env_path.open("a", encoding="utf-8") as f:
                f.write("\n# [F280 2026-09-21] spread-relative quote width"
                        " (evidence: h53b/h54/h55/h56); <=0 = legacy absolute-bp path\n")
                f.write("\n".join(adds) + "\n")
            print(f"已追加到 .env: {', '.join(adds)}")
        else:
            print("(.env 已含 MM_SPREAD_MULT / MM_SPREAD_CROSS_MARGIN，未改动)")
    except Exception as e:
        print(f"⚠️ .env 写入失败（需手工添加 MM_SPREAD_MULT）：{e}")

    print("\n⚠️ **worker 必须重启** —— 当前进程加载的是改前的 core.py/runner.py")
    print("   （core.py 改于 23:54:15、runner.py 改于 23:56:17，进程启动更早）。")
    print("\n⚠️ 诚实声明：反事实显示改动后 6/6 币**仍为负**，只是亏损速率减半")
    print("   （跨币均值 −1.4167 → −0.7792bp）。这是止血，不是治病。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
