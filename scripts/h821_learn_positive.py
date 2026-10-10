# -*- coding: utf-8 -*-
"""学习汇总改接正在跑的重复来回。

以前本脚本按仓位编号把账本腿配成往返，里面混着拿了十几个小时的旧单，
和学习正在用的「开仓价到平仓价」不是同一套。现在交给 pp_learn_tables：
选币表、桶表、flow_learn_last.json 一次写齐。
"""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path


def _find_root(start: Path) -> Path:
    p = start.resolve()
    for _ in range(6):
        if (p / "backend").is_dir():
            return p
        if p.parent == p:
            break
    return start.resolve().parents[1]


ROOT = _find_root(Path(__file__))


def main() -> int:
    spec = importlib.util.spec_from_file_location(
        "pp_learn_tables", str(ROOT / "scripts" / "pp_learn_tables.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return int(mod.main())


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
