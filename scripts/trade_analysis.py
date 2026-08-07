"""
近两天交易数据全面分析
"""
import sqlite3, json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

db = r"d:\001Alpha\Hyper-Alpha-Arena\data\alpha_arena.db"
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()

# ═══════════════════════════════════════════════════════
# 1. 盈亏状况分析
# ═══════════════════════════════════════════════════════
print("=" * 70)
print("1. 盈亏状况分析 (近2天)")
print("=" * 70)

# Paper positions closed in last 2 days
cur.execute("""
    SELECT id, symbol, side, entry_price, close_price, margin, unrealized_pnl,
           leverage, timeframe_tier, trade_nature, close_reason,
           partial_realized_pnl, partial_fee_paid, opened_at, closed_at,
           reduce_count, original_margin, tp_price, sl_price, status
    FROM paper_positions
    WHERE closed_at >= ? OR (status = 'open')
    ORDER BY COALESCE(closed_at, opened_at) DESC
""", (cutoff,))

positions = cur.fetchall()
print(f"\n总仓位数: {len(positions)} (含未平仓)")

# Separate open vs closed
open_pos = [p for p in positions if p['status'] == 'open']
closed_pos = [p for p in positions if p['status'] == 'closed']
print(f"  已平仓: {len(closed_pos)}")
print(f"  未平仓: {len(open_pos)}")

# PnL analysis for closed positions
total_pnl = 0
wins = []
losses = []
for p in closed_pos:
    pnl = float(p['unrealized_pnl'] or 0) + float(p['partial_realized_pnl'] or 0)
    fee = float(p['partial_fee_paid'] or 0)
    net_pnl = pnl - fee
    total_pnl += net_pnl
    if net_pnl > 0:
        wins.append(net_pnl)
    else:
        losses.append(net_pnl)

print(f"\n--- 盈亏统计 ---")
print(f"  总净盈亏: {total_pnl:+.2f} USDT")
print(f"  盈利订单数: {len(wins)}")
print(f"  亏损订单数: {len(losses)}")
if wins:
    print(f"  平均盈利: +{sum(wins)/len(wins):.2f} USDT")
    print(f"  最大盈利: +{max(wins):.2f} USDT")
if losses:
    print(f"  平均亏损: {sum(losses)/len(losses):.2f} USDT")
    print(f"  最大亏损: {min(losses):.2f} USDT")
print(f"  胜率: {len(wins)/max(len(closed_pos),1)*100:.1f}%")

# Small loss analysis
small_losses = [l for l in losses if abs(l) < 5]
micro_losses = [l for l in losses if abs(l) < 1]
print(f"\n--- 小额亏损分析 ---")
print(f"  亏损<5U的订单: {len(small_losses)}/{len(losses)} ({len(small_losses)/max(len(losses),1)*100:.0f}%)")
print(f"  亏损<1U的订单: {len(micro_losses)}/{len(losses)} ({len(micro_losses)/max(len(losses),1)*100:.0f}%)")

# ═══════════════════════════════════════════════════════
# 2. 减仓行为分析
# ═══════════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print("2. 减仓行为分析")
print("=" * 70)

# Check reduce_count on positions
reduced = [p for p in positions if (p['reduce_count'] or 0) > 0]
print(f"\n有减仓记录的仓位: {len(reduced)}")
for p in reduced[:15]:
    partial_pnl = float(p['partial_realized_pnl'] or 0)
    partial_fee = float(p['partial_fee_paid'] or 0)
    margin = float(p['original_margin'] or p['margin'] or 0)
    entry = float(p['entry_price'] or 0)
    print(f"  {p['symbol']:10s} side={p['side']:5s} reduce={p['reduce_count']} "
          f"partial_pnl={partial_pnl:+.2f} fee={partial_fee:.2f} "
          f"margin={margin:.2f} entry={entry:.2f} "
          f"tier={p['timeframe_tier']} reason={p['close_reason']}")

