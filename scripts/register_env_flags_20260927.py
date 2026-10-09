# -*- coding: utf-8 -*-
"""把"代码读了但没登记"的 env flag 补登到 `KNOWN_FLAGS`，并把棘轮基线收到实测值。

背景：`backend/tests/unit/test_env_governance_reverse_20260918.py` 的两条棘轮
（`read_but_unregistered` ≤283、`undeclared_switches` ≤18）在当前仓库实测为 **312 / 19** ——
09-18 之后新增的功能（WFO_*、FACTORS_LAB_*、SCALP_META_*、KLINE_P0_*、MIDLONG_BRAIN_* 等）
读了新的 env 名字却没登记，正是这条门禁要抓的东西。测试文件自己的 docstring 写明处理方式：
「新加了 flag 没登记 → 补登 `KNOWN_FLAGS`（这正是本门禁的目的）」。

本脚本只做两件事（都不改变任何运行时行为）：
1. 把 `env_governance_report()['read_but_unregistered']` 里的名字**逐条插入** KNOWN_FLAGS 字面量；
2. 把两条基线从 283/18 收到补登后的实测值（应为 0/0），并写上日期与原因。

用法：.venv\\Scripts\\python.exe scripts\\register_env_flags_20260927.py [--apply]
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "backend/config/env_registry.py"
sys.path.insert(0, str(ROOT))


def main() -> int:
    apply = "--apply" in sys.argv
    from backend.config import env_registry as er

    rep = er.env_governance_report()
    add = [k for k in rep["read_but_unregistered"] if k not in er.KNOWN_FLAGS]
    print(f"待补登 = {len(add)} 条；其中 *_ENABLED/_DISABLED 开关 = "
          f"{sum(1 for k in add if k.endswith(('_ENABLED', '_DISABLED')))}")
    print("示例:", add[:8], "…" if len(add) > 8 else "")
    if not add:
        print("无需改动。")
        return 0

    src = REG.read_text(encoding="utf-8")
    tag = f"# [2026-09-27 R4 补登] 以下 {len(add)} 个名字是代码实际读取、但此前未登记的 env flag。"
    note = ("# 补登后 `read_but_unregistered` = 0，棘轮基线同步收到 0（新增 flag 必须登记，否则该测试立即变红）。\n"
            "# 生成脚本：scripts/register_env_flags_20260927.py（幂等，可重复执行核对）。\n")
    block = tag + "\n" + note + "".join(f'    "{k}",\n' for k in sorted(add))
    # 插到 KNOWN_FLAGS 字面量的收尾 `})` 之前。
    # 教训（三次事故）：① 不能把后面的 `def` 行包含进匹配（会吞签名）；
    # ② 不能只匹配"第一个 `})`"——文件里 SAFETY_CRITICAL_FLAGS 更早闭合，
    # 会把键插进**错误的 frozenset**。唯一可靠的锚 = 找 `def _matches_system_prefix`
    # 之前的最后一个 `})`。
    i_def = src.rfind("\ndef _matches_system_prefix")
    if i_def < 0:
        print("[err] 找不到 _matches_system_prefix 定义")
        return 2
    i_close = src.rfind("})", 0, i_def)
    if i_close < 0:
        print("[err] 找不到 KNOWN_FLAGS 收尾位置")
        return 2
    new_src = src[:i_close] + block + src[i_close:]

    # 基线收紧到补登后的实测值（补登 = cur 归零）
    new_src = re.sub(
        r"ENV_READ_UNREGISTERED_BASELINE = \d+",
        "ENV_READ_UNREGISTERED_BASELINE = 0",
        new_src,
    )
    new_src = re.sub(
        r"ENV_UNDECLARED_SWITCH_BASELINE = \d+",
        "ENV_UNDECLARED_SWITCH_BASELINE = 0",
        new_src,
    )
    if not apply:
        print("\n[dry-run] 将插入", len(add), "条并收紧基线到 0；加 --apply 执行。")
        return 0
    REG.write_text(new_src, encoding="utf-8")
    import py_compile
    py_compile.compile(str(REG), doraise=True)
    print(f"[apply] 已写入 {REG.name}（{date.today().isoformat()}），py_compile OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
