# -*- coding: utf-8 -*-
"""[轮114] .env 补丁：把既有中线冷却基准 30min → 2h（二进制安全）。

依据（158 笔中线已平仓，按"距上次同币平仓的间隔"分桶）：
  0–2h  n=40  均值 **−0.389%**  胜率 **0.375**   ← 显著最差
  去掉该档后 n=118 均值 **−0.027%** 胜率 0.500
注：既有模块 `reentry_cooldown` 还有更长档（亏损 4h / SL 2h），本项只调**基准**那一档。

用法：python scripts/_apply_env_rotation114.py [--apply]
"""
import argparse
import io
import os
import sys

ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

PATCH = {
    "TIER_MID_COOLDOWN_SEC": "7200",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    with io.open(ENV, encoding="utf-8", errors="surrogateescape", newline="") as f:
        raw = f.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(nl)

    changed, seen = [], set()
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key = s.split("=", 1)[0].strip()
        if key in PATCH:
            seen.add(key)
            old = s.split("=", 1)[1]
            if old != PATCH[key]:
                lines[i] = f"{key}={PATCH[key]}"
                changed.append((key, old, PATCH[key]))
    for key, val in PATCH.items():
        if key not in seen:
            lines.append(f"{key}={val}")
            changed.append((key, "(缺省 1800=30min)", val))

    print("=== 计划 ===")
    for k, o, n in changed:
        print(f"  {k}: {o} → {n}")
    if not changed:
        print("  （无变化）")
        return 0
    if not args.apply:
        print("\n[dry-run] 未落盘。加 --apply 执行。")
        return 0
    with io.open(ENV, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(nl.join(lines))
    print(f"\n[OK] 已写入 {ENV}（换行符 {repr(nl)} 保持）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
