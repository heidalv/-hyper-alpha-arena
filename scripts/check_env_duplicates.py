#!/usr/bin/env python3
"""`.env` 重复键去重校验（2026-07-06 整改新增）。

背景：审查报告 4.6/发现A 指出 `.env` 里存在多处重复定义的配置键，
dotenv 采用"最后写入者获胜"的隐式规则——任何人以后在文件顶部/中部
再加一行同名配置用于"临时测试"，都可能因为忘记删除而意外覆盖生产值，
且没有任何工具会提醒。本脚本在部署/CI 流程中前置执行，发现重复键
直接非零退出，阻止带着隐患的配置上线。

用法：
    python scripts/check_env_duplicates.py [--env-file .env]

退出码：
    0   未发现重复键
    1   发现重复键（详情打印到 stdout）
    2   .env 文件不存在
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def find_duplicate_keys(env_path: Path) -> dict[str, list[tuple[int, str]]]:
    """扫描 env 文件，返回 {key: [(行号, 原始行), ...]}，仅包含出现 >=2 次的 key。"""
    occurrences: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for lineno, raw_line in enumerate(
        env_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
    ):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _KEY_RE.match(raw_line)
        if not m:
            continue
        key = m.group(1)
        occurrences[key].append((lineno, raw_line.rstrip()))

    return {k: v for k, v in occurrences.items() if len(v) >= 2}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file", default=str(ROOT / ".env"),
        help="待校验的 env 文件路径，默认项目根目录下的 .env",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f"[check_env_duplicates] 文件不存在: {env_path}")
        return 2

    dups = find_duplicate_keys(env_path)
    if not dups:
        print(f"[check_env_duplicates] OK：{env_path} 未发现重复键。")
        return 0

    print(f"[check_env_duplicates] 发现 {len(dups)} 个重复定义的键（{env_path}）：")
    for key, occs in sorted(dups.items()):
        print(f"  - {key}  出现 {len(occs)} 次：")
        for lineno, raw_line in occs:
            print(f"      L{lineno}: {raw_line}")
        print(
            f"      → dotenv 实际生效值来自最后一次出现（L{occs[-1][0]}），"
            f"其余为无效的隐式覆盖，必须删除多余定义。"
        )
    print(
        "\n[check_env_duplicates] 校验失败：请删除多余的重复定义，"
        "只保留一份预期生效的配置行后再部署。"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
