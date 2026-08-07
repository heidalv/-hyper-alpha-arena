"""
因子清洗脚本（P1.2，方案 §P1.2，幂等可重跑）。

遍历 backend/services/factor_engine/factors/ai_generated/*.py（987 个损坏/重复的 AI 因子），
经 purge_pipeline 清洗为 ≤50 个表达式化因子。

由于 987 个因子中 162 个连语法都不通过（P0.6 CI 已发现），且大多是自由 Python 类
（无法转译为表达式 AST），本脚本的作用是：
    1. 证实清洗逻辑可对真实因子目录运行
    2. 将损坏/无法转译的因子归档（附原因）
    3. 输出清洗报告

注：真正的因子转译（自由代码 → 表达式 AST）需要逐因子人工/LLM 辅助，
    因为 987 个因子各有不同结构。本脚本完成"能跑通 + 归档损坏 + 报告"部分，
    转译后的表达式因子进活跃集（≤50）由 P4 CodegenAgent 持续补充。

用法：
    python scripts/purge_factors.py --dry-run   # 仅报告，不移动文件
    python scripts/purge_factors.py             # 执行清洗 + 归档
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FACTORS_DIR = BACKEND / "services" / "factor_engine" / "factors" / "ai_generated"
QUARANTINE_DIR = BACKEND / "services" / "factor_engine" / "factors" / "_ai_gen_quarantine"
ARCHIVE_DIR = ROOT / "_archive" / "factors_purged"
sys.path.insert(0, str(ROOT))

from backend.services.factor_engine.purge_pipeline import (
    CandidateFactor,
    stage1_static_audit,
)


def collect_candidates() -> list[CandidateFactor]:
    """
    收集 987 个 AI 因子为候选。
    由于它们是自由 Python 类（无法转译 AST），expr_ast=None，
    stage1 会直接 REJECTED（这正是诊断的结论：无纪律因子全淘汰）。
    """
    candidates = []
    if not FACTORS_DIR.exists():
        return candidates
    for py in sorted(FACTORS_DIR.glob("*.py")):
        if py.name == "__init__.py":
            continue
        candidates.append(CandidateFactor(
            factor_id=py.stem,
            source_name=str(py.relative_to(ROOT)),
            expr_ast=None,  # 自由 Python 类，无法转译
        ))
    return candidates


def run(dry_run: bool = True) -> int:
    candidates = collect_candidates()
    print(f"收集到 {len(candidates)} 个 AI 因子候选")

    # Stage 1: 静态审计（自由 Python 类全部 REJECTED）
    surv, rej = stage1_static_audit(candidates)
    print("\n阶段1 静态审计：")
    print(f"  通过：{len(surv)}（可转译表达式 AST）")
    print(f"  拒绝：{len(rej)}（自由 Python 类 / 损坏）")

    if dry_run:
        print("\n[dry-run] 不移动文件。以下因子将被归档：")
        for c in rej[:10]:
            print(f"  - {c.factor_id}: {c.reject_reason}")
        if len(rej) > 10:
            print(f"  ... 共 {len(rej)} 个")
        print(f"\n清洗报告：输入 {len(candidates)} → 静态拒 {len(rej)} → 幸存 {len(surv)}")
        return 0

    # 实际归档
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archived = 0
    for c in rej:
        src = FACTORS_DIR / f"{c.factor_id}.py"
        if src.exists():
            dst = ARCHIVE_DIR / f"{c.factor_id}.py"
            shutil.move(str(src), str(dst))
            # 写原因日志
            (ARCHIVE_DIR / f"{c.factor_id}.reason.txt").write_text(
                c.reject_reason, encoding="utf-8"
            )
            archived += 1
    print(f"\n已归档 {archived} 个因子到 {ARCHIVE_DIR}")
    print(f"清洗报告：输入 {len(candidates)} → 归档 {archived} → 剩余 {len(surv)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="因子清洗（987 → ≤50）")
    ap.add_argument("--dry-run", action="store_true", help="仅报告，不移动文件")
    args = ap.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
