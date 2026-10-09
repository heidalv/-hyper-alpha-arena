"""F292：把出库报价从 f=0.95 拉回更靠 mid（0.30）—— 修正 F287 的方向。

# 依据（H104，120 个含强平周期，300s 真实路径）

出库是"卖"，成交条件 = **主动买成交价 ≥ 我们的卖价 Q**，`Q = px + f×half`：

| f | 被动成交率 | 越过当时卖一的比例 |
|---|---|---|
| **0.00（挂 mid）** | **84.5%** | 10.0% |
| 0.50 | 81.0% | 12.5% |
| **0.95（当前 F287 值）** | **79.3%** | 13.3% |
| 1.20 | 75.9% | 16.7% |
| 2.00 | 66.4% | 25.8% |

**⇒ 出库挂得越靠外，成交率越低；最优是 f=0.00（挂 mid），比当前 0.95 高 +5.2pp。**

**⇒ F287 把 0.9 → 0.95 是**方向错了**（我是照 H77 的模拟做的，
   而 H77 的模拟已被证明在"挂单能否成交"上会退化）。**

# 为什么 f 越小越好（机制）

  · f 小 ⇒ Q 靠近 mid ⇒ 价格回到 mid 附近时我们就在那儿 ⇒ 容易成交
  · f 大 ⇒ Q 靠近卖一 ⇒ 只有卖一被推上去才轮到我们 ⇒ 反而难成交
  · 而 `f=0` 的"越过卖一比例"只有 10% ⇒ 绝大多数时候仍是合规的 maker 挂单

# 本次改动（保守半步，不直接跳到 0）

`spread_mult_reduce`：**0.95 → 0.30**

  · 预期被动成交率 ~82%（介于 84.5% 与 81.0% 之间）
  · 越界率 ~11%（略高于 f=0 的 10%，风险可控）
  · 不直接跳 0.00，是因为"越界"会让我们偶发变成 taker（付 4bp）——
    先走半步，用实盘测出"成交率提升"与"越界成本"的实际权衡

# 判据（事先定死，上线后测）

  · 强平率应下降（当前 10.6%）
  · **每周期完整口径（pnl+fee）应改善** —— 若恶化说明越界成本 > 成交率收益
  · 若恶化 ⇒ 回滚（`--rollback`）

用法：
    .venv\\Scripts\\python.exe scripts\\mm_apply_exit_near_mid.py --dry-run
    .venv\\Scripts\\python.exe scripts\\mm_apply_exit_near_mid.py
    .venv\\Scripts\\python\\python.exe scripts\\mm_apply_exit_near_mid.py --rollback
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
ROLLBACK_KEY = "f292_exit_near_mid_rollback"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--value", type=float, default=0.30)
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
    print("=" * 88)
    print(f"F292 出库报价拉回更靠 mid（车道 {LANE}）")
    print("=" * 88)
    print(f"\n  spread_mult_reduce：{old!r} -> {a.value!r}")
    print(f"  （0.0 = 挂 mid 成交率最高；0.95 = F287 的值，实测成交率低 5.2pp）")
    print(f"\n  依据 H104（120 个含强平周期，300s 真实路径）：")
    print(f"    f=0.00 成交率 84.5%   f=0.95 成交率 79.3%   f=2.00 成交率 66.4%")

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
        "reason": ("F292 exit quote nearer mid: H104 shows f=0 fills 84.5% vs "
                   "f=0.95 79.3% => F287 moved the wrong way"),
    }
    reg.update_meta(LANE, meta)
    print(f"\n已写入注册表。回滚: --rollback")

    # ── [F292 关键] **必须同时改 .env** ────────────────────────────────────
    # 坑（本轮实测踩到）：F280 的 `apply_env_param_overrides` 让 **env 显式优先**，
    # 而 F287 当年把 `MM_SPREAD_MULT_REDUCE=0.95` 写进了 `.env`
    # ⇒ 只改注册表**不生效**（消费端仍读 0.95），重启也没用 ✗✗
    # ⇒ 凡是这两个来源都存在的参数，改动必须**同时落两处**。
    env_path = ROOT / ".env"
    try:
        txt = env_path.read_text(encoding="utf-8", errors="replace")
        import re as _re
        new_line = f"MM_SPREAD_MULT_REDUCE={a.value:g}"
        if "MM_SPREAD_MULT_REDUCE=" in txt:
            txt2 = _re.sub(r"(?m)^MM_SPREAD_MULT_REDUCE=.*$", new_line, txt)
            env_path.write_text(txt2, encoding="utf-8")
            print(f"已同步 .env：{new_line}")
        else:
            with env_path.open("a", encoding="utf-8") as f:
                f.write(f"\n# [F292 2026-09-21] {new_line}\n")
            print(f"已追加 .env：{new_line}")
    except Exception as e:
        print(f"⚠️ .env 同步失败（**必须手工改，否则本改动不生效**）：{e}")

    print("\n⚠️ 需重启 worker 才能生效，并验证消费端读到新值。")
    print("\n上线后必看：")
    print("   · 强平率（当前 10.6%，应下降）")
    print("   · **每周期完整口径 pnl+fee**（应改善；若恶化说明越界成本过高）")
    print("   · 越界比例（若显著 >13% 说明挂得太靠外了）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