# Paper orders for reduce/partial close
print(f"\n--- 减仓订单 (paper_orders close_reason LIKE partial%) ---")
cur.execute("""
    SELECT symbol, side, filled_price, quantity, filled_quantity, fee, pnl,
           close_reason, trade_nature, created_at, filled_at
    FROM paper_orders
    WHERE close_reason LIKE '%partial%' OR close_reason LIKE '%reduce%'
    ORDER BY created_at DESC
    LIMIT 30
""")
reduce_orders = cur.fetchall()
print(f"  减仓订单数: {len(reduce_orders)}")
for o in reduce_orders[:15]:
    print(f"  {str(o['created_at'])[:19]:20s} {o['symbol']:10s} {o['side']:5s} "
          f"price={o['filled_price']} qty={o['filled_quantity']} "
          f"pnl={o['pnl']} fee={o['fee']} reason={o['close_reason']}")

# ═══════════════════════════════════════════════════════
# 3. 策略类型分布
# ═══════════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print("3. 策略类型分布")
print("=" * 70)

# By tier - all positions
print("\n--- 仓位 tier 分布 (所有) ---")
cur.execute("""
    SELECT timeframe_tier, status, COUNT(*) as cnt, SUM(margin) as total_margin
    FROM paper_positions
    GROUP BY timeframe_tier, status
    ORDER BY status, timeframe_tier
""")
for r in cur.fetchall():
    margin = r['total_margin'] or 0
    print(f"  tier={r['timeframe_tier']:6s} status={r['status']:8s} count={r['cnt']} margin={margin:.2f}")

# trade_nature distribution
print("\n--- 仓位 trade_nature 分布 ---")
cur.execute("""
    SELECT trade_nature, COUNT(*) as cnt FROM paper_positions GROUP BY trade_nature
""")
for r in cur.fetchall():
    print(f"  nature={str(r['trade_nature']):15s} count={r['cnt']}")

# Active strategies by tier
print("\n--- 活跃策略 tier 分布 ---")
cur.execute("""
    SELECT timeframe_tier, COUNT(*) as cnt FROM ai_strategies
    WHERE status = 'active' GROUP BY timeframe_tier
""")
for r in cur.fetchall():
    print(f"  tier={r['timeframe_tier']:6s} active={r['cnt']}")

# ═══════════════════════════════════════════════════════
# 4. 策略复用机制
# ═══════════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print("4. 策略复用与记忆机制")
print("=" * 70)

# Strategy memory
cur.execute("SELECT strategy_id, total_trades, win_rate, avg_profit, avg_loss FROM strategy_memories")
memories = cur.fetchall()
print(f"\n  策略记忆记录数: {len(memories)}")
for m in memories[:10]:
    print(f"  sid={m['strategy_id'][:15]}... trades={m['total_trades']} "
          f"win_rate={m['win_rate']:.0%} avg_profit={m['avg_profit']:.2f} avg_loss={m['avg_loss']:.2f}")

# Strategy trades
cur.execute("SELECT COUNT(*) as cnt FROM strategy_trades")
print(f"\n  策略交易明细数: {cur.fetchone()['cnt']}")

# How many strategies were reused (active with trades > 0)
cur.execute("""
    SELECT COUNT(*) as cnt FROM ai_strategies s
    LEFT JOIN strategy_memories m ON s.strategy_id = m.strategy_id
    WHERE s.status = 'active' AND m.total_trades > 0
""")
reused = cur.fetchone()['cnt']
cur.execute("SELECT COUNT(*) as cnt FROM ai_strategies WHERE status = 'active'")
total_active = cur.fetchone()['cnt']
print(f"\n  有交易记录的活跃策略: {reused}/{total_active}")

# ═══════════════════════════════════════════════════════
# 5. 交易效率问题
# ═══════════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print("5. 交易效率分析")
print("=" * 70)

# All paper orders in last 2 days
cur.execute("""
    SELECT symbol, side, order_type, filled_price, quantity, filled_quantity,
           fee, pnl, close_reason, trade_nature, status, created_at, filled_at,
           tp_price, sl_price, strategy_id
    FROM paper_orders
    WHERE created_at >= ?
    ORDER BY created_at DESC
""", (cutoff,))
orders = cur.fetchall()

print(f"\n近2天总订单数: {len(orders)}")

# Order type breakdown
order_types = defaultdict(int)
for o in orders:
    order_types[o['close_reason'] or 'OPEN'] += 1
print("\n--- 订单类型分布 ---")
for k, v in sorted(order_types.items(), key=lambda x: -x[1]):
    print(f"  {k:25s}: {v}")

