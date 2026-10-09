"""回填 Hermes L1 提案智慧 — 对历史 paper_validated/rolled_back 提案重新提取。

用法：
    backend\\.venv\\Scripts\\python.exe scripts/hermes_backfill_wisdom.py
    backend\\.venv\\Scripts\\python.exe scripts/hermes_backfill_wisdom.py --force-reextract
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass


def main():
    parser = argparse.ArgumentParser(description="Hermes L1 提案智慧回填")
    parser.add_argument(
        "--force-reextract",
        action="store_true",
        help="删除已有 wisdom 记录后重新提取",
    )
    parser.add_argument("--limit", type=int, default=500, help="最多处理提案数")
    args = parser.parse_args()

    from backend.services.hermes_db import init_hermes_db, hermes_execute
    from backend.services.hermes_proposal_wisdom_engine import proposal_wisdom
    from backend.database.connection import SessionLocal
    from backend.database.models import OpenCodeEvolutionProposalDB

    init_hermes_db()

    db = SessionLocal()
    try:
        rows = (
            db.query(OpenCodeEvolutionProposalDB)
            .filter(
                OpenCodeEvolutionProposalDB.status.in_(["paper_validated", "rolled_back"])
            )
            .order_by(OpenCodeEvolutionProposalDB.id.desc())
            .limit(args.limit)
            .all()
        )
        print(f"待处理提案: {len(rows)} 条")

        if args.force_reextract:
            ids = [r.id for r in rows]
            for pid in ids:
                hermes_execute(
                    "DELETE FROM proposal_wisdom_records WHERE proposal_id=?",
                    (pid,),
                )
            print(f"已清除 {len(ids)} 个 proposal_id 的旧 wisdom 记录")

        extracted = 0
        skipped = 0
        for row in rows:
            wid = proposal_wisdom.extract_wisdom_from_proposal(row.id)
            if wid:
                extracted += 1
            else:
                skipped += 1

        patterns = proposal_wisdom.update_pattern_library()
        print(f"完成: 新提取 {extracted} 条, 跳过 {skipped} 条, 模式库更新 {patterns} 条")
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
