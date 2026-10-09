"""按 H16 挂宽扫描结果修正 mm_asterdex 的报价参数。

## 为什么必须改（现场证据）

车道 24h 只有 **2 笔**成交，而报价决策 **3964 次**（成交率 0.05%）。
`H16` 在 4h 真实 tick 上扫挂宽，结论是决定性的：

    width   成交率    成交数   mk5s    净/决策
      0.0   0.3639    36281   +2.69   -0.110
      1.0   0.0331     3298   +2.70   +0.029   <-- 推荐
      2.0   0.0100     1000   +2.85   +0.038
      5.0   0.0023      232   +8.58   +0.047
     30.0   0.0000        0     nan      nan   <-- 当前配置：**成交数为 0**

⇒ 挂宽 30bp 时**物理上不可能成交**（实际半价差只有 0.59–9.5bp，报价在盘口外 40–50 倍）。

## 同时修正的陈旧值

`meta.params` 是**增量覆盖**，停在 09-17 口径，与 `.env` 意图及研究结论都不一致：

    w_base_bp             30.0  ->  1.5     （30 挂盘口外 40-50 倍，成交为 0）
    min_width_bp           3.0  ->  0.3     （3.0 让"贴盘口"根本做不到）
    min_width_reduce_bp    6.0  ->  0.0     （减仓侧要能贴盘口，否则只能等超时 taker 平）
    max_one_side_seconds  3600  ->  300     （.env 意图 300；1 小时单边持仓过长）
    k_vol_sigma_cap       None  ->  2.0     （σ 无上限会把挂宽推到天外）
    side_mode      counter_trend -> both    （趋势闸已单独管方向；两层叠加会 87% 只挂单边）

## 明确不动的地方

    stop_loss_bp = 0（关闭价格止损）—— 研究结论：价格止损劣于时间闸，
                   且旧设计 100% 亏损来自止损后的 taker 平仓。**保持 0**。
    trend_pause_bp = 15（保留趋势闸，只封顺势侧）
    compound_ratio = 0.1 ⇒ 单腿 $30（$300 的 10%）

## 用法

    python scripts/mm_apply_h16_params.py --dry-run
    python scripts/mm_apply_h16_params.py
    python scripts/mm_apply_h16_params.py --rollback      # 回到改动前
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

from backend.services import lane_registry as reg  # noqa: E402

LANE = "mm_asterdex"
# 目标值（每一项都在 docstring 里给了依据）
TARGET = {
    "w_base_bp": 1.5,
    "min_width_bp": 0.3,
    "min_width_reduce_bp": 0.0,
    "max_one_side_seconds": 300.0,
    "k_vol_sigma_cap": 2.0,
    "side_mode": "both",
}


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
        prev = (meta.get("h16_rollback") or {}).get("params")
        if not prev:
            print("没有可回滚的记录（h16_rollback.params 不存在）")
            return 1
        params.update(prev)
        meta["params"] = params
        meta["h16_rollback"] = {}
        reg.update_meta(LANE, meta)
        print(f"已回滚 {len(prev)} 个参数: {json.dumps(prev, ensure_ascii=False)}")
        return 0

    print(f"车道 {LANE}  当前 -> 目标")
    changed = {}
    old_vals = {}
    for k, v in TARGET.items():
        cur = params.get(k)
        mark = "" if cur == v else "   <-- 改"
        print(f"  {k:<24} {str(cur):>10} -> {str(v):>8}{mark}")
        if cur != v:
            changed[k] = v
            old_vals[k] = cur

    if not changed:
        print("\n已是最新，无需改动")
        return 0
    if args.dry_run:
        print(f"\n（dry-run）将改 {len(changed)} 项，未写入")
        return 0

    params.update(changed)
    meta["params"] = params
    meta["h16_rollback"] = {"params": old_vals,
                            "at": datetime.now(timezone.utc).isoformat()}
    meta["ops_changes"] = (meta.get("ops_changes") or []) + [{
        "by": "agent(F250/H16)",
        "ts": datetime.now(timezone.utc).isoformat(),
        "reason": ("挂宽扫描（4h 真实 tick，7 币）证明 w_base_bp=30 的成交数为 **0**"
                   "（报价在盘口外 40-50 倍）；同步修正 meta.params 里停留在 09-17 "
                   "口径的 min_width/max_one_side/k_vol_sigma_cap/side_mode。"
                   "stop_loss_bp 保持 0（价格止损劣于时间闸，见研究结论）。"),
        "changes": {k: {"from": old_vals[k], "to": v} for k, v in changed.items()},
    }]
    ok = reg.update_meta(LANE, meta)
    print(f"\n{'已写入' if ok else '写入失败'}: {json.dumps(changed, ensure_ascii=False)}")
    print("回滚: python scripts/mm_apply_h16_params.py --rollback")
    print("生效: 需 worker 重读注册表（下一个 tick 或重启 worker）")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
