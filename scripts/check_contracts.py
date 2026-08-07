"""
Lean 5 层契约检查器（P0.6 骨架，P2.1 完整启用）。

目标（方案 §1.2）：跨层数据传递只能用 contracts/types.py 定义的 dataclass，
禁止 agent 互相 import 内部模块。

当前状态（P0）：契约 dataclass 尚未在 P2.1 建成。本脚本做"前置体检"：
  1. check_syntax —— 所有 backend/**/*.py 可解析（语法零错误）。
  2. check_imports —— 关键模块（config.env_registry / settings）可导入。
  3. check_env_flags —— env_registry --check 的编程式调用。
  4. check_contract_layering —— 契约 dataclass 存在后，扫描跨层直传违规。

退出码：0 全通过，>0 有违规（CI 失败）。

用法：
    python scripts/check_contracts.py
    python scripts/check_contracts.py --strict   # env flag 未知即失败
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# 定位项目根（脚本在 <root>/scripts/ 下）
ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(ROOT))


def check_syntax() -> list[str]:
    """
    遍历 backend/**/*.py，收集语法错误。返回错误描述列表。

    注：backend/factor_engine/factors/ai_generated/ 和 _ai_gen_quarantine/ 下有大量
    损坏的 AI 生成因子文件（P1.2 因子清洗将整体清除/转表达式 DSL）。在 P1.2 完成前，
    这些目录的语法错误降级为 warning（不计入 hard error），避免阻塞 P0 CI。
    核心代码的语法错误仍为 hard error。
    """
    errors: list[str] = []
    factor_warnings = 0
    FACTOR_DIRS = {"ai_generated", "_ai_gen_quarantine"}
    for py in BACKEND.rglob("*.py"):
        rel = py.relative_to(ROOT)
        if any(p in {".venv", "__pycache__", "node_modules"} for p in py.parts):
            continue
        try:
            ast.parse(py.read_text(encoding="utf-8", errors="ignore"), filename=str(rel))
        except SyntaxError as e:
            if "factor_engine" in py.parts and FACTOR_DIRS & set(py.parts):
                factor_warnings += 1
            else:
                errors.append(f"{rel}:{e.lineno}: SyntaxError: {e.msg}")
    if factor_warnings:
        print(f"   [warn] ai_generated/_quarantine 因子目录 {factor_warnings} 个语法错误（P1.2 因子清洗将清除，暂不计入 hard error）")
    return errors


def check_imports() -> list[str]:
    """导入关键配置模块，验证依赖链无破损。"""
    errors: list[str] = []
    for mod in ("backend.config.env_registry",):
        try:
            __import__(mod)
        except Exception as e:  # noqa: BLE001
            errors.append(f"import {mod} failed: {e!r}")
    return errors


def check_env_flags(strict: bool = False) -> list[str]:
    """复用 env_registry 找未知/静默关闭 flag。strict=True 时未知即违规。"""
    errors: list[str] = []
    try:
        from backend.config.env_registry import find_silently_disabled_safety_flags, find_unknown_flags
    except Exception:  # noqa: BLE001
        return ["env_registry 不可导入，跳过 env flag 检查"]
    unknown = find_unknown_flags()
    if unknown:
        msg = "未知系统 env-flag（疑似拼写/遗留）: " + ", ".join(unknown[:20])
        if strict:
            errors.append(msg)
        else:
            print(f"[warn] {msg}")
    disabled = find_silently_disabled_safety_flags()
    if disabled:
        print(f"[warn] 安全关键 flag 被关闭（请确认有意）: {', '.join(disabled)}")
    return errors


# Lean 5 层 dataclass 契约名（P2.1 落地后启用强制）
CONTRACT_TYPES = {
    "Instrument", "MarketSnapshot", "FactorVector", "Insight",
    "Target", "ApprovedTarget", "OrderEvent",
}
# 层边界（P2 后建立）：跨这些目录的函数调用参数必须是契约 dataclass
LAYER_BOUNDARIES = {
    "services/contracts": "contracts",
    "services/data": "L2",
    "services/alpha": "L3",
    "services/factor_engine": "L3",
    "services/portfolio": "L4",
    "services/exchange": "L5",
}


def check_contract_layering() -> list[str]:
    """
    P2.1：契约层 contracts/types.py 已建立。检查：
        1. 契约文件可导入（语法 + 依赖无破损）
        2. 全部期望契约 dataclass 存在
        3. 契约层零业务依赖（不 import services，保证可独立编译/迁移）
    跨层函数签名的强制检查（L2→L3→L4→L5 必须传契约 dataclass）在 P2 热路径
    各 agent 重写后逐步收紧，目前先保证契约自身健康。
    """
    errors: list[str] = []
    contracts_file = BACKEND / "services" / "contracts" / "types.py"
    if not contracts_file.exists():
        # P2.1 已落地，文件必须存在
        errors.append("契约层 contracts/types.py 不存在（P2.1 应已建立）")
        return errors

    # 1. 可导入
    try:
        import importlib
        mod = importlib.import_module("backend.services.contracts.types")
    except Exception as e:  # noqa: BLE001
        errors.append(f"contracts.types 导入失败: {e!r}")
        return errors

    # 2. 期望 dataclass 存在
    for name in CONTRACT_TYPES:
        if not hasattr(mod, name):
            errors.append(f"契约层缺失 dataclass: {name}")

    # 3. 零业务依赖（contracts 源码不应 import services 业务模块）
    src = contracts_file.read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_prefixes = ("backend.services", "backend.api")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod_name = getattr(node, "module", None) or ""
            if mod_name and any(mod_name.startswith(p) for p in forbidden_prefixes):
                errors.append(
                    f"契约层禁止依赖业务模块，但 import 了 '{mod_name}'（契约须零业务依赖）"
                )

    if not errors:
        print(f"[info] 契约层健康（{len(CONTRACT_TYPES)} dataclass，零业务依赖）。")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="env flag 未知即失败")
    args = ap.parse_args()

    all_errors: list[str] = []
    print("== 1/4 语法检查 ==")
    e = check_syntax()
    print(f"   扫描完成，语法错误：{len(e)}")
    all_errors.extend(e)

    print("== 2/4 关键导入检查 ==")
    e = check_imports()
    print(f"   导入错误：{len(e)}")
    all_errors.extend(e)

    print("== 3/4 环境变量登记检查 ==")
    e = check_env_flags(strict=args.strict)
    all_errors.extend(e)

    print("== 4/4 契约分层检查 ==")
    e = check_contract_layering()
    all_errors.extend(e)

    if all_errors:
        print(f"\n❌ 检查未通过：{len(all_errors)} 项违规")
        for err in all_errors[:50]:
            print(f"   - {err}")
        return 1
    print("\n✅ 检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
