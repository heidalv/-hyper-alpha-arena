# -*- coding: utf-8 -*-
"""[F345 验证] 读回路端到端：真实 v7 库副本 + 真开关 + 真留痕（不碰生产库）。

判定四问：
  ① 开闸后，MLTO 车道能否拿到**多 kind** 的教训块（而非 4 条 success_recipe）？
  ② master 车道旧语义（V7_LESSONS_IN_MASTER 默认 true）是否保持？
  ③ 读取是否真的写进 use_count / retrieval_log（"写必有读"可验收）？
  ④ 关闸（默认）时是否逐字节不改行为（空串）？
"""
from __future__ import annotations

import io
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REAL = ROOT / "backend" / "data" / "factor_evolution_memory_v7.db"


def q(db: Path, sql: str, params=()):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def main() -> int:
    print("=" * 78)
    print("F345 读回路端到端验证（生产库副本）")
    print("=" * 78)
    if not REAL.exists():
        print("找不到 v7 库:", REAL)
        return 1
    tmpdir = Path(tempfile.mkdtemp(prefix="v7copy_"))
    copy = tmpdir / "v7.db"
    shutil.copy2(REAL, copy)
    print(f"副本: {copy}")

    before_used = q(copy, "SELECT COUNT(*) FROM v7_lessons WHERE status='active' "
                          "AND COALESCE(use_count,0)>0")[0][0]
    before_total = q(copy, "SELECT COUNT(*) FROM v7_lessons WHERE status='active'")[0][0]
    before_log = q(copy, "SELECT COUNT(*) FROM v7_retrieval_log")[0][0]
    print(f"起始: active={before_total} 被读过={before_used} "
          f"({before_used / before_total:.1%})  retrieval_log={before_log}")

    os.environ["V7_MEMORY_DB_PATH"] = str(copy)
    from backend.services import learning_readback as LR

    print("\n④ 默认（auto）关闸 —— 期望空串、零写入")
    LR._POOL_CACHE.clear()
    blk_off = LR.decision_block(lane="mlto")
    n_log_off = q(copy, "SELECT COUNT(*) FROM v7_retrieval_log")[0][0]
    print(f"   mlto block = {blk_off!r}   retrieval_log 增量 = {n_log_off - before_log}")

    print("\n② master 车道旧语义（V7_LESSONS_IN_MASTER 未设 = true）")
    LR._POOL_CACHE.clear()
    blk_master = LR.decision_block(lane="master")
    print(f"   master block 行数={len(blk_master.splitlines())} 首行={blk_master.splitlines()[0] if blk_master else ''}")
    kinds_master = [ln.split("[")[1].split("|")[0] for ln in blk_master.splitlines() if ln.startswith("- [")]
    print(f"   master 抽到的 kind: {kinds_master}")

    print("\n① 开闸后 MLTO 车道（LEARNING_READBACK_ENABLED=1）")
    os.environ["LEARNING_READBACK_ENABLED"] = "1"
    LR._POOL_CACHE.clear()
    blk_on = LR.decision_block(lane="mlto")
    print(blk_on)
    kinds = [ln.split("[")[1].split("|")[0] for ln in blk_on.splitlines() if ln.startswith("- [")]
    print(f"\n   MLTO 抽到的 kind: {kinds}")
    print(f"   去重后 kind 数={len(set(kinds))}（旧实现恒为 1 种，且 4 条同 kind）")

    print("\n③ 读取留痕（写必有读）")
    after_used = q(copy, "SELECT COUNT(*) FROM v7_lessons WHERE status='active' "
                         "AND COALESCE(use_count,0)>0")[0][0]
    after_log = q(copy, "SELECT COUNT(*) FROM v7_retrieval_log")[0][0]
    print(f"   被读过: {before_used} → {after_used}（+{after_used - before_used}）")
    print(f"   retrieval_log: {before_log} → {after_log}（+{after_log - before_log}）")
    lanes = q(copy, "SELECT query, COUNT(*) FROM v7_retrieval_log WHERE query LIKE '[%' "
                    "GROUP BY query ORDER BY 2 DESC")
    print(f"   按车道留痕: {lanes}")

    print("\n③b 该留痕对 maintenance() 的后果（回归：读过的教训不再被当垃圾退役）")
    from backend.services.evolution import evolution_memory_v7 as EV7
    print(f"   maintenance(30) 预览 = {EV7.maintenance(max_unused_age_days=30)}")

    print("\n④b 生产库未被本次验证改动（只动了副本）")
    real_log = q(REAL, "SELECT COUNT(*) FROM v7_retrieval_log")[0][0]
    print(f"   生产库 retrieval_log 仍为 {real_log}（起始 {before_log}）"
          f"  ⇒ {'未改动' if real_log == before_log else '**被改动，需排查**'}")
    shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
