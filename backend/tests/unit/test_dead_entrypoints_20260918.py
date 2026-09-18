"""P2-12 回归：死分支已删、未接线的公开入口已被标注（且标注不得失真）。

事故背景（轮66 审计 P2-12）
--------------------------
1. `backend/services/full_auto/loops/trading_cycle_loop.py` 的第三个分支
   `elif MIDLONG_AI_MANDATORY and len(active_ids) > max_strategies:`（中长线优先限流）
   **永远进不去**：`_full_symbol_coverage = FULLAUTO_AI_DOMINANT or MIDLONG_AI_MANDATORY`，
   走到该 elif 时若 `_full_symbol_coverage` 为假则 `MIDLONG_AI_MANDATORY` 必为假；
   若为真则 `max_strategies = len(active_ids)`（:87），判据退化成
   `len(active_ids) > len(active_ids)` 恒 False。
2. `backend/services/full_auto/midlong_position_manager.py:1869` 的
   `run_position_management_for_session()` 全库无调用者，但文档语气像是已接线的能力。

修法（轮93）：删除死分支（保留一段说明其不可达性的证明），
并给未接线的公开入口加上明确的"当前无调用者"标注 + 本测试把事实钉住。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_LOOP = ROOT / "backend" / "services" / "full_auto" / "loops" / "trading_cycle_loop.py"
_MPM = ROOT / "backend" / "services" / "full_auto" / "midlong_position_manager.py"


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(re.sub(r"\s+#\s.*$", "", line))
    return "\n".join(out)


# ── trading_cycle_loop：死分支已删 ────────────────────────────


def test_dead_midlong_throttle_branch_is_gone():
    live = _strip_comments(_LOOP.read_text(encoding="utf-8"))
    assert not re.search(r"elif MIDLONG_AI_MANDATORY and len\(active_ids\) > max_strategies", live), (
        "恒 False 的 elif 必须删除，不得留在活代码里"
    )
    assert "中长线优先限流: " not in live, "该分支的日志语句也应随之消失"
    assert "midlong_rows" not in live and "short_rows" not in live


def test_removal_proof_is_documented():
    """删除必须留证明，否则下一个人会以为是误删。"""
    src = _LOOP.read_text(encoding="utf-8")
    assert "轮93 修 P2-12" in src
    assert "len(active_ids) > len(active_ids)" in src, "要写出退化成恒 False 的那一步"
    assert "穷举全部 flag 组合" in src


def test_surviving_throttle_branch_is_reachable():
    """第一个限流分支必须保留且仍可达（`not _full_symbol_coverage` 且超预算）。"""
    live = _strip_comments(_LOOP.read_text(encoding="utf-8"))
    assert re.search(
        r"if not _full_symbol_coverage and len\(active_ids\) > max_strategies:", live
    )
    assert "批量限流" in live


def test_max_strategies_still_len_active_ids_in_full_coverage_mode():
    """删除依据的前提必须仍然成立：全币种覆盖模式下 max_strategies == len(active_ids)。"""
    live = _strip_comments(_LOOP.read_text(encoding="utf-8"))
    assert re.search(r"_full_symbol_coverage = FULLAUTO_AI_DOMINANT or MIDLONG_AI_MANDATORY", live)
    assert re.search(r"max_strategies = max\(1, len\(active_ids\) or 1\)", live), (
        "若这里改成显式预算，恒 False 的论证就失效了 —— 那时可以重新考虑中长线优先限流"
    )


# ── midlong_position_manager：未接线入口已标注 ────────────────


def test_unwired_entrypoint_is_documented_as_unwired():
    src = _MPM.read_text(encoding="utf-8")
    anchor = src.index("def run_position_management_for_session(")
    doc = src[anchor: anchor + 1600]
    assert "当前全仓无任何调用者" in doc, "必须明确写出没有调用者"
    assert "P2-12" in doc
    assert "test_dead_entrypoints_20260918" in doc, "要指出改接线时该同步哪个测试"


def test_unwired_entrypoint_has_no_callers_anywhere():
    """把事实钉住：全仓（除本文件定义与本测试）不得出现该名字。

    只扫代码目录（backend / scripts / mobile），不 rglob 整个仓库 ——
    仓库里有 node_modules 与归档目录，全量读盘会把测试拖到超时。
    一旦有人真的接上线，本测试会失败 —— 那时请按提示更新 docstring 与本断言。
    """
    hits: list[str] = []
    skip_dirs = {"__pycache__", "node_modules", "_audit_ml", "_archive", ".git"}
    roots = [ROOT / "backend", ROOT / "scripts", ROOT / "mobile"]
    for base in roots:
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if any(
                part in skip_dirs or part.startswith(".pytest_tmp") for part in p.parts
            ):
                continue
            if p == Path(__file__) or p.stat().st_size > 2_000_000:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if "run_position_management_for_session" not in text:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if "run_position_management_for_session" in line:
                    hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    call_sites = [h for h in hits if "def run_position_management_for_session" not in h]
    assert not call_sites, (
        "该入口已被接线？请更新 midlong_position_manager 的 docstring 与本断言：\n"
        + "\n".join(call_sites)
    )


def test_entrypoint_body_is_still_valid_python():
    """保留（而非删除）的前提：它必须是可用的正确实现，不是半成品。"""
    src = _MPM.read_text(encoding="utf-8")
    ast.parse(src)
    anchor = src.index("def run_position_management_for_session(")
    body = src[anchor:]
    assert "manage_position(" in body, "批量入口必须真的委托给单仓实现"
    assert "SessionLocal()" in body, "每 symbol 独立连接（线程安全）必须保留"
    assert "ThreadPoolExecutor" in body


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
