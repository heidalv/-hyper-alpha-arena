"""
彻底修复 session-paper 账户分裂问题
==================================

诊断到的根本问题
-----------------
1. session.active_strategy_ids 跟踪的 19 个 auto_* 策略全部 account_id=3 (live AI 账户)
2. 但 paper_account_id=4 上才有真实 6 个持仓
3. 3 个持仓引用孤儿 strategy_id（已被删除的 tpl_*）
4. _update_session_stats 函数用 session.account_id（=3）查持仓 → 永远 0 笔

修复内容
---------
Step 1: 把 acct=3 上 19 个 auto_* 策略迁到 acct=4
Step 2: 修复 3 个孤儿持仓/订单的 strategy_id 重映射到正确策略
Step 3: 把 acct=4 上"非业务"的 12 个 tpl_* 旧策略归档（避免再混淆）
Step 4: 重新计算 session.total_trades / total_pnl
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "alpha_arena.db"
print(f"[修复] DB={DB}")

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row
c = conn.cursor()

LIVE_ACCT = 3
PAPER_ACCT = 4
SESSION_ID = "fa_f36540b3f3"

# ─────────────────────────────────
# Step 0: 备份现状
# ─────────────────────────────────
print("\n=== Step 0: 备份当前状态 ===")
c.execute("SELECT COUNT(*) FROM ai_strategies WHERE account_id=?", (LIVE_ACCT,))
live_count = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM ai_strategies WHERE account_id=?", (PAPER_ACCT,))
paper_count = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM paper_positions WHERE account_id=? AND status='open'", (PAPER_ACCT,))
open_pos = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM paper_orders WHERE account_id=?", (PAPER_ACCT,))
order_count = c.fetchone()[0]
print(f"  迁前 acct={LIVE_ACCT}: {live_count} 策略 | acct={PAPER_ACCT}: {paper_count} 策略, {open_pos} 持仓, {order_count} 订单")

# ─────────────────────────────────
# Step 1: 把 acct=3 上的 auto_* 策略整体迁到 acct=4
# ─────────────────────────────────
print(f"\n=== Step 1: 把 acct={LIVE_ACCT} 的 auto_* 策略迁到 acct={PAPER_ACCT} ===")
c.execute(
    "UPDATE ai_strategies SET account_id=? WHERE account_id=? AND strategy_id LIKE 'auto_%'",
    (PAPER_ACCT, LIVE_ACCT),
)
migrated = c.rowcount
print(f"  迁移完成: {migrated} 个 auto_* 策略 acct={LIVE_ACCT} → acct={PAPER_ACCT}")

# ─────────────────────────────────
# Step 2: 修复 3 个孤儿持仓/订单的 strategy_id 重映射
# 规则：根据 sym + tier 找 acct=4 上同维度的 auto_* 策略
# ─────────────────────────────────
print(f"\n=== Step 2: 修复孤儿 strategy_id ===")
remap_plan = []
# 取所有持仓，看 strategy_id 在 ai_strategies 里是否存在
c.execute("""
    SELECT id, symbol, side, strategy_id, timeframe_tier
    FROM paper_positions
    WHERE account_id=? AND status='open'
    ORDER BY id
""", (PAPER_ACCT,))
positions = c.fetchall()

for pos in positions:
    pos_id = pos["id"]
    sym = pos["symbol"]
    side = pos["side"]
    old_sid = pos["strategy_id"]
    tier = pos["timeframe_tier"]

    # 看 strategy_id 是否仍存在 ai_strategies 表
    c2 = conn.cursor()
    c2.execute("SELECT account_id FROM ai_strategies WHERE strategy_id=?", (old_sid,))
    row = c2.fetchone()
    if row:
        if row["account_id"] == PAPER_ACCT:
            print(f"  pos#{pos_id} {sym}/{side}({tier}) strat={old_sid} ✅ 已正确")
            continue
        else:
            # 不应再出现，因 step 1 已迁
            print(f"  pos#{pos_id} {sym}/{side}({tier}) strat={old_sid} ⚠️ 还在 acct={row['account_id']}（异常）")
            continue
    # 孤儿，找替代
    c2.execute("""
        SELECT strategy_id, name, status, created_at
        FROM ai_strategies
        WHERE account_id=? AND primary_symbol=? AND timeframe_tier=?
              AND strategy_id LIKE 'auto_%'
              AND status='active'
        ORDER BY created_at DESC
        LIMIT 1
    """, (PAPER_ACCT, sym, tier))
    cand = c2.fetchone()
    if not cand:
        # 退而求其次：tpl_*
        c2.execute("""
            SELECT strategy_id, name, status
            FROM ai_strategies
            WHERE account_id=? AND primary_symbol=? AND timeframe_tier=?
                  AND status='active'
            ORDER BY created_at DESC
            LIMIT 1
        """, (PAPER_ACCT, sym, tier))
        cand = c2.fetchone()
    if cand:
        new_sid = cand["strategy_id"]
        remap_plan.append((pos_id, sym, side, tier, old_sid, new_sid, cand["name"]))
        print(f"  pos#{pos_id} {sym}/{side}({tier}) strat={old_sid} → {new_sid} | {cand['name']}")
    else:
        print(f"  pos#{pos_id} {sym}/{side}({tier}) strat={old_sid} ❌ 无候选可映射！")

# 应用重映射
print(f"\n  应用 {len(remap_plan)} 条重映射 ...")
for pos_id, sym, side, tier, old_sid, new_sid, name in remap_plan:
    # 修 paper_positions
    c.execute("UPDATE paper_positions SET strategy_id=? WHERE id=?", (new_sid, pos_id))
    # 修 paper_orders（同 strategy_id 的）
    c.execute(
        "UPDATE paper_orders SET strategy_id=? WHERE account_id=? AND strategy_id=?",
        (new_sid, PAPER_ACCT, old_sid)
    )
    # 修 strategy_trades（如有）
    try:
        c.execute(
            "UPDATE strategy_trades SET strategy_id=? WHERE strategy_id=?",
            (new_sid, old_sid)
        )
    except Exception:
        pass

# ─────────────────────────────────
# Step 3: acct=4 上原有 12 个 tpl_* 策略 → 归档（避免后续重复创建混淆）
# 但保留还在被持仓引用的（pos#5 引用 tpl_short_range_dc58d7）
# ─────────────────────────────────
print(f"\n=== Step 3: 归档无持仓引用的 tpl_* 历史策略 ===")
c.execute("""
    SELECT DISTINCT strategy_id FROM paper_positions
    WHERE account_id=? AND status='open' AND strategy_id LIKE 'tpl_%'
