"""h595 — 从 worker 日志**解析闸门构成**（只读，R139）。

上一轮（R138）发现日志每 5 分钟一行、含 `skip={...}` 计数器，但我当时把行截到 150 字符 ✗。
本脚本取**最后一行**、完整解析 `skip` 字典，并给出：
  · 各闸门（`trend_down` / `trend_only_flat` / …）的**累计计数与占比**；
  · `ticks` / `fills` ⇒ 实际成交率；
  · 与"要不要关趋势闸"（队列 ⑥ = h520）相关的量化依据 ✓。

用法：python scripts/h595_gate_mix.py
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "mm_lane_worker.log"


def main() -> int:
    if not LOG.exists():
        print(f"✗ 找不到 {LOG}")
        return 1
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    last = next((l for l in reversed(lines) if "skip=" in l), None)
    if not last:
        print("✗ 最近没有含 skip= 的行")
        return 1
    print("=" * 92)
    print("worker 最近一行（完整）")
    print("=" * 92)
    print("  " + last[:400] + (" …" if len(last) > 400 else ""))
    m = re.search(r"skip=\{([^}]*)\}", last)
    ticks = re.search(r"ticks=(\d+)", last)
    fills = re.search(r"fills=(\d+)", last)
    ts = last[:19]
    print("\n" + "=" * 92)
    print(f"闸门构成解析（行时间 {ts}）")
    print("=" * 92)
    if ticks and fills:
        t, f = int(ticks.group(1)), int(fills.group(1))
        print(f"  ticks={t}  fills={f}  ⇒ 累计成交率 {f/max(t,1):.1%}")
    if m:
        pairs = []
        for part in m.group(1).split(","):
            if ":" in part:
                k, v = part.split(":", 1)
                k, v = k.strip().strip("'\""), v.strip()
                try:
                    pairs.append((k, int(v)))
                except ValueError:
                    pass
        total = sum(v for _, v in pairs) or 1
        print(f"\n  {'闸门':<26}{'累计':>8}{'占比':>9}")
        for k, v in sorted(pairs, key=lambda x: -x[1]):
            print(f"  {k:<26}{v:>8}{v/total:>9.1%}")
        print(f"  {'（合计）':<26}{total:>8}")
        trend = sum(v for k, v in pairs if "trend" in k)
        print(f"\n  ⇒ 与趋势有关的闸门合计 {trend}（{trend/total:.0%}）"
              f"；其余 {total-trend}（{(total-trend)/total:.0%}）")
    else:
        print("  ✗ 未解析到 skip 字典")
        return 1
    print("=" * 92)
    print("用途：这是队列 ⑥（h520：`trend_only_q` 0.35→0）的**实盘侧量化依据** ✓；"
          "注意这些是**累计计数**（各闸门可重叠），不等于互斥份额 ✓。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
