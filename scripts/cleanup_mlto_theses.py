#!/usr/bin/env python3
"""
MLTO Thesis 数据清理脚本（一次性运行）

用途：
  1. 删除 MltoThesis 表中所有 mid tier 记录（中线已不走 MLTO）
  2. 删除 AI 选币 symbol 的 long tier thesis（AI 选币不做长线）
  3. 清空 thesis_store._THESIS_CACHE 内存缓存

用法：
  cd Hyper-Alpha-Arena
  python scripts/cleanup_mlto_theses.py            # 预览（dry-run）
  python scripts/cleanup_mlto_theses.py --execute  # 实际执行删除
"""
from __future__ import annotations

import argparse
import logging
import sys
import os

# 确保能导入 backend 包
_proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cleanup_mlto")

DUMMY_HOLD_REASON = "mid_tier_cleanup"


def main():
    parser = argparse.ArgumentParser(description="清理 MLTO thesis 旧数据")
    parser.add_argument(
        "--execute", action="store_true",
        help="实际执行删除（默认 dry-run 只预览）",
    )
    args = parser.parse_args()

    from backend.database.connection import AnalyticsSessionLocal, SessionLocal
    from backend.services.mlto.db_models import MltoThesis
    from backend.database.models import FullAutoSession

    # ── 步骤 1：统计 mid tier thesis ──
    ana_db = AnalyticsSessionLocal()
    core_db = SessionLocal()
    try:
        mid_rows = ana_db.query(MltoThesis).filter(MltoThesis.tier == "mid").all()
        logger.info("mid tier thesis 记录数: %d", len(mid_rows))

        # ── 步骤 2：收集所有 session 的 auto_coin_symbols ──
        auto_coin_symbols: set[str] = set()
        sessions = core_db.query(FullAutoSession).all()
        for sess in sessions:
            for s in (getattr(sess, "auto_coin_symbols", None) or []):
                sym = str(s).strip().upper()
                if sym:
                    auto_coin_symbols.add(sym)
        logger.info("AI 选币 symbol 合集: %s", sorted(auto_coin_symbols) if auto_coin_symbols else "(空)")

        # ── 步骤 3：统计 AI 选币 long tier thesis ──
        ai_long_rows = []
        if auto_coin_symbols:
            ai_long_rows = (
                ana_db.query(MltoThesis)
                .filter(
                    MltoThesis.tier == "long",
                    MltoThesis.symbol.in_([s.upper() for s in auto_coin_symbols]),
                )
                .all()
            )
        logger.info("AI 选币 long tier thesis 记录数: %d", len(ai_long_rows))

        # ── 步骤 4：dry-run 预览 ──
        if not args.execute:
            logger.info("=" * 60)
            logger.info("DRY-RUN 模式 — 以下数据将被删除（加 --execute 实际执行）：")
            logger.info("  mid tier thesis: %d 条", len(mid_rows))
            logger.info("  AI 选币 long tier thesis: %d 条", len(ai_long_rows))
            if ai_long_rows:
                _syms = sorted({r.symbol.upper() for r in ai_long_rows})
                logger.info("  涉及 symbol: %s", _syms)
            logger.info("=" * 60)
            return

        # ── 步骤 5：实际删除 ──
        deleted_mid = 0
        for row in mid_rows:
            ana_db.delete(row)
            deleted_mid += 1

        deleted_ai_long = 0
        for row in ai_long_rows:
            ana_db.delete(row)
            deleted_ai_long += 1

        ana_db.commit()
        logger.info("已删除 mid tier thesis: %d 条", deleted_mid)
        logger.info("已删除 AI 选币 long tier thesis: %d 条", deleted_ai_long)

        # ── 步骤 6：清空内存缓存 ──
        try:
            from backend.services.mlto import thesis_store
            cache_count = len(thesis_store._THESIS_CACHE)
            thesis_store._THESIS_CACHE.clear()
            logger.info("已清空 _THESIS_CACHE 内存缓存: %d 条", cache_count)
        except Exception as e:
            logger.warning("清空 _THESIS_CACHE 跳过: %s", e)

        logger.info("清理完成！请重启后端进程以彻底释放所有缓存引用。")

    except Exception as e:
        logger.error("清理失败: %s", e, exc_info=True)
        try:
            ana_db.rollback()
        except Exception:
            pass
        sys.exit(1)
    finally:
        try:
            ana_db.close()
        except Exception:
            pass
        try:
            core_db.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
