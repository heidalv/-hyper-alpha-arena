# -*- coding: utf-8 -*-
"""[轮110] .env 补丁：中线止盈档位重标（二进制安全）。

依据（159 笔中线已平仓，真实 MFE/MAE 网格，已扣 0.04% 往返费）：
  现行 SL1.5% / 首档 TP 0.8% = **−0.175%/笔**（0.8% 列是 7×8 网格里全场最差）
  保留的 mlto 臂：TP2.5% = +0.237、TP4.0% = +0.386、TP6.0% = +0.399
  mlto 臂 MFE 分位：P50=1.41% P75=2.46% P85=3.67% P90=3.85% P95=4.52%
  ⇒ 首档 = P75(2.5%)、二档 ≈ P90(4.0%)、三档放到 P95 之外(6.0%) 让尾部奔跑
  放宽止损无效（SL1.5/2.0/3.0/4.0 → +0.237/+0.245/+0.158/+0.058）⇒ SL 保持 1.5%，
  每笔风险不变，无需重算仓位乘子。

用法：python scripts/_apply_env_rotation110.py [--apply]
"""
import argparse
import io
import os
import sys

ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

PATCH = {
    "EXIT_POLICY_MID_TP_STAGES": "2.5,4.0,6.0",
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
            changed.append((key, "(缺省，走 ExitPolicy 默认)", val))

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
