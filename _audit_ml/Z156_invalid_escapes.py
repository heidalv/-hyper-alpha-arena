# -*- coding: utf-8 -*-
"""Z156 (目标项 (a)/(f)): 扫描「非法转义序列」等延迟失败语法隐患。

Python 3.12 对非 raw 字符串里的无效转义（如 "\\/"、"\\d"）只发 SyntaxWarning；
未来版本会升级为 SyntaxError。这类问题不会在运行时暴露，但会在升级/`-W error` 的
CI 里整片炸掉 —— 属于典型「静默失效」。

用法: python _audit_ml/Z156_invalid_escapes.py [root ...]
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOTS = [Path(p) for p in (sys.argv[1:] or [r"D:\001Alpha\Hyper-Alpha-Arena\backend"])]
SKIP = ("_pytest_tmp", "__pycache__", ".venv", "node_modules")


def main() -> None:
    bad: list[tuple[str, int, str]] = []
    total = 0
    for root in ROOTS:
        for py in sorted(root.rglob("*.py")):
            if any(s in str(py) for s in SKIP):
                continue
            total += 1
            # 口径修正：必须编译**字节**（与 import 机制一致，会解析 PEP 263 coding cookie
            # 并剥离 UTF-8 BOM）。读成 str 会把「GBK 编码文件」「带 BOM 文件」误判为语法错误。
            raw = py.read_bytes()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                try:
                    compile(raw, str(py), "exec")
                except (SyntaxError, SyntaxWarning, ValueError) as e:  # noqa: PERF203
                    bad.append((str(py), getattr(e, "lineno", 0) or 0, f"{type(e).__name__}: {e}"))
                    continue
                for w in caught:
                    if issubclass(w.category, SyntaxWarning):
                        bad.append((str(py), w.lineno or 0, f"SyntaxWarning: {w.message}"))
    print(f"扫描 {total} 个 .py 文件")
    if not bad:
        print("✅ 无非法转义/语法隐患")
    else:
        print(f"❌ 发现 {len(bad)} 处：")
        for path, line, msg in bad:
            print(f"  {path}:{line}  {msg}")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
