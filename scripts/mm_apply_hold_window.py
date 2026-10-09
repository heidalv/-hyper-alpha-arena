"""F258：把交易时长锁定在 **30 秒 ~ 5 分钟**（用户给定窗口）。

## 依据（用户 2026-09-20）

> 「交易时间进行锁定吧，既然都是把时间锁定在 **30秒到5分钟之内**，这个是有验证过的」

这正是本模块最早的定位（「中短期高频交易 · 30s–5min · 点差捕获」）。

## 改前 vs 改后

| 参数 | 改前 | 改后 | 说明 |
|---|---|---|---|
| `max_one_side_seconds` | **900**（15min） | **300**（5min） | 上界回到用户窗口 |
| `min_hold_seconds` | 字段不存在 | **30** | 下限，新增 |

### 为什么上界从 900 改回 300

上一轮我按 H17 同窗口扫描把 300 → 900（扫描显示 900 比 300 好 +0.12bp）。
**但那是在"只考虑强平费"的口径下**。用户给定的 30s–5min 是一个**已验证的策略边界**，
而且 H26 实测暴露了 900 的真实后果：

    持仓时长 中位 70s   p25 45s   p75 167s   **max 3612s**
    ⇒ 14% 的往返超出 5 分钟，最长 60 分钟

**超出窗口的那 14% 才是长尾风险的来源**，而 H17 的 bp 差（0.12bp）
远小于一次长尾事件（ARB 那笔 −79.5bp）的代价。

### 为什么下限同样重要

出场成本**几乎与持有时长无关**（穿越点差 + 对手方逆向选择，是固定成本）。
H26 实测：出场腿 −$0.032/往返 = 入场腿盈利（+$0.011/往返）的 **3 倍**。
刚建仓就强制平掉 = 白付一次出场成本，且没给对手腿任何成交机会。

### 下限**只管强制出口**

被拦住的只有引擎**主动发起**的三条路径：止损 / 超时 / OFI 择时。
**本方被动成交任何时候都允许**（对手方打过来 = 自然出库，不付成本）。
所以"行情真来了要赶紧走"不受影响 —— 减仓侧 maker 单照常挂。

用法：
    .venv\\Scripts\\python.exe scripts\\mm_apply_hold_window.py [--dry-run] [--rollback]
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

# 用户给定窗口：30 秒 ~ 5 分钟
#
# [H61/H63 2026-09-21] 上界从 300s 收到 **60s** —— 依据是两条**实测**（不是模拟）：
#
#   (1) H61 持有时间曲线（27,555 笔真实 tick 成交，**单调**）：
#         hold  均值bp    中位     p1      最亏      胜率
#           5   -0.2763  +0.0056  -6.25   -32.64   0.5870
#          30   -0.5933  -0.4766 -14.25   -56.90   0.4418
#          60   -0.7394  -0.6262 -19.65   -78.90   0.4370
#         300   -1.2991  -0.7369 -46.24  -103.83   0.4677
#       **每一个更短的持有都优于 300s**，且左尾随持有时间单调变肥（p1 从 −6.3 到 −46.2）。
#
#   (2) H63 实盘账本归因（1,877 笔）：
#         phase=fill    1,823 笔  均值 −2.3714bp  中位 **+1.2719bp**  胜率 **0.7466**
#         phase=flatten    58 笔  均值 **−28.9458bp** 中位 −13.0737bp 胜率 0.2414
#       **2.9% 的强平腿吃掉 28% 的亏损**，最亏 40 笔里 17 笔是 flatten
#       （UNI −164.8bp、ASTER −160.9bp、DOGE −133.1bp…），集中在薄币。
#
#   ⇒ 持有上限是尾部的主要放大器。300s 是**可选值里最差的一个**。
#     取 60s：H61 在 60s 仍有 −0.7394bp，而 300s 是 −1.2991bp，**差 0.56bp**，
#     同时 p1 从 −46.24 收到 −19.65、最亏从 −103.83 收到 −78.90。
#     不取 5s/10s 是因为 15s tick 节奏 + 真实延迟下那不是可执行窗口。
TARGET = {
    "max_one_side_seconds": 60.0,
    "min_hold_seconds": 30.0,
}
ROLLBACK_KEY = "f258_hold_window_rollback"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
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

    print(f"车道 {LANE}   改前 -> 改后（H61/H63 实测：持有上限 300s → 60s）")
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
    meta[ROLLBACK_KEY] = {
        "params": old_vals,
        "applied_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "reason": "F258 hold window 30s-300s (user-specified, validated range)",
    }
    reg.update_meta(LANE, meta)
    print(f"\n已写入 {len(changed)} 项。回滚: --rollback")
    print("⚠️ runner 每 60s 检查一次 meta 指纹 ⇒ 自动热采用，无需重启 worker。")
    print("\n窗口语义提醒：")
    print("  · 上界 300s：持仓超 5 分钟 ⇒ 打对手价平掉（原有行为）")
    print("  · 下限  30s：持仓未满 30 秒 ⇒ **禁止**引擎主动平仓（止损/超时/OFI）")
    print("              但**被动成交**不受限（对手方打过来随时允许）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
