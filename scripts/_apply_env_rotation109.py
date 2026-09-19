# -*- coding: utf-8 -*-
"""[轮109] .env 补丁：关掉中线因子路由实开 + 抬高中线锁利地板（二进制安全）。

为什么：中线 7 天 62 笔 —— 毛利 +31.78、手续费 36.81、**净 −5.03**；
其中 `entry_source=factor_route` 34 笔 **净 −103.55**（笔均 −3.05），
`mlto` 28 笔 **净 +98.52**（笔均 +3.52）⇒ 亏损全部来自因子路由这条入场路径。

改动：
  MIDLONG_MID_VIA_FACTOR_ROUTE=true  → false   止血（影子档继续记证据）
  MIDLONG_MIN_LOCK_PROFIT_PCT_MID    → 0.010   （原 0.005：锁利地板 0.5%→1.0%，
                                                 往返手续费仅 0.04%，0.5% 的锁利
                                                 等于把赢单在 +1% 就截断）

用法：python scripts/_apply_env_rotation109.py [--apply]
"""
import argparse
import io
import os
import sys

ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

PATCH = {
    "MIDLONG_MID_VIA_FACTOR_ROUTE": "false",
    "MIDLONG_MIN_LOCK_PROFIT_PCT_MID": "0.010",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    with io.open(ENV, encoding="utf-8", errors="surrogateescape", newline="") as f:
        raw = f.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(nl)

    changed = []
    seen = set()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in PATCH:
            seen.add(key)
            old = stripped.split("=", 1)[1]
            if old != PATCH[key]:
                lines[i] = f"{key}={PATCH[key]}"
                changed.append((key, old, PATCH[key]))

    for key, val in PATCH.items():
        if key not in seen:
            lines.append(f"{key}={val}")
            changed.append((key, "(缺省)", val))

    print("=== 计划 ===")
    for k, o, n in changed:
        print(f"  {k}: {o} → {n}")
    if not changed:
        print("  （无变化）")
        return 0
    if not args.apply:
        print("\n[dry-run] 未落盘。加 --apply 执行。")
        return 0

    out = nl.join(lines)
    with io.open(ENV, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(out)
    print(f"\n[OK] 已写入 {ENV}（换行符 {repr(nl)} 保持）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
