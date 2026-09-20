# -*- coding: utf-8 -*-
"""[轮153] 棘轮：`archived` 必须是策略状态机的**终态**。

背景
----
轮153 把 21 条 `tpl_long*`（长线模板族残余：9 active + 12 paused）落成 `archived`，
以停止被扫描侧（`full_auto_trading_service` / `master_execution` / `midlong_helpers`
的 `status == "active"` 过滤）反复捞起 → 24h 127 次 `long_template_source_block`。

这次清理之所以安全，靠的是"**没有任何自动路径会把 archived 复活**"这一事实：
  · `paper_session_helpers` 的恢复循环只选 `paused/frozen/terminated`（163/212/228 行）
  · `symbol_risk._unfreeze_symbols` 只选 `paused`（351 行）
  · `strategy_learning_service` 的升降级只遍历 `active/graduated/golden`（1854 行）

如果哪天有人给这些循环加上 `archived`（例如"归档策略也该参与训练"），这次清理会被
**静默回滚**——用户明确反感"设计了但会自己解冻/复活"的机制。本测试把该事实钉死。

唯一允许复活 archived 的地方是**人手调用**的 API 端点
（`backend/api/ai_strategy_routes.py::resume_strategy`），它必须同时接受
`archived/terminated/paused`——那是人的显式指令，不是自动路径。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
SERVICES = BACKEND / "services"
API = BACKEND / "api"

# 允许把 archived 复活成 active 的函数（完整限定名 → 理由）
ALLOWED_REVIVERS = {
    "backend/api/ai_strategy_routes.py::resume_strategy": "人手调用的恢复端点",
}


def _read(path: Path) -> str:
    # utf-8-sig：仓库存量文件里混有带 BOM 的（历史 Set-Content -Encoding utf8 产物），
    # 直接 utf-8 读会让 ast.parse 在首行抛 SyntaxError。
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _iter_functions(path: Path):
    try:
        tree = ast.parse(_read(path))
    except SyntaxError:
        # 仓库里有**本来就无法解析**的文件：AI 生成的因子被隔离在
        # backend/services/factor_engine/factors/_ai_gen_quarantine/
        # （如 ai_gen_chaotic.py 的 `class Directional Chaos Index(BaseFactor)`）。
        # 它们不被导入（隔离目录），与本棘轮无关 → 跳过，不让它把测试染红。
        return
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _sets_active(node: ast.AST) -> list[str]:
    """函数体内把某个 `.status` 赋成字符串 "active" 的目标名。"""
    out: list[str] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Assign):
            continue
        if len(sub.targets) != 1:
            continue
        tgt = sub.targets[0]
        if not isinstance(tgt, ast.Attribute) or tgt.attr != "status":
            continue
        val = sub.value
        if isinstance(val, ast.Constant) and val.value == "active":
            out.append(ast.unparse(tgt))
    return out


def _revivers():
    """(限定名, 目标, 函数源码) —— 所有会写 status="active" 的函数。"""
    hits = []
    for base in (SERVICES, API):
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(BACKEND).as_posix()
            if "/tests/" in rel or rel.endswith("__init__.py"):
                continue
            src = _read(path)
            for fn in _iter_functions(path):
                targets = _sets_active(fn)
                if not targets:
                    continue
                hits.append((
                    f"backend/{rel}::{fn.name}", targets, _selects_archived(fn),
                ))
    return hits


def _has_archived_const(node: ast.AST) -> bool:
    return any(
        isinstance(s, ast.Constant) and s.value == "archived" for s in ast.walk(node)
    )


def _selects_archived(node: ast.AST) -> list[str]:
    """函数体内把 "archived" 当**筛选/比较条件**用的位置（片段文本）。

    刻意**不**把 `lo.status = "archived"` 这类赋值算进来：那是"归档别人"，
    是轮153 想鼓励的动作。真正危险的是**读取** archived 之后再写回 active
    （`== "archived"` / `not in (... "archived" ...)` / `.in_([... "archived" ...])`）。
    """
    out: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Compare):
            if any(_has_archived_const(x) for x in [sub.left, *sub.comparators]):
                out.append(ast.unparse(sub)[:140])
        elif isinstance(sub, ast.Call):
            fname = ast.unparse(sub.func)
            if fname.endswith(("in_", "notin_")):
                if any(_has_archived_const(a) for a in sub.args):
                    out.append(ast.unparse(sub)[:140])
    return out


def test_no_automatic_path_revives_archived():
    """自动路径不得把 archived 当成可复活来源（服务层零容忍）。"""
    offenders = []
    for qual, targets, selects in _revivers():
        if not selects:
            continue
        if qual in ALLOWED_REVIVERS:
            continue
        offenders.append(f"{qual} → 写 {targets}；同时按 archived 筛选：{selects}")
    assert not offenders, (
        "以下函数既写 status=\"active\"、又把 archived 当筛选条件 —— "
        "可能把轮153 的归档静默复活：\n  "
        + "\n  ".join(offenders)
        + "\n若确为自动路径，请改成只复活 paused/frozen/terminated。"
    )


def test_manual_resume_endpoint_still_accepts_archived():
    """人手恢复端点必须仍能撤销归档（否则轮153 的清理不可逆）。"""
    path = API / "ai_strategy_routes.py"
    tree = ast.parse(_read(path))
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "resume_strategy"),
        None,
    )
    assert fn is not None, "resume_strategy 端点消失：归档后无法人工撤销"
    body = ast.get_source_segment(_read(path), fn) or ""
    assert "archived" in body, "resume_strategy 不再接受 archived：轮153 的清理变成不可逆"


@pytest.mark.parametrize(
    "rel",
    [
        "services/full_auto/paper_session_helpers.py",
        "services/full_auto/symbol_risk.py",
        "services/strategy_learning_service.py",
    ],
)
def test_known_revive_paths_never_select_archived(rel: str):
    """三条已知自动恢复路径的选择集里不得出现 archived。"""
    for fn in _iter_functions(BACKEND / rel):
        targets = _sets_active(fn)
        if not targets:
            continue
        selects = _selects_archived(fn)
        assert not selects, (
            f"{rel}::{fn.name} 写 status=\"active\" 且按 archived 筛选 {selects} —— "
            f"历史上它只恢复 paused/frozen/terminated"
        )

