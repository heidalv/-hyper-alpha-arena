"""
回填脚本：补全 ai_strategies 表中 timeframe_tier 和 genome 字段
用法:
    python scripts/backfill_strategy_tier_genome.py          # dry-run 模式（只打印，不写库）
    python scripts/backfill_strategy_tier_genome.py --apply  # 实际执行回填
"""
import sys
import json
import sqlite3
from pathlib import Path

# ── 数据库路径 ──────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "alpha_arena.db"

# ── Timeframe → tier 映射 ────────────────────────────────────────
TF_TO_TIER: dict[str, str] = {
    "1m": "short", "3m": "short", "5m": "short", "15m": "short",
    "30m": "mid",  "1h": "mid",   "2h": "mid",   "4h": "mid",
    "6h": "long",  "8h": "long",  "12h": "long",
    "1d": "long",  "3d": "long",  "1w": "long",  "1M": "long",
}

# ── tier → genome 默认值 ─────────────────────────────────────────
TIER_GENOME: dict[str, dict] = {
    "short": {"trade_nature": "intraday",  "expected_hold_hours": 4},
    "mid":   {"trade_nature": "swing",     "expected_hold_hours": 24},
    "long":  {"trade_nature": "position",  "expected_hold_hours": 168},
}


def infer_tier(timeframe: str | None) -> str:
    if timeframe:
        return TF_TO_TIER.get(timeframe.strip(), "mid")
    return "mid"


def merge_genome(existing: str | None, tier: str) -> str:
    """合并已有 genome（若有）与 tier 默认值，返回 JSON 字符串。"""
    base = dict(TIER_GENOME[tier])
    if existing:
        try:
            current = json.loads(existing)
            if isinstance(current, dict):
                # 已有值优先，只补缺失 key
                base.update(current)   # current 覆盖 base
        except (json.JSONDecodeError, TypeError):
            pass
    return json.dumps(base, ensure_ascii=False)


def run(apply: bool = False) -> None:
    if not DB_PATH.exists():
        print(f"[ERROR] 数据库文件不存在: {DB_PATH}")
        sys.exit(1)

    print(f"[INFO] 数据库: {DB_PATH}")
    print(f"[INFO] 模式: {'APPLY（实际写入）' if apply else 'DRY-RUN（只预览）'}")
    print("=" * 60)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ── 统计总量 ────────────────────────────────────────────────
    cur.execute("SELECT COUNT(*) as cnt FROM ai_strategies")
    total = cur.fetchone()["cnt"]
    print(f"[STAT] 策略总数: {total}")

    # ── 需要回填的行 ─────────────────────────────────────────────
    cur.execute("""
        SELECT id, strategy_id, name, timeframe, timeframe_tier, genome
        FROM ai_strategies
        WHERE timeframe_tier IS NULL OR genome IS NULL
    """)
    rows = cur.fetchall()
    print(f"[STAT] 需要回填（tier 或 genome 为 NULL）: {len(rows)}")

    # ── timeframe 分布统计 ───────────────────────────────────────
    cur.execute("""
        SELECT COALESCE(timeframe, '(NULL)') as tf, COUNT(*) as cnt
        FROM ai_strategies
        WHERE timeframe_tier IS NULL OR genome IS NULL
        GROUP BY tf
        ORDER BY cnt DESC
    """)
    print("\n[STAT] 待回填策略 timeframe 分布:")
    print(f"  {'timeframe':<12}  {'count':>6}  {'→ tier':>8}")
    print("  " + "-" * 35)
    for r in cur.fetchall():
        tier = infer_tier(r["tf"] if r["tf"] != "(NULL)" else None)
        print(f"  {r['tf']:<12}  {r['cnt']:>6}  {'→ ' + tier:>8}")

    print()

    # ── 预览 / 执行 ──────────────────────────────────────────────
    updated = 0
    for row in rows:
        tier = infer_tier(row["timeframe"])

        # genome：如已有值则 merge，否则新建
        new_genome_str = merge_genome(row["genome"], tier)

        tier_changed   = row["timeframe_tier"] != tier
        genome_changed = row["genome"] != new_genome_str

        if not (tier_changed or genome_changed):
            continue  # 已是正确值，跳过

        if not apply:
            print(
                f"  [DRY] id={row['id']:>5}  strategy_id={row['strategy_id']:<30}"
                f"  tf={str(row['timeframe']):<6}  tier={tier:<6}"
                f"  genome={new_genome_str[:60]}..."
            )
        else:
            cur.execute(
                """
                UPDATE ai_strategies
                SET timeframe_tier = ?, genome = ?
                WHERE id = ?
                """,
                (tier, new_genome_str, row["id"]),
            )
        updated += 1

    print(f"\n[STAT] {'将要更新' if not apply else '已更新'} {updated} 条记录")

    if apply:
        conn.commit()
        print("[INFO] 事务已提交")

        # ── 验证 ─────────────────────────────────────────────────
        cur.execute("SELECT COUNT(*) as cnt FROM ai_strategies WHERE timeframe_tier IS NULL")
        null_tier = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) as cnt FROM ai_strategies WHERE genome IS NULL")
        null_genome = cur.fetchone()["cnt"]
        print(f"\n[VERIFY] 回填后 timeframe_tier 仍为 NULL: {null_tier}")
        print(f"[VERIFY] 回填后 genome 仍为 NULL:          {null_genome}")

        cur.execute("""
            SELECT timeframe_tier, COUNT(*) as cnt
            FROM ai_strategies
            GROUP BY timeframe_tier
            ORDER BY cnt DESC
        """)
        print("\n[VERIFY] 最终 tier 分布:")
        for r in cur.fetchall():
            print(f"  {str(r['timeframe_tier']):<8}  {r['cnt']}")
    else:
        print("\n[INFO] 这是 DRY-RUN，未写入数据库。")
        print("[INFO] 执行实际回填请加 --apply 参数：")
        print("       python scripts/backfill_strategy_tier_genome.py --apply")

    conn.close()


if __name__ == "__main__":
    apply_flag = "--apply" in sys.argv
    run(apply=apply_flag)
