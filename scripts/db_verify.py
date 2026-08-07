"""
Database verification script - Check strategy tier distribution and position tier records
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def check_database():
    import sqlite3
    db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'alpha_arena.db')

    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        db_path = os.path.join(os.path.dirname(__file__), '..', 'backend', 'data', 'alpha_arena.db')
        if not os.path.exists(db_path):
            print(f"Also not found at {db_path}")
            return

    print(f"Using database: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Check strategies
    print("\n=== Strategies (timeframe_tier distribution) ===")
    try:
        cur.execute("""
            SELECT timeframe_tier, COUNT(*) as cnt
            FROM ai_strategies
            GROUP BY timeframe_tier
            ORDER BY cnt DESC
        """)
        rows = cur.fetchall()
        for r in rows:
            print(f"  {r['timeframe_tier']}: {r['cnt']}")
    except Exception as e:
        print(f"  Error: {e}")

    # Check strategy genome trade_nature
    print("\n=== Strategy genomes (trade_nature in genome) ===")
    try:
        cur.execute("""
            SELECT id, name, genome, timeframe_tier
            FROM ai_strategies
            LIMIT 20
        """)
        rows = cur.fetchall()
        for r in rows:
            genome_str = r['genome'] or '{}'
            try:
                import json
                genome = json.loads(genome_str) if isinstance(genome_str, str) else genome_str
                trade_nature = genome.get('trade_nature', 'NOT_SET')
            except:
                trade_nature = 'PARSE_ERROR'
            print(f"  [{r['timeframe_tier']:6s}] {r['name'][:40]:40s} trade_nature={trade_nature!r}")
    except Exception as e:
        print(f"  Error: {e}")

    # Check paper positions
    print("\n=== Paper Positions (timeframe_tier distribution) ===")
    try:
        cur.execute("""
            SELECT timeframe_tier, COUNT(*) as cnt, SUM(entry_value) as total_value
            FROM paper_positions
            WHERE status = 'open'
            GROUP BY timeframe_tier
            ORDER BY cnt DESC
        """)
        rows = cur.fetchall()
        for r in rows:
            total = r['total_value'] or 0
            print(f"  {r['timeframe_tier']}: {r['cnt']} positions, total_value={total:.2f}")
    except Exception as e:
        print(f"  Error: {e}")

    # Check total strategies
    print("\n=== Summary ===")
    try:
        cur.execute("SELECT COUNT(*) as total FROM ai_strategies")
        total_strats = cur.fetchone()['total']
        print(f"  Total strategies: {total_strats}")

        cur.execute("SELECT COUNT(*) as total FROM paper_positions WHERE status='open'")
        total_pos = cur.fetchone()['total']
        print(f"  Total open positions: {total_pos}")
    except Exception as e:
        print(f"  Error: {e}")

    conn.close()
    print("\nDatabase verification complete.")

if __name__ == "__main__":
    check_database()
