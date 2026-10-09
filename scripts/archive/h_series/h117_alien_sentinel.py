"""H117：非宇宙币成交的**实时哨兵** —— 残留是否仍在发生。

# 为什么需要

H115/H116 证实：宇宙收缩后，lane 仍对已剔除的 DOGE **正常做市了 5.3 分钟**
（09:11:11~09:16:28），18 笔**真实账本成交**（`lane_ledger.lane_id=mm_asterdex`），
峰值 |持仓| $303 —— 不是 `plan_orphan_exit` 的「单笔 taker 全平」语义。

当前心跳 `states` 只有 3 币、`orphan_inventory` 为空 ⇒ 之后似乎干净了。
但「似乎」不算证据。本脚本按窗口统计**非宇宙币的成交笔数**，
给出「残留是否已停止」的可复核判据。

# 判据（事先定死）

以 worker 最近一次重启时刻为界：
  · 界后非宇宙币成交 = 0 ⇒ 残留是**过渡期**现象（旧进程/旧状态收尾），已停止
  · 界后仍有非宇宙币成交 ⇒ **活动 bug**：宇宙收缩不能立即生效，必须修
    （后果：想剔除的币仍在吃敞口，且在**最高强平率**的币上）

用法：
    .venv\\Scripts\\python.exe scripts\\h117_alien_sentinel.py
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASIS = ROOT / "logs" / "mm_fill_basis.jsonl"
WLOG = ROOT / "logs" / "mm_lane_worker.log"
UNIVERSE = {"ASTER", "SOL", "XRP"}


def last_worker_start() -> str:
    """从 worker 日志取最近一次 start 行的时刻。"""
    if not WLOG.exists():
        return ""
    last = ""
    for line in WLOG.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[mm-worker\] start ", line)
        if m:
            last = m.group(1)
    return last.replace(" ", "T")


def main():
    rows = []
    for line in BASIS.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    rows.sort(key=lambda r: r.get("ts") or 0)

    rest = last_worker_start()
    print("=" * 100)
    print("H117  非宇宙币成交哨兵")
    print("=" * 100)
    print(f"  宇宙 = {sorted(UNIVERSE)}")
    print(f"  最近一次 worker start = {rest or '(未找到)'}")

    aliens = [r for r in rows if r.get("symbol") not in UNIVERSE]
    print(f"\n  全部非宇宙币成交 {len(aliens)} 笔")

    # 按分钟聚合非宇宙币成交
    bymin = defaultdict(lambda: defaultdict(int))
    for r in aliens:
        iso = (r.get("iso") or "")[:16]
        bymin[iso][r.get("symbol")] += 1
    print(f"\n  按分钟（只列有活动的分钟）：")
    for k in sorted(bymin):
        detail = " ".join(f"{s}:{n}" for s, n in sorted(bymin[k].items()))
        print(f"    {k}   {detail}")

    print("\n" + "=" * 100)
    print("判定")
    print("=" * 100)
    if not rest:
        print("  ⚠️ 找不到 worker start 时刻，无法判定。")
        return 1
    after = [r for r in aliens if (r.get("iso") or "") >= rest]
    before = [r for r in aliens if (r.get("iso") or "") < rest]
    print(f"  最近重启({rest}) **之前**的非宇宙币成交：{len(before)} 笔")
    print(f"  最近重启({rest}) **之后**的非宇宙币成交：**{len(after)}** 笔")
    if after:
        print("\n  ⇒ **活动 bug**：重启后仍在给非宇宙币成交 ⇒ 收缩不是即时生效的。")
        print("     后果：想剔除的币仍在消耗敞口，且通常是强平率最高的一批。")
        for r in after[:20]:
            print(f"       {r['iso'][:19]} {r['symbol']} {r['side']} "
                  f"qty={r['qty']:.4f} flat={r.get('flatten')} fee={r.get('fee_rate')}")
    else:
        print("\n  ⇒ 重启后**已无非宇宙币成交** ⇒ 残留是过渡期现象（旧进程/旧运行态收尾）。")
        print("     仍应记录：收缩从「注册表写入」到「实际停止报价」有 **~5 分钟**的窗口。")

    # 这段残留的代价
    tail = [r for r in aliens if (r.get("iso") or "") >= "2026-09-21T09:11"]
    print(f"\n  ── 该过渡窗口的代价（09:11 之后）──")
    print(f"     成交 {len(tail)} 笔；其中 flatten 腿 "
          f"{sum(1 for r in tail if r.get('flatten'))} 笔")
    tk = [r for r in tail if (r.get("fee_rate") or 0) > 0]
    print(f"     taker 腿 {len(tk)} 笔（fee_rate>0）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
