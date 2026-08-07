#!/usr/bin/env python3
"""一次性清理：删除库里 tier='mid' 的 MLTO thesis 及其关联记录。

背景（2026-07-03，三层独立架构）：
  中线(mid)已改为走 SwingAgent 独立决策，不再走 MLTO。重启前遗留的旧 mid thesis
  会一直停留在 Analytics 库里（review_count 很高、updated_at 冻结），前端 thesis
  汇总接口若不过滤就会显示"中线僵尸卡片"。

  Fix A 已在接口层过滤 mid；本脚本额外把这些死记录从库里删掉，保持数据干净。

清理范围（AnalyticsDB）：
  - mlto_thesis            where tier='mid'
  - mlto_memory_events     关联这些 thesis_id
  - mlto_thesis_events     关联这些 thesis_id
  - mlto_debate_log        关联这些 thesis_id
  - mlto_signal_weights    where tier='mid'

用法：
  # 预览（不删）
  python scripts/cleanup_mid_theses_2026_07_03.py --dry-run
  # 实际删除
  python scripts/cleanup_mid_theses_2026_07_03.py --apply
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


def main() -> int:
    parser = argparse.ArgumentParser(description="清理旧的中线(mid) MLTO thesis")
    parser.add_argument("--apply", action="store_true", help="实际执行删除（默认只预览）")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不删除")
    args = parser.parse_args()
    do_apply = args.apply and not args.dry_run

    from backend.database.connection import AnalyticsSessionLocal
    from backend.services.mlto.db_models import (
        MltoThesis,
        MltoMemoryEvent,
        MltoThesisEvent,
        MltoDebateLog,
        MltoSignalWeight,
    )

    db = AnalyticsSessionLocal()
    try:
        mid_theses = db.query(MltoThesis).filter(MltoThesis.tier == "mid").all()
        thesis_ids = [t.thesis_id for t in mid_theses]

        print(f"=== 清理旧中线 MLTO thesis ({'APPLY' if do_apply else 'DRY-RUN'}) ===")
        print(f"命中 mid thesis 行数: {len(mid_theses)}")
        for t in mid_theses[:50]:
            print(
                f"  - {t.session_id[:12]} {t.symbol:<8} review={t.review_count:<4} "
                f"readiness={t.open_readiness:<3} updated={t.updated_at}"
            )
        if len(mid_theses) > 50:
            print(f"  ... 其余 {len(mid_theses) - 50} 行省略")

        # 关联记录计数
        n_mem = n_evt = n_deb = 0
        if thesis_ids:
            n_mem = db.query(MltoMemoryEvent).filter(MltoMemoryEvent.thesis_id.in_(thesis_ids)).count()
            n_evt = db.query(MltoThesisEvent).filter(MltoThesisEvent.thesis_id.in_(thesis_ids)).count()
            n_deb = db.query(MltoDebateLog).filter(MltoDebateLog.thesis_id.in_(thesis_ids)).count()
        n_weight = db.query(MltoSignalWeight).filter(MltoSignalWeight.tier == "mid").count()

        print(f"关联 memory_events: {n_mem} | thesis_events: {n_evt} | debate_log: {n_deb}")
        print(f"mid signal_weights: {n_weight}")

        if not do_apply:
            print("\n[DRY-RUN] 未删除任何数据。加 --apply 执行删除。")
            return 0

        deleted = 0
        if thesis_ids:
            deleted += db.query(MltoMemoryEvent).filter(
                MltoMemoryEvent.thesis_id.in_(thesis_ids)
            ).delete(synchronize_session=False)
            deleted += db.query(MltoThesisEvent).filter(
                MltoThesisEvent.thesis_id.in_(thesis_ids)
            ).delete(synchronize_session=False)
            deleted += db.query(MltoDebateLog).filter(
                MltoDebateLog.thesis_id.in_(thesis_ids)
            ).delete(synchronize_session=False)
        deleted += db.query(MltoSignalWeight).filter(
            MltoSignalWeight.tier == "mid"
        ).delete(synchronize_session=False)
        deleted += db.query(MltoThesis).filter(
            MltoThesis.tier == "mid"
        ).delete(synchronize_session=False)
        db.commit()

        print(f"\n[APPLY] 已删除关联+主表共 {deleted} 行（含 {len(mid_theses)} 条 mid thesis）。")
        return 0
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"[ERROR] 清理失败已回滚: {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
