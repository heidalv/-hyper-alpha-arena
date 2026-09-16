# -*- coding: utf-8 -*-
"""[P8 / §67 执行 2026-09-10] 死键登记册：把「.env 里存在但代码从不读取」的键变成**机器可审计**的清单。

背景（§54.5）：`.env` 有 946 个键，其中一批是"开关装饰" —— 写了但没有任何读取方
（如 `MARKET_SCANNER_*`、`ANOMALY_DETECTOR_ENABLED`）。原决策项 P8 的选择是
「删除 / 接线 / 保留说明」。本轮采取的**安全执行**：
  * **不删 `.env` 任何一行**（删除会让"以后想接线"的人失去线索，且 `.env` 还有 P9 的注释损坏待处理）；
  * 改为产出**登记册** `data/dead_keys_register.json`（键、分类、证据、处置建议、复核时间）；
  * 用**两套独立工具**交叉验证：静态 AST 审计（audit_config_effective）∩ 运行期注册表校验
    （env_registry.find_unknown_flags）——两者不一致即报错退出。

用法：
  python backend/scripts/audit_dead_keys_register.py            # 生成 + 校验
  python backend/scripts/audit_dead_keys_register.py --check    # 只校验（CI/测试用）
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# [2026-09-16 调研轮7] Windows 控制台默认 cp936(GBK)：打印 ✅/❌ 会抛 UnicodeEncodeError
# → 脚本 rc=1 → 例行审计误报 FAIL、真失败被淹没。守卫 stdout/stderr 为 UTF-8。
try:  # pragma: no cover
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REGISTER = ROOT / "data" / "dead_keys_register.json"

#: 注册表口径不覆盖的非系统前缀键（静态审计会算作死键，但注册表只校验系统前缀）
NON_PREFIX_ALLOWLIST = {"LOG_FILE", "NO_PROXY", "no_proxy"}

#: 处置建议词汇表（登记册里每项都必须是其中之一）
DECISIONS = {
    "retain-annotated": "保留并在本登记册记录（无行为影响，供后续接线或清理）",
    "delete-candidate": "建议删除（纯装饰性开关，且不存在任何读取方或路线图引用）",
    "wire-up": "建议接线（代码里已有同名意图但用错键名，需人确认）",
}


def _load_audit_module():
    path = ROOT / "backend" / "scripts" / "audit_config_effective.py"
    spec = importlib.util.spec_from_file_location("_audit_cfg_eff", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _classify(key: str, kind: str) -> str:
    """按"是否连路线图引用都没有"给处置建议（保守：默认保留并登记）。"""
    if kind == "used_only_in_tests":
        return "wire-up"
    return "retain-annotated"


def build(*, write: bool = True) -> dict:
    mod = _load_audit_module()
    rep = mod.build_report()
    findings = rep.get("findings", {})
    dead = sorted(findings.get("env_truly_dead") or [])
    tests_only = sorted(findings.get("env_used_only_in_tests") or [])
    dynamic = sorted(findings.get("env_dynamic_prefix_read") or [])
    suffix = sorted(findings.get("env_suffix_composed_read") or [])

    from backend.config.env_registry import find_unknown_flags

    registry_unknown = sorted(find_unknown_flags())

    # 去重：同一键可能同时出现在 "真死" 与 "仅测试引用" 两个集合里
    # （实测 18 个键全部如此）——"仅测试引用"信息量更大（说明有人写过读取方），故覆盖之。
    entries_map: dict[str, dict] = {}
    for key in dead:
        entries_map[key] = {
            "key": key, "kind": "truly_dead", "decision": _classify(key, "truly_dead"),
            "evidence": "audit_config_effective.findings.env_truly_dead",
        }
    for key in tests_only:
        entries_map[key] = {
            "key": key, "kind": "used_only_in_tests", "decision": _classify(key, "used_only_in_tests"),
            "evidence": "audit_config_effective.findings.env_used_only_in_tests",
        }
    entries = [entries_map[k] for k in sorted(entries_map)]

    dead_set = set(dead)
    unknown_set = set(registry_unknown)
    diff = sorted(dead_set ^ unknown_set)
    unexplained = [k for k in diff if k not in NON_PREFIX_ALLOWLIST]

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "env_keys_total": rep.get("env_n"),
        "truly_dead_n": len(dead),
        "used_only_in_tests_n": len(tests_only),
        "dynamic_prefix_read_n": len(dynamic),
        "suffix_composed_read_n": len(suffix),
        "registry_unknown_n": len(registry_unknown),
        "cross_check_diff": diff,
        "cross_check_ok": not unexplained,
        "decisions_vocabulary": DECISIONS,
        "entries": entries,
    }
    if write:
        REGISTER.parent.mkdir(parents=True, exist_ok=True)
        REGISTER.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验既有登记册与当前状态是否一致")
    args = ap.parse_args()

    if args.check and REGISTER.exists():
        old = json.loads(REGISTER.read_text(encoding="utf-8"))
        new = build(write=False)
        old_keys = {e["key"] for e in old.get("entries", [])}
        new_keys = {e["key"] for e in new["entries"]}
        print(f"登记册 {len(old_keys)} 项 / 当前 {len(new_keys)} 项")
        added = sorted(new_keys - old_keys)
        gone = sorted(old_keys - new_keys)
        if added:
            print("  新增死键（请更新登记册）:", added)
        if gone:
            print("  不再是死键（说明已接线，可从登记册移除）:", gone)
        print("  交叉验证:", "✅ 一致" if new["cross_check_ok"] else f"❌ 不一致 {new['cross_check_diff']}")
        return 0 if (not added and not gone and new["cross_check_ok"]) else 1

    payload = build()
    print(f"死键登记册已写入 {REGISTER}")
    print(f"  env 键总数 {payload['env_keys_total']}｜真死键 {payload['truly_dead_n']}｜"
          f"仅测试引用 {payload['used_only_in_tests_n']}｜动态前缀读取 {payload['dynamic_prefix_read_n']}｜"
          f"后缀拼接读取 {payload['suffix_composed_read_n']}")
    print(f"  独立工具（env_registry）未知键 {payload['registry_unknown_n']} 个；"
          f"交叉验证：{'✅ 一致' if payload['cross_check_ok'] else '❌ 不一致'}")
    if payload["cross_check_diff"]:
        print("  差集:", payload["cross_check_diff"])
    for e in payload["entries"]:
        print(f"    - {e['key']}  [{e['kind']}] → {e['decision']}")
    return 0 if payload["cross_check_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
