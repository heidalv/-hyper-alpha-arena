# -*- coding: utf-8 -*-
"""[F368 2026-09-18] MLTO 决策路径**信号接线矩阵**：谁生产、谁登记、谁按名读。

由来：用户指出「长线周期没有正确接入」，查证发现 `factor_anchor_4h` 属"生产端有、消费端
没登记"⇒ 落到 `weights.get(name, 0.01)` 兜底（F367）。这类缺陷不该靠人肉发现，
本脚本把它做成**可复跑的矩阵**：

| 列 | 含义 |
|---|---|
| 生产 | `mlto/*.py` 里 `Signal("名字", …)` 出现的名字（AST 解析，含 f-string 前缀） |
| 登记 | `WEIGHTS_MID` / `WEIGHTS_LONG` 的键 |
| 按名读 | 代码里 `s.name == "X"` 形式显式取用的名字（如 `llm_qual`） |
| 判定 | 未登记 → **0.01 兜底**（≈没接）；登记=0.0 → 刻意忽略；两者都没有 → 算了没人读 |

只读；AST + 正则，不导入除 decision_hub 以外的重模块。
"""
from __future__ import annotations

import ast
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

MLTO = ROOT / "backend/services/mlto"


def produced() -> dict:
    """AST 扫 `Signal(<name>, ...)`：返回 {名字: [文件:行]}。f-string 取固定前缀+参数名。"""
    out: dict = {}
    for f in sorted(MLTO.rglob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            if name != "Signal" or not node.args:
                continue
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                key = a0.value
            elif isinstance(a0, ast.JoinedStr):
                # f"factor_anchor_{_period}" → "factor_anchor_*"
                parts = []
                for v in a0.values:
                    if isinstance(v, ast.Constant):
                        parts.append(str(v.value))
                    else:
                        parts.append("*")
                key = "".join(parts)
            else:
                key = "<动态表达式>"
            out.setdefault(key, []).append(f"{f.name}:{node.lineno}")
    return out


def by_name_reads() -> dict:
    """正则扫 `s.name == "X"` / `.name in ("X","Y")` 形式的显式按名取用。"""
    out: dict = {}
    for f in sorted(MLTO.rglob("*.py")):
        txt = f.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"\.name\s*(?:==|in)\s*\(?([^)\n:]+)", txt):
            for lit in re.findall(r"[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']", m.group(1)):
                out.setdefault(lit, []).append(f"{f.name}:{txt[:m.start()].count(chr(10)) + 1}")
    return out


def main() -> int:
    from backend.services.mlto.decision_hub import WEIGHTS_LONG, WEIGHTS_MID

    prod = produced()
    reads = by_name_reads()
    mid, lng = set(WEIGHTS_MID), set(WEIGHTS_LONG)

    print("=" * 92)
    print("MLTO 信号接线矩阵（生产 → 登记 → 按名读）")
    print("=" * 92)
    hdr = f"{'信号名':<26}{'生产处':<7}{'MID':<9}{'LONG':<9}{'按名读':<7}判定"
    print(hdr)
    print("-" * 92)
    problems = []
    for name in sorted(prod):
        where = len(prod[name])
        is_prefix = name.endswith("*")
        in_mid = name in mid or (is_prefix and any(k.startswith(name[:-1]) for k in mid))
        in_lng = name in lng or (is_prefix and any(k.startswith(name[:-1]) for k in lng))
        read = name in reads
        # 前缀名（factor_anchor_*）走 decision_hub 的前缀规则 ⇒ 视为已接
        prefix_handled = is_prefix and name.startswith("factor_anchor_")
        if in_mid or in_lng:
            verdict = "已登记"
            if (name in mid and WEIGHTS_MID.get(name) == 0.0) or (
                    name in lng and WEIGHTS_LONG.get(name) == 0.0):
                verdict = "登记=0（刻意忽略）"
        elif prefix_handled:
            verdict = "前缀规则接上（F367）"
        else:
            verdict = "**未登记 ⇒ 0.01 兜底（≈没接）**"
            problems.append((name, prod[name]))
        print(f"{name:<26}{where:<7}{'是' if in_mid else '·':<9}{'是' if in_lng else '·':<9}"
              f"{'是' if read else '·':<7}{verdict}")

    print("\n" + "=" * 92)
    print("按名读取但**无生产处**的信号（可能是死读取 / 或名字写错）")
    print("=" * 92)
    dead = []
    for name in sorted(reads):
        if name in prod:
            continue
        # 允许"由外部/其它模块生产"的情况：只报既无生产、又不在权重表里的
        if name in mid or name in lng:
            print(f"  {name:<26}登记在权重表（生产处可能在 MLTO 之外）")
        else:
            print(f"  {name:<26}**既无生产处也不在权重表** ⇒ 读取恒 None（{reads[name][:2]}）")
            dead.append(name)

    print("\n" + "=" * 92)
    print(f"结论：{len(prod)} 个生产信号；未登记={len(problems)}；"
          f"按名读取却无生产/无登记={len(dead)}")
    for n, w in problems:
        print(f"  ⚠️ 未登记: {n}  生产于 {w[:2]}")
    for n in dead:
        print(f"  ⚠️ 死读取: {n}  {reads[n][:2]}")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