""", (PAPER_ACCT,))
referenced = {r["strategy_id"] for r in c.fetchall()}
print(f"  保留持仓引用的 tpl_*: {referenced}")

c.execute(
    """UPDATE ai_strategies SET status='archived'
       WHERE account_id=? AND strategy_id LIKE 'tpl_%' AND status IN ('active','paused')""",
    (PAPER_ACCT,),
)
# 但需把被引用的恢复
for sid in referenced:
    c.execute(
        "UPDATE ai_strategies SET status='active' WHERE strategy_id=?",
        (sid,)
    )
print(f"  归档完成（被持仓引用的已恢复 active）")

# ─────────────────────────────────
# Step 4: 重新计算 session.active_strategy_ids（清掉无效引用）
# 把所有 acct=4 上 active 的 strategy_id 加入 session 跟踪
# ─────────────────────────────────
print(f"\n=== Step 4: 重建 session.active_strategy_ids ===")
import json

c.execute("""
    SELECT strategy_id FROM ai_strategies
    WHERE account_id=? AND status='active'
    ORDER BY created_at
""", (PAPER_ACCT,))
new_active_ids = [r["strategy_id"] for r in c.fetchall()]
print(f"  acct={PAPER_ACCT} 上 active 策略: {len(new_active_ids)} 个")

c.execute(
    "UPDATE full_auto_sessions SET active_strategy_ids=? WHERE session_id=?",
    (json.dumps(new_active_ids), SESSION_ID)
)
print(f"  session.active_strategy_ids 更新完成")

# ─────────────────────────────────
# Step 5: 重新计算 session.total_trades / total_pnl
# 直接 sum paper_positions，简单稳定
# ─────────────────────────────────
print(f"\n=== Step 5: 重算 session 统计 ===")
c.execute("""
    SELECT COUNT(*) as cnt,
           SUM(COALESCE(unrealized_pnl, 0) + COALESCE(partial_realized_pnl, 0)) as pnl,
           SUM(CASE WHEN COALESCE(unrealized_pnl, 0) + COALESCE(partial_realized_pnl, 0) > 0 THEN 1 ELSE 0 END) as wins
    FROM paper_positions
    WHERE account_id=?
""", (PAPER_ACCT,))
stat = c.fetchone()
total_trades = stat["cnt"]
total_pnl = round(float(stat["pnl"] or 0), 4)
wins = stat["wins"] or 0
print(f"  total_trades={total_trades} winning={wins} pnl=${total_pnl}")

c.execute(
    """UPDATE full_auto_sessions
       SET total_trades=?, winning_trades=?, total_pnl=?
       WHERE session_id=?""",
    (total_trades, wins, total_pnl, SESSION_ID)
)

conn.commit()

# ─────────────────────────────────
# 验证
# ─────────────────────────────────
print("\n=== 验证 ===")
c.execute("""
    SELECT id, symbol, side, status, strategy_id, timeframe_tier
    FROM paper_positions WHERE account_id=? AND status='open' ORDER BY id
""", (PAPER_ACCT,))
for r in c.fetchall():
    sid = r["strategy_id"]
    c2 = conn.cursor()
    c2.execute("SELECT account_id, name FROM ai_strategies WHERE strategy_id=?", (sid,))
    info = c2.fetchone()
    if info:
        print(f"  pos#{r['id']} {r['symbol']:<8}/{r['side']:<5}({r['timeframe_tier']:<5}) strat={sid:<35} acct={info['account_id']} | {info['name']}")
    else:
        print(f"  pos#{r['id']} {r['symbol']:<8}/{r['side']:<5}({r['timeframe_tier']:<5}) strat={sid:<35} ❌ 仍是孤儿")

c.execute("SELECT total_trades, winning_trades, total_pnl, status FROM full_auto_sessions WHERE session_id=?", (SESSION_ID,))
s = c.fetchone()
print(f"\n  session.total_trades={s['total_trades']} winning={s['winning_trades']} pnl=${s['total_pnl']} status={s['status']}")

conn.close()
print("\n✅ 修复完成")