# Fee analysis
total_fee = sum(float(o['fee'] or 0) for o in orders)
total_realized = sum(float(o['pnl'] or 0) for o in orders)
print(f"\n--- 手续费影响 ---")
print(f"  总手续费: {total_fee:.2f} USDT")
print(f"  总实现盈亏: {total_realized:+.2f} USDT")
print(f"  净盈亏: {total_realized - total_fee:+.2f} USDT")
print(f"  手续费占比: {total_fee / max(abs(total_realized), 0.01) * 100:.1f}%")

# Symbol-level PnL
print("\n--- 各币种盈亏 ---")
cur.execute("""
    SELECT symbol,
           COUNT(*) as cnt,
           SUM(CASE WHEN unrealized_pnl > 0 THEN 1 ELSE 0 END) as wins,
           SUM(unrealized_pnl) as total_pnl,
           SUM(partial_realized_pnl) as partial_pnl,
           SUM(partial_fee_paid) as total_fee
    FROM paper_positions
    WHERE closed_at >= ? OR status = 'open'
    GROUP BY symbol
    ORDER BY total_pnl ASC
""", (cutoff,))
for r in cur.fetchall():
    pnl = float(r['total_pnl'] or 0) + float(r['partial_pnl'] or 0)
    fee = float(r['total_fee'] or 0)
    net = pnl - fee
    print(f"  {r['symbol']:10s} trades={r['cnt']} wins={r['wins']} "
          f"pnl={pnl:+.2f} fee={fee:.2f} net={net:+.2f}")

# Close reason analysis
print("\n--- 平仓原因分布 ---")
cur.execute("""
    SELECT close_reason, COUNT(*) as cnt,
           AVG(unrealized_pnl) as avg_pnl,
           MIN(unrealized_pnl) as min_pnl,
           MAX(unrealized_pnl) as max_pnl
    FROM paper_positions
    WHERE status = 'closed'
    GROUP BY close_reason
    ORDER BY cnt DESC
""")
for r in cur.fetchall():
    print(f"  {str(r['close_reason']):25s} count={r['cnt']} "
          f"avg_pnl={r['avg_pnl']:+.2f} range=[{r['min_pnl']:+.2f}, {r['max_pnl']:+.2f}]")

# Holding time analysis
print("\n--- 持仓时间分析 ---")
cur.execute("""
    SELECT symbol, side, entry_price, close_price, unrealized_pnl,
           partial_fee_paid, reduce_count, close_reason,
           opened_at, closed_at, timeframe_tier
    FROM paper_positions
    WHERE status = 'closed' AND opened_at >= ?
    ORDER BY opened_at DESC
""", (cutoff,))
trades_with_time = cur.fetchall()
hold_times = []
for t in trades_with_time:
    try:
        o = datetime.fromisoformat(t['opened_at'].replace('Z', '+00:00').replace(' ', 'T'))
        c = datetime.fromisoformat(t['closed_at'].replace('Z', '+00:00').replace(' ', 'T'))
        hold_min = (c - o).total_seconds() / 60
        hold_times.append((hold_min, t))
    except:
        pass

if hold_times:
    avg_hold = sum(h[0] for h in hold_times) / len(hold_times)
    short_trades = [h for h in hold_times if h[0] < 30]
    print(f"  平均持仓时间: {avg_hold:.0f} 分钟 ({avg_hold/60:.1f} 小时)")
    print(f"  持仓<30分钟的: {len(short_trades)}/{len(hold_times)}")
    for h, t in short_trades[:10]:
        pnl = float(t['unrealized_pnl'] or 0)
        fee = float(t['partial_fee_paid'] or 0)
        print(f"    {t['symbol']:10s} {h:.0f}min pnl={pnl:+.2f} fee={fee:.2f} reason={t['close_reason']}")

# ═══════════════════════════════════════════════════════
# 6. 订单时间线
# ═══════════════════════════════════════════════════════
print(f"\n{'=' * 70}")
print("6. 近2天完整订单时间线")
print("=" * 70)
for o in orders[:40]:
    ts = str(o['created_at'] or '')[:19]
    reason = o['close_reason'] or 'OPEN'
    pnl = float(o['pnl'] or 0)
    fee = float(o['fee'] or 0)
    print(f"  {ts} {o['symbol']:10s} {o['side']:5s} "
          f"price={o['filled_price']} qty={o['filled_quantity']} "
          f"pnl={pnl:+.2f} fee={fee:.2f} {reason}")

conn.close()
print("\n\n分析完成。")
