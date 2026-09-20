# -*- coding: utf-8 -*-
"""[轮128 2026-09-20] swing 模块退役：卡删掉、依赖面钉死、文档不再说谎。

## 背景（用户原话）
用户看着画布上「旧 swing 模块（非中线！）」这张 **0 行 / 断链** 的卡问：
「这是怎么回事，有用么，没用收删掉」。

## 核实结论（三轮取证，见 scripts/_probe128*.py）
| 引用 | 位置 | 状态 |
|---|---|---|
| `_archive_prompt` | trend_agent.py:389-390 | **活**（prompt 落盘，唯一活引用） |
| `swing_agent.update_thesis` | mlto/qual_layer.py:821-823 | 死路径（唯一调用者 orchestrator 09-05 下线） |
| `import swing_agent` ×3 | master_execution.py | 死导入（AST：名字从未被使用）→ 本轮删除 |
| `is_swing_nature` / `derive_swing_side` | 仅注释/文档 | 0 个生产调用点 |

运行时三重证据：当前日志 `[SwingAgent]` 命中 0；归档最后一条 `[SwingAgent]` =
2026-08-13 16:25:56；`alpha_analytics.llm_usage_logs.call_type` 全表含 swing = **0 条**
（对照 `sync:TrendAgent:review/pyramid` 各 277/253 条且当下仍在写）。

本文件把这些事实钉成 ratchet：谁要把 swing 重新接回生产链路，
**必须**同时更新模块 docstring 的核实表 + 画布的 retired_layers 说明 —— 不允许悄悄复活。
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import agent_wall as W  # noqa: E402

#: 生产代码里**允许**导入 backend.services.swing_agent 的文件（白名单，改动必须走文档）
ALLOWED_IMPORTERS = {
    "backend/services/trend_agent.py",     # _archive_prompt（唯一活引用）
    "backend/services/mlto/qual_layer.py",  # update_thesis（死路径：orchestrator 已下线）
}

PRUNE_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".git", "_archive",
              ".qoder", ".od-skills", "qaa_chromadb", "qaa_memory", "qaa_workflow"}


def _production_py_files():
    for p in (ROOT / "backend").rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if any(part in PRUNE_DIRS for part in p.relative_to(ROOT / "backend").parts[:-1]):
            continue
        if rel.startswith("backend/tests/") or "/tests/" in rel:
            continue
        yield rel, p


def _swing_importers():
    """AST 找真正 import swing_agent 的生产文件。"""
    out = set()
    for rel, p in _production_py_files():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "backend.services.swing_agent":
                out.add(rel)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == "backend.services.swing_agent":
                        out.add(rel)
    return out


def _swing_attr_callers(attrs):
    """AST 找 `swing_agent.<attr>` 形式的生产属性访问，返回 {attr: [file:line]}。"""
    found = {a: [] for a in attrs}
    for rel, p in _production_py_files():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                    and node.value.id == "swing_agent" and node.attr in found:
                found[node.attr].append(f"{rel}:{node.lineno}")
    return found


def _references_swing_agent(rel_path: str) -> list[str]:
    """AST 判断某文件是否**真的**引用 swing_agent（注释里提到不算）。

    为什么不用 `"swing_agent" not in src`：本轮删除死导入时，注释里保留了
    「此处原为 `from ... import swing_agent`，已删」这样的历史说明——
    文本断言会把**解释性注释**误判成"又接回来了"。
    """
    hits = []
    tree = ast.parse((ROOT / rel_path).read_text(encoding="utf-8", errors="replace"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "backend.services.swing_agent":
            hits.append(f"{rel_path}:{node.lineno} from-import")
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "backend.services.swing_agent":
                    hits.append(f"{rel_path}:{node.lineno} import")
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "swing_agent":
            hits.append(f"{rel_path}:{node.lineno} swing_agent.{node.attr}")
        elif isinstance(node, ast.Name) and node.id in ("swing_agent", "_swing_router"):
            hits.append(f"{rel_path}:{node.lineno} name:{node.id}")
    return hits


# ───────────────────── 1. 画布：卡已删、退役说明已写 ─────────────────────

def test_swing_card_removed_from_canvas():
    ids = [n["id"] for n in W.NODES]
    assert "swing_agent" not in ids, (
        "「旧 swing 模块」卡已按用户指令删除（0 行/断链、且它是代码模块不是 agent）；"
        "要恢复请先说明它对应哪个运行时 agent 与产物"
    )


def test_swing_retired_note_present_and_points_to_evidence():
    layers = {r["id"]: r for r in W._retired_layers_note()}
    assert "swing_agent_v1" in layers, "画布必须能回答『为什么这里没有 swing』（retired_layers）"
    note = layers["swing_agent_v1"]
    assert note.get("note") and note.get("doc") and note.get("since")
    # 说明必须指向"唯一活引用"，否则又变成一句无法核对的断言
    assert "_archive_prompt" in note["note"], "退役说明必须写清唯一活引用，避免下次重复排查"
    assert "swing_agent.py" in note["doc"], "必须给出可复核的依据位置"


def test_no_dangling_edges_after_removal():
    ids = {n["id"] for n in W.NODES}
    bad = [e for e in W.EDGES if e["from"] not in ids or e["to"] not in ids]
    assert not bad, f"删除节点后留下悬空边: {bad}"


# ───────────────────── 2. 依赖面：钉死白名单 ─────────────────────

def test_swing_importers_match_documented_whitelist():
    got = _swing_importers()
    assert got == ALLOWED_IMPORTERS, (
        f"生产代码里 import swing_agent 的文件集合变了：{sorted(got)} != {sorted(ALLOWED_IMPORTERS)}。\n"
        "新增引用请先在 backend/services/swing_agent.py 的核实表里写明**调用点与可达性**，"
        "并同步 retired_layers 说明；移除引用则请更新本白名单。"
    )


def test_deprecated_routing_helpers_have_no_production_callers():
    """`is_swing_nature` / `derive_swing_side` 在文档里曾被声称"仍在使用"，实测 0 调用点。"""
    res = _swing_attr_callers({"is_swing_nature", "derive_swing_side"})
    for attr, hits in res.items():
        assert not hits, (
            f"{attr} 出现生产属性访问 {hits} —— 与模块 docstring 的核实表不符，请更新文档"
        )


def test_master_execution_no_longer_references_swing_agent():
    """master 路径与已废弃模块解耦（原 3 处 import 是死导入，轮128 删除）。

    AST 判定：注释里保留的"此处曾 import 已删"历史说明不算引用。
    """
    hits = _references_swing_agent("backend/services/full_auto/master_execution.py")
    assert not hits, (
        f"master_execution.py 又真的引用了 swing_agent：{hits}；"
        "该模块在此处历史上只是死导入（AST 证实名字从未被使用）"
    )


# ───────────────────── 3. 文档不许说谎 ─────────────────────

def test_swing_docstring_documents_reality():
    """模块 docstring 必须给出**可核对**的依赖面，而不是一句"仍在使用"。"""
    src = (ROOT / "backend/services/swing_agent.py").read_text(encoding="utf-8", errors="replace")
    head = src[:6000]
    assert "轮128" in head, "docstring 必须保留本轮核实记录（含日期），便于追溯"
    assert "_archive_prompt" in head, "必须写明唯一活引用是 _archive_prompt"
    assert "死路径" in head and "orchestrator" in head, \
        "必须说明 update_thesis 路径已死（唯一调用者 orchestrator 09-05 下线）"
    assert "死导入" in head, "必须写明 master_execution 的 3 处 import 是死导入"
    # 旧的空口声明不得回归
    assert not re.search(r"`swing_agent\.is_swing_nature` 仍用于路由检测", src), \
        "『is_swing_nature 仍用于路由检测』已被证伪（0 调用点），不得回归"


def test_swing_evidence_archived_in_reports():
    """取证产物必须留在仓库里（可复核），避免结论只存在于聊天记录。

    注意：`scripts/_probe*.py` 被 .gitignore 忽略（只在本机可复跑），
    所以**受版本控制的那一份**是 reports/ 下的报告 + 原始输出。
    """
    rep = ROOT / "reports/_轮128_swing模块退役取证_20260920.md"
    assert rep.exists(), f"取证报告缺失: {rep}"
    txt = rep.read_text(encoding="utf-8")
    for marker in ("_archive_prompt", "orchestrator", "2026-08-13", "call_type"):
        assert marker in txt, f"报告缺少关键证据标记: {marker}"
    for name in ("_probe128_residuals.txt", "_probe128_calltype.txt"):
        assert (ROOT / "reports" / name).exists(), f"取证原始输出缺失: reports/{name}"
