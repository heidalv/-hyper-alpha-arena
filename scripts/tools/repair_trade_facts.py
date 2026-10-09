"""trade_facts 数据完整性修复（学习链路输入）。

问题（2026-10-05 实测）：
  ① `manual_close_all` 11 笔合计 +$307,317 —— entry_price=118.97（SOL 的价）被写到
     BTC/ETH 行上，导致 pnl 量纲爆炸。
  ② `pnl` 是**毛利**，手续费在 `fees` 列单独存。学习/归因侧读 `pnl` 会系统性高估。
  ③ 没有净额列 ⇒ "盈利为证"无从验证。

本脚本做三件事（可重复执行、幂等）：
  A. 建 `trade_facts_quarantine` 隔离表，把**证明为坏**的行搬进去（带原因），
     并在 `trade_facts` 删除；坏数据不再污染学习。
  B. `trade_facts` 增列 `net_pnl`（= pnl − fees）与 `price_ok`（价格合理性布尔），
     并回填。net_pnl 成为**权威盈亏口径**。
  C. 打印修复前后对比，供验收。

用法：
    python scripts/tools/repair_trade_facts.py            # 干跑，只报告
    python scripts/tools/repair_trade_facts.py --apply    # 实际执行
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import psycopg  # noqa: E402

DSN = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"

# ── 坏行判据 ────────────────────────────────────────────────────
# 判据 1：entry/exit 对数比绝对值 > 0.5（单笔价格变动 > 65%）→ 不可能是同一仓位
# 判据 2：pnl 绝对值 > 10000 → 与账户规模（约 $128 权益）量纲不符
BAD_SQL = """
    (entry_price > 0 AND exit_price > 0
     AND abs(ln(exit_price / entry_price)) > 0.5)
    OR abs(coalesce(pnl, 0)) > 10000
"""


def q(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际执行（默认干跑）")
    args = ap.parse_args()
    apply = args.apply

    print("=" * 78)
    print(f"trade_facts 修复  mode={'APPLY' if apply else 'DRY-RUN'}")
    print("=" * 78)

    with psycopg.connect(DSN, autocommit=True) as conn:
        cur = conn.cursor()

        # ── 0. 现状 ──────────────────────────────────────────────
        rows = q(cur, "SELECT count(*), round(sum(pnl)::numeric,2), round(sum(fees)::numeric,2) FROM trade_facts")
        print(f"\n[现状] 行数={rows[0][0]}  Σpnl(毛利)=${rows[0][1]}  Σfees=${rows[0][2]}")

        # ── 1. 找出坏行 ──────────────────────────────────────────
        bad = q(cur, f"""
            SELECT id, ts, account_id, position_id, symbol, side, tier,
                   entry_price, exit_price, fees, pnl, close_reason
            FROM trade_facts WHERE {BAD_SQL}
            ORDER BY abs(coalesce(pnl,0)) DESC
        """)
        print(f"\n[坏行] 命中 {len(bad)} 行：")
        print(f"{'id':>6} {'symbol':<7}{'side':<6}{'entry':>14}{'exit':>14}{'pnl':>14}{'reason':<22}")
        for r in bad:
            print(f"{r[0]:>6} {str(r[4])[:6]:<7}{str(r[5])[:5]:<6}{r[7]:>14.4f}{r[8]:>14.4f}"
                  f"{r[10]:>14.2f} {str(r[11])[:21]:<22}")

        if not bad:
            print("  （无坏行，数据已干净）")

        # ── 2. A: 隔离 ───────────────────────────────────────────
        if bad:
            print("\n[A] 建隔离表 trade_facts_quarantine …")
            if apply:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS trade_facts_quarantine (
                        LIKE trade_facts INCLUDING DEFAULTS
                    )
                """)
                cur.execute("""
                    ALTER TABLE trade_facts_quarantine
                        ADD COLUMN IF NOT EXISTS quarantine_reason text,
                        ADD COLUMN IF NOT EXISTS quarantined_at timestamptz DEFAULT now(),
                        ADD COLUMN IF NOT EXISTS src_id bigint
                """)
                cur.execute(f"""
                    INSERT INTO trade_facts_quarantine
                        (ts, source, account_id, position_id, symbol, tier, side,
                         entry_price, exit_price, fees, pnl, outcome, close_reason,
                         factor_exposures, resonance, strategy_id, backfilled,
                         quarantine_reason, src_id)
                    SELECT ts, source, account_id, position_id, symbol, tier, side,
                           entry_price, exit_price, fees, pnl, outcome, close_reason,
                           factor_exposures, resonance, strategy_id, backfilled,
                           '价格量纲异常(entry/exit 对数比>0.5 或 |pnl|>10000)', id
                    FROM trade_facts WHERE {BAD_SQL}
                """)
                n_ins = cur.rowcount
                cur.execute(f"DELETE FROM trade_facts WHERE {BAD_SQL}")
                n_del = cur.rowcount
                print(f"    隔离 {n_ins} 行，从 trade_facts 删除 {n_del} 行")
            else:
                print(f"    [干跑] 将隔离/删除 {len(bad)} 行")

        # ── 3. B: 增列 net_pnl / price_ok 并回填 ─────────────────
        print("\n[B] trade_facts 增加权威净额列 …")
        if apply:
            cur.execute("ALTER TABLE trade_facts ADD COLUMN IF NOT EXISTS net_pnl double precision")
            cur.execute("ALTER TABLE trade_facts ADD COLUMN IF NOT EXISTS price_ok boolean DEFAULT true")
            cur.execute("""
                UPDATE trade_facts
                   SET net_pnl = coalesce(pnl, 0) - coalesce(fees, 0),
                       price_ok = NOT (
                           entry_price > 0 AND exit_price > 0
                           AND abs(ln(exit_price / entry_price)) > 0.5
                       )
            """)
            print(f"    回填 {cur.rowcount} 行")
        else:
            print("    [干跑] 将新增 net_pnl, price_ok 并回填全部行")

        # ── 4. C: 修复后对比 ─────────────────────────────────────
        if apply:
            rows = q(cur, """
                SELECT count(*),
                       round(sum(pnl)::numeric,2),
                       round(sum(net_pnl)::numeric,2),
                       round(sum(fees)::numeric,2)
                FROM trade_facts
            """)
            n, gp, npnl, fe = rows[0]
            print(f"\n[C] 修复后：行数={n}  Σpnl(毛)=${gp}  Σfees=${fe}  Σnet_pnl=${npnl}")
            rows = q(cur, """
                SELECT tier, count(*), round(sum(pnl)::numeric,2), round(sum(net_pnl)::numeric,2)
                FROM trade_facts GROUP BY 1 ORDER BY 1
            """)
            print(f"    {'tier':<8}{'n':>7}{'Σpnl(毛)':>14}{'Σnet_pnl(净)':>15}")
            for r in rows:
                print(f"    {r[0]:<8}{r[1]:>7}{str(r[2]):>14}{str(r[3]):>15}")
            rows = q(cur, "SELECT count(*) FROM trade_facts_quarantine")
            print(f"    隔离表行数 = {rows[0][0]}")

    print("\n" + "=" * 78)
    print("完成" + ("" if apply else "（干跑；加 --apply 实际执行）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
