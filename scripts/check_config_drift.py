#!/usr/bin/env python3
"""
check_config_drift.py — 运行时配置漂移校验（R1）

比对 docs/RUNTIME_CONFIG_FACTS.md（声明意图/期望值）与仓库根 .env（实况），
报告 OK / DRIFT / MISSING / DUPLICATE / UNKNOWN：

  OK        事实清单与 .env 一致
  DRIFT     两边都有但值不一致（文档=x, 实况=y）→ 退出码 1
  MISSING   事实清单有、.env 无，且期望值不是 false/0（缺省即 falsy 视为 OK）→ 退出码 1
  DUPLICATE .env 中同一键出现多次 → 退出码 1（历史教训：重复键会静默覆盖）
  UNKNOWN   .env 有、事实清单未登记（仅统计与提示，不导致失败）

安全约束：绝不读取/输出含 KEY|SECRET|PASSWORD|TOKEN|SALT|HMAC|FERNET|PRIVATE|WEBHOOK|_URL 的键。

用法：
  python scripts/check_config_drift.py             # 全量比对
  python scripts/check_config_drift.py --verbose   # 同时列出 UNKNOWN 键名
  python scripts/check_config_drift.py --fix-doc   # 把 .env 实况回写事实清单期望值列（生成 .bak 备份）
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FACTS_PATH = REPO_ROOT / "docs" / "RUNTIME_CONFIG_FACTS.md"
ENV_PATH = REPO_ROOT / ".env"

SECRET_HINT = re.compile(r"(KEY|SECRET|PASSWORD|TOKEN|SALT|HMAC|FERNET|PRIVATE|WEBHOOK|PROXY|_URL)", re.I)
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
FACT_ROW_RE = re.compile(r"^\|\s*([A-Z][A-Z0-9_]*)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|")


def is_secret_key(key: str) -> bool:
    return bool(SECRET_HINT.search(key))


def load_env(path: Path) -> tuple[dict[str, str], list[str]]:
    """解析 .env → {key: value}，并收集重复键。"""
    values: dict[str, str] = {}
    duplicates: list[str] = []
    seen: set[str] = set()
    if not path.exists():
        return values, duplicates
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not ENV_KEY_RE.match(key) or is_secret_key(key):
            continue
        if key in seen:
            duplicates.append(key)
        seen.add(key)
        values[key] = value.strip().strip('"').strip("'")
    return values, duplicates


def load_facts(path: Path) -> list[tuple[str, str, str]]:
    """解析事实清单表格 → [(key, expected, note)]。"""
    facts: list[tuple[str, str, str]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = FACT_ROW_RE.match(raw.strip())
        if not m:
            continue
        key, intent, expected = m.group(1), m.group(2).strip(), m.group(3).strip()
        if is_secret_key(key):
            continue
        facts.append((key, expected, intent))
    return facts


def normalize(value: str) -> str:
    v = value.strip().lower()
    return v


def _same(env_value: str, expected: str) -> bool:
    if normalize(env_value) == normalize(expected):
        return True
    # 布尔等价：true/yes/1 与 false/no/0
    TRUE_SET, FALSE_SET = {"true", "yes", "1", "on"}, {"false", "no", "0", "off"}
    e, x = normalize(env_value), normalize(expected)
    return (e in TRUE_SET and x in TRUE_SET) or (e in FALSE_SET and x in FALSE_SET)


def _print_ok(key: str, actual: str, note: str = "") -> None:
    print(f"OK       {key:45s} = {actual}{('  ' + note) if note else ''}")


def run_check(env: dict[str, str], duplicates: list[str], facts: list[tuple[str, str, str]],
              verbose: bool = False) -> int:
    problems = 0
    print(f"facts={len(facts)} env_keys={len(env)}")
    for key, expected, intent in facts:
        if key not in env:
            if normalize(expected) in {"false", "0", "off", "no"}:
                _print_ok(key, f"(absent, default {expected})")
            else:
                problems += 1
                print(f"MISSING  {key:45s} expected {expected:8s} (absent in .env) -- {intent}")
            continue
        actual = env[key]
        if _same(actual, expected):
            _print_ok(key, actual)
        else:
            problems += 1
            print(f"DRIFT    {key:45s} doc={expected:8s} env={actual:8s} -- {intent}")

    if duplicates:
        for d in sorted(set(duplicates)):
            problems += 1
            print(f"DUPLICATE {d:45s} key appears multiple times in .env (silent override risk, dedupe needed)")

    # UNKNOWN: .env 有、事实清单未登记（仅提示）
    fact_keys = {k for k, _, _ in facts}
    unknown = [k for k in env if k not in fact_keys]
    print(f"UNKNOWN  {len(unknown)} env flags not registered in facts file (hint only, not a failure)")
    if verbose:
        for k in sorted(unknown):
            print(f"  - {k} = {env[k]}")
    return 1 if problems else 0


def fix_doc(env: dict[str, str], facts: list[tuple[str, str, str]], path: Path) -> None:
    """把 .env 实况回写事实清单期望值列（先备份）。"""
    env_by_key = {k: v for k, v in env.items()}
    backup = path.with_suffix(path.suffix + ".bak")
    path.replace(backup)
    out_lines: list[str] = []
    for raw in backup.read_text(encoding="utf-8", errors="replace").splitlines():
        m = FACT_ROW_RE.match(raw.strip())
        if m and m.group(1) in env_by_key:
            key, intent, expected = m.group(1), m.group(2), m.group(3)
            actual = env_by_key[key]
            if not _same(actual, expected):
                raw = f"| {key} | {intent} | {actual} | ⚠ 已回写实况（原期望 {expected}，请人工确认意图） |"
        out_lines.append(raw)
    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"Wrote back {path.name} (backup: {backup.name}). Review before commit.")


def main() -> int:
    parser = argparse.ArgumentParser(description="运行时配置漂移校验（R1）")
    parser.add_argument("--verbose", action="store_true", help="列出 UNKNOWN 键名")
    parser.add_argument("--fix-doc", action="store_true", help="把 .env 实况回写事实清单")
    args = parser.parse_args()

    if not FACTS_PATH.exists():
        print(f"事实清单不存在: {FACTS_PATH}")
        return 2

    env, duplicates = load_env(ENV_PATH)
    facts = load_facts(FACTS_PATH)

    if args.fix_doc:
        fix_doc(env, facts, FACTS_PATH)
        facts = load_facts(FACTS_PATH)  # 重读后继续校验

    code = run_check(env, duplicates, facts, verbose=args.verbose)
    print("\nCONCLUSION: " + ("DRIFT FOUND - fix before commit" if code else "NO DRIFT"))
    return code


if __name__ == "__main__":
    sys.exit(main())
