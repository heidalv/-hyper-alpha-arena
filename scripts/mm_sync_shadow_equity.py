"""F290：把 `meta.shadow_equity` 从"配置的起始值"改成"引擎现算的活权益"。

# 为什么这条能立刻生效（不需要重启后端）

前端 `HftControlPanel.tsx:221` 显示 `paper.total_equity`，
而**当前已部署**的后端（启动于 02:53:57，早于我的 F289 改动）用
`meta.shadow_equity` 填充它 —— 实测该值是 **300.0（配置的起始权益）**，
不是活值 ⇒ 面板显示「总权益 $300.00 · 起始 $300.00」，
**掩盖了 −$29.3 的已实现亏损** ✗✗

而**引擎每 tick 都在写真正的活权益**到 `logs/mm_lane_status.json`
（实测 270.3）—— 只是没人把它写回注册表。

⇒ 本脚本把活权益写回 `meta.shadow_equity`，**立即修正看板**，
且**无需重启后端**（用的是已部署代码就读的字段）。

# 同时报告一个未解决的对账差异

```
available_balance − 300 = −38.56
realized_pnl            = −29.33
差 = **−$9.23**（账本里找不到对应 action）
```

本脚本**不修**这个差异（原因未知），只把它记录下来。

用法：
    .venv\\Scripts\\python.exe scripts\\mm_sync_shadow_equity.py --dry-run
    .venv\\Scripts\\python.exe scripts\\mm_sync_shadow_equity.py
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
ROLLBACK_KEY = "f290_shadow_equity_rollback"
STATUS = ROOT / "logs" / "mm_lane_status.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args()

    lane = reg.get_lane(LANE)
    if not lane:
        print(f"车道不存在: {LANE}")
        return 1
    meta = dict(lane.get("meta") or {})

    if a.rollback:
        prev = meta.get(ROLLBACK_KEY) or {}
        if not prev:
            print(f"没有可回滚记录（{ROLLBACK_KEY}）")
            return 1
        meta["shadow_equity"] = prev.get("shadow_equity")
        meta[ROLLBACK_KEY] = {}
        reg.update_meta(LANE, meta)
        print(f"已回滚 shadow_equity = {meta['shadow_equity']}")
        return 0

    # 读引擎现算的活权益
    live = None
    try:
        j = json.loads(STATUS.read_text(encoding="utf-8"))
        live = float(j.get("equity") or 0.0)
        age = datetime.datetime.now().timestamp() - float(j.get("ts") or 0)
    except Exception as e:
        print(f"✗ 读 {STATUS} 失败：{e}")
        return 1
    if live <= 0:
        print(f"✗ 活权益无效（{live}）⇒ 不写入（fail-closed）")
        return 1
    if age > 120:
        print(f"✗ 心跳已陈旧 {age:.0f}s > 120s ⇒ 不写入（fail-closed）")
        return 1

    old = meta.get("shadow_equity")
    print("=" * 84)
    print(f"F290 同步 shadow_equity（车道 {LANE}）")
    print("=" * 84)
    print(f"  心跳活权益 = ${live:.4f}（{age:.0f}s 前）")
    print(f"  meta.shadow_equity: {old} -> {round(live, 4)}")
    print(f"\n  ⚠️ 该字段被用作**配置的起始权益**还是**活权益**，语义被混用了。")
    print(f"     本脚本按「看板要显示活值」来用 —— 因为 frontend 拿它当总权益显示。")
    print(f"     若日后有别的模块把它当「起始值」，会受影响 ⇒ 已记录回滚点。")

    if old is not None and abs(float(old) - live) < 0.01:
        print("\n无需改动（已一致）。")
        return 0
    if a.dry_run:
        print("\n(--dry-run，未写入)")
        return 0

    meta["shadow_equity"] = round(live, 4)
    meta[ROLLBACK_KEY] = {
        "shadow_equity": old,
        "applied_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "reason": "F290 sync shadow_equity to engine live equity (dashboard showed stale 300)",
    }
    reg.update_meta(LANE, meta)
    print(f"\n已写入。回滚: --rollback")
    print(f"⚠️ 后端**不需要重启** —— 已部署代码就读这个字段（F289 的改动尚未生效，")
    print(f"   所以现在正是这个字段在决定看板显示什么）。")
    print(f"\n未解决：available_balance−300 = −38.56 而 realized_pnl = −29.33，差 −$9.23，")
    print(f"          账本里找不到对应 action ⇒ 如实记录，未修。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
