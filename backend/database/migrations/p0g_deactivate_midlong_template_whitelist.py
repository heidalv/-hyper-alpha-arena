"""
Migration: P0-G mid/long strategy template whitelist governance (DATA)

Design: docs/中长线改造升级设计_2026-08-14.md §4.1 P0-G.

Deactivates mid/long templates that must never be instantiated by the
mid/long tiers:
  R1  reversion family        (category='mean_reversion' or name ~ 'reversion')
  R2  placeholder metrics     (win_rate=1.0 with trades>0)
  R3  zero-backtest outside swing/trend/momentum families
  R4  promoted templates with too few samples (1 <= backtest_total_trades < 40)

Applied on 2026-08-14 against tenant 326 (paper account 14):
  R1+R2+R3: UPDATE 16   R4: UPDATE 3   (19 rows total, ids below)
Backup before applying: postgres_backup/midlong_fixes_*_strategy_templates.dump

Deactivated template ids (19):
  5, 13, 442, 466, 467, 478, 483, 484, 498, 499, 500, 503, 511, 567, 568, 569,
  570, 571, 572

Whitelist that stays ACTIVE (10 templates):
  backtested : [swing] SOL mid, [swing] BNB mid, 中周期动量追踪, 波段趋势回调
  base library (manual pick): 长线摆动交易, 长线趋势跟随, 突破持仓,
    中周期区间交易, 中周期突破, 高波动动量

Idempotent: re-running upgrade() returns rowcount 0 for already-deactivated rows.
"""

from sqlalchemy import text
from backend.database.connection import SessionLocal

DEACTIVATED_IDS = [
    5, 13, 442, 466, 467, 478, 483, 484, 498, 499,
    500, 503, 511, 567, 568, 569, 570, 571, 572,
]


def upgrade():
    db = SessionLocal()
    try:
        print("Starting migration: p0g_deactivate_midlong_template_whitelist")

        # R1+R2+R3
        result = db.execute(text("""
            SET app.is_admin='on'; SET app.tenant_id='326';
            UPDATE strategy_templates SET is_active=false, updated_at=CURRENT_TIMESTAMP
            WHERE tier IN ('mid','long') AND is_active AND (
              name ILIKE '%reversion%' OR category = 'mean_reversion'
              OR (backtest_win_rate = 1.0 AND backtest_total_trades > 0)
              OR (backtest_total_trades = 0 AND category NOT IN ('swing','trend','momentum'))
            )
        """))
        print(f"  R1+R2+R3 deactivated: {result.rowcount}")

        # R4 low-sample promoted
        result = db.execute(text("""
            UPDATE strategy_templates SET is_active=false, updated_at=CURRENT_TIMESTAMP
            WHERE tier IN ('mid','long') AND is_active
              AND backtest_total_trades BETWEEN 1 AND 39 AND source = 'promoted'
        """))
        print(f"  R4 low-sample deactivated: {result.rowcount}")

        # safety net: ensure every id in the audit list is inactive
        result = db.execute(text("""
            UPDATE strategy_templates SET is_active=false, updated_at=CURRENT_TIMESTAMP
            WHERE id = ANY(:ids) AND is_active
        """), {"ids": DEACTIVATED_IDS})
        print(f"  Safety-net id list deactivated: {result.rowcount}")

        db.commit()

        # Verify
        rows = db.execute(text("""
            SELECT COUNT(*) FROM strategy_templates
            WHERE tier IN ('mid','long') AND is_active
        """)).scalar()
        print(f"  Active mid/long templates remaining: {rows}")
        print("✅ Migration completed successfully!")
    except Exception as e:
        db.rollback()
        print(f"❌ Migration failed: {e}")
        raise
    finally:
        db.close()


def downgrade():
    """Restore the 19 audit-listed templates to active."""
    db = SessionLocal()
    try:
        print("Rolling back migration: p0g_deactivate_midlong_template_whitelist")
        result = db.execute(text("""
            UPDATE strategy_templates SET is_active=true, updated_at=CURRENT_TIMESTAMP
            WHERE id = ANY(:ids) AND NOT is_active
        """), {"ids": DEACTIVATED_IDS})
        db.commit()
        print(f"  Restored: {result.rowcount}")
        print("✅ Migration rolled back successfully!")
    except Exception as e:
        db.rollback()
        print(f"❌ Rollback failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "downgrade":
        downgrade()
    else:
        upgrade()
