"""F287：上线 F286 的减仓侧价差倍数（出库腿更靠近 mid）。

## 依据（H77，判据已过极端情形单调性测试）

出库报价 = `ask − f×价差` 时的**整往返**每笔净额（24h、21,527 笔入场、分母含强平）：

| f | 成交率(60s) | 每笔净额 |
|---|---|---|
| 0.00（挂 best_ask） | 78.8% | −1.7624bp |
| 0.50 | 80.9% | −1.7795bp |
| **1.00（挂 mid）** | **93.3%** | **−0.8398bp** |

⇒ 出库挂 mid 比挂 touch 好 **+0.92bp**；机制自洽（半价差≈0.9bp，改善≈0.9bp）。

引擎当前减仓侧用 `spread_mult=0.9` ⇒ 报价在 `bid+0.9h` ≈ 0.45×价差
⇒ 对应 H77 的 **f≈0.5** 行。`spread_mult_reduce=1.0` 才是 f=1（挂 mid）。

## 为什么先上 0.95 而不是直接 1.0

**我的模拟一贯比实盘保守约 1bp**（H77 的 f=1/hold60s 是 −0.84bp，而实盘 fill+flatten
合并约 −0.23bp）。既然模型的**绝对水平**不可信、只有**相对排序**可信，
就不应该一步跳到模型最优点 —— 先走半步并**用 F285 的往返标签实测**。

## 风险

  · 出库更靠 mid ⇒ **少赚价差**（坏）但**成交率更高、强平更少**（好）
  · H77 说净效果为正，但那是模拟；**实盘必须实测**
  · 回滚一条命令

用法：
    .venv\\Scripts\\python.exe scripts\\mm_apply_reduce_mult.py --dry-run
    .venv\\Scripts\\python.exe scripts\\mm_apply_reduce_mult.py --value 0.95
    .venv\\Scripts\\python.exe scripts\\mm_apply_reduce_mult.py --rollback
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
ROLLBACK_KEY = "f287_reduce_mult_rollback"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--value", type=float, default=0.95)
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

    old = params.get("spread_mult_reduce")
    print("=" * 84)
    print(f"F287 减仓侧价差倍数（车道 {LANE}）")
    print("=" * 84)
    print(f"  spread_mult（进场+默认） = {params.get('spread_mult')!r}")
    print(f"  spread_mult_reduce       {old!r} -> {a.value!r}")
    print(f"  （1.0 = 出库挂 mid；0.9 ≈ 当前行为）")
    if old == a.value:
        print("\n无需改动。")
        return 0
    if a.dry_run:
        print("\n(--dry-run，未写入)")
        return 0

    params["spread_mult_reduce"] = float(a.value)
    meta["params"] = params
    meta[ROLLBACK_KEY] = {
        "params": {"spread_mult_reduce": old},
        "applied_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "reason": f"F287 reduce-side spread mult = {a.value}; evidence H77 (f=1 best)",
    }
    reg.update_meta(LANE, meta)
    print("\n已写入。回滚: --rollback")

    # 同步 .env（ASCII 追加，不触碰既有中文注释）
    env_path = ROOT / ".env"
    try:
        txt = env_path.read_text(encoding="utf-8", errors="replace")
        if "MM_SPREAD_MULT_REDUCE=" not in txt:
            with env_path.open("a", encoding="utf-8") as f:
                f.write("\n# [F287 2026-09-21] reduce-side spread mult"
                        " (H77: exit at mid beats exit at touch by +0.92bp)\n")
                f.write("MM_SPREAD_MULT_REDUCE=%.4g\n" % a.value)
            print("已追加 MM_SPREAD_MULT_REDUCE 到 .env")
        else:
            print("(.env 已含 MM_SPREAD_MULT_REDUCE)")
    except Exception as e:
        print(f"⚠️ .env 写入失败（需手工添加）：{e}")

    print("\n⚠️ worker 每 60s 热采用 meta ⇒ 通常无需重启；")
    print("   但 `spread_mult_reduce` 是新增字段，**保险起见重启一次**并验证。")
    print("\n⚠️ 上线后必看：① 减仓侧宽度是否变大 ② 强平率是否下降")
    print("   （用 `scripts/h80_episode_pnl.py` 按往返口径核对）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
