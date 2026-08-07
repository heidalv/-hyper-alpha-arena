"""
Trading System Diagnostic Report
Analyzes alpha_arena.db to identify trading performance issues
"""
import sqlite3
import json
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = r"d:\001Alpha\Hyper-Alpha-Arena\data\alpha_arena.db"
TAKER_FEE_RATE = 0.0006  # 0.06% per side

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
c = conn.cursor()

now_str = "2026-04-10 12:00:00"
three_days_ago = "2026-04-07 00:00:00"

print("=" * 80)
print("  ALPHA ARENA - TRADING SYSTEM DIAGNOSTIC REPORT")
print(f"  Analysis window: {three_days_ago} ~ {now_str}")
print("=" * 80)

# ============================================================
# SECTION 1: Paper Orders Analysis
# ============================================================
print("\n" + "=" * 80)
print("  SECTION 1: PAPER ORDERS ANALYSIS")
print("=" * 80)

c.execute("SELECT COUNT(*) FROM paper_orders WHERE created_at >= ?", (three_days_ago,))
total_orders = c.fetchone()[0]
print(f"\n  Total orders (last 3 days): {total_orders}")

c.execute("SELECT COUNT(DISTINCT symbol) FROM paper_orders WHERE created_at >= ?", (three_days_ago,))
unique_symbols = c.fetchone()[0]
print(f"  Unique symbols traded: {unique_symbols}")

c.execute("SELECT DISTINCT symbol FROM paper_orders WHERE created_at >= ?", (three_days_ago,))
symbols = [r[0] for r in c.fetchall()]
print(f"  Symbols: {', '.join(symbols)}")

# Opening orders (close_reason IS NULL) vs Closing orders (close_reason IS NOT NULL)
c.execute("""
    SELECT 
        SUM(CASE WHEN close_reason IS NULL THEN 1 ELSE 0 END) as opens,
        SUM(CASE WHEN close_reason IS NOT NULL THEN 1 ELSE 0 END) as closes
    FROM paper_orders WHERE created_at >= ?
""", (three_days_ago,))
row = c.fetchone()
open_count, close_count = row['opens'], row['closes']
print(f"  Opening orders: {open_count}")
print(f"  Closing orders: {close_count}")

# --- Close Reason Distribution ---
print("\n  --- Close Reason Distribution ---")
c.execute("""
    SELECT close_reason, COUNT(*) as cnt 
    FROM paper_orders 
    WHERE created_at >= ? AND close_reason IS NOT NULL
    GROUP BY close_reason 
    ORDER BY cnt DESC
""", (three_days_ago,))
close_reasons = c.fetchall()
for r in close_reasons:
    pct = r['cnt'] / close_count * 100 if close_count > 0 else 0
    print(f"    {r['close_reason']:30s}  {r['cnt']:4d}  ({pct:5.1f}%)")

# --- Holding Time Analysis ---
print("\n  --- Holding Time Analysis ---")
# Match open orders (close_reason IS NULL) with subsequent close orders for same symbol
c.execute("""
    SELECT id, symbol, side, filled_price, quantity, fee, pnl, created_at, close_reason
    FROM paper_orders 
    WHERE created_at >= ?
    ORDER BY symbol, created_at
""", (three_days_ago,))
all_orders = c.fetchall()

# Build open/close pairs per symbol
open_positions = {}  # symbol -> list of open orders
trades = []  # completed trade pairs

for o in all_orders:
    sym = o['symbol']
    if o['close_reason'] is None:
        # Opening order
        if sym not in open_positions:
            open_positions[sym] = []
        open_positions[sym].append(o)
    else:
        # Closing order - match with earliest open of same symbol
        if sym in open_positions and open_positions[sym]:
            opener = open_positions[sym][0]
            open_time = datetime.strptime(opener['created_at'], "%Y-%m-%d %H:%M:%S") if '.' not in opener['created_at'] else datetime.strptime(opener['created_at'].split('.')[0], "%Y-%m-%d %H:%M:%S")
            close_time = datetime.strptime(o['created_at'], "%Y-%m-%d %H:%M:%S") if '.' not in o['created_at'] else datetime.strptime(o['created_at'].split('.')[0], "%Y-%m-%d %H:%M:%S")
            holding_seconds = (close_time - open_time).total_seconds()
            
            # Notional value for fee estimation
            open_notional = opener['filled_price'] * opener['quantity'] if opener['filled_price'] and opener['quantity'] else 0
            close_notional = o['filled_price'] * o['quantity'] if o['filled_price'] and o['quantity'] else 0
            estimated_fee = (open_notional + close_notional) * TAKER_FEE_RATE
            
            trades.append({
                'symbol': sym,
                'open_side': opener['side'],
                'close_reason': o['close_reason'],
                'holding_seconds': holding_seconds,
                'pnl': o['pnl'] if o['pnl'] is not None else 0,
                'recorded_fee': (opener['fee'] or 0) + (o['fee'] or 0),
                'estimated_fee': estimated_fee,
                'open_notional': open_notional,
                'close_notional': close_notional,
                'open_time': opener['created_at'],
                'close_time': o['created_at'],
            })
            
            # If close quantity >= open quantity, remove the opener
            if o['quantity'] and opener['quantity'] and o['quantity'] >= opener['quantity'] * 0.9:
                open_positions[sym].pop(0)
        else:
            # Close without matching open (maybe opened before our window)
            close_notional = o['filled_price'] * o['quantity'] if o['filled_price'] and o['quantity'] else 0
            trades.append({
                'symbol': sym,
                'open_side': '?',
                'close_reason': o['close_reason'],
                'holding_seconds': None,
                'pnl': o['pnl'] if o['pnl'] is not None else 0,
                'recorded_fee': o['fee'] or 0,
                'estimated_fee': close_notional * TAKER_FEE_RATE * 2,  # estimate both sides
                'open_notional': 0,
                'close_notional': close_notional,
                'open_time': None,
                'close_time': o['created_at'],
            })

timed_trades = [t for t in trades if t['holding_seconds'] is not None]
if timed_trades:
    avg_hold = sum(t['holding_seconds'] for t in timed_trades) / len(timed_trades)
    min_hold = min(t['holding_seconds'] for t in timed_trades)
    max_hold = max(t['holding_seconds'] for t in timed_trades)
    median_hold = sorted(t['holding_seconds'] for t in timed_trades)[len(timed_trades)//2]
    
    def fmt_time(s):
        if s < 60: return f"{s:.0f}s"
        if s < 3600: return f"{s/60:.1f}min"
        return f"{s/3600:.1f}hr"
    
    print(f"    Matched trade pairs: {len(timed_trades)}")
    print(f"    Average holding time:  {fmt_time(avg_hold)}")
    print(f"    Median holding time:   {fmt_time(median_hold)}")
    print(f"    Min holding time:      {fmt_time(min_hold)}")
    print(f"    Max holding time:      {fmt_time(max_hold)}")
    
    # Holding time buckets
    buckets = {"<5min": 0, "5-30min": 0, "30min-2hr": 0, "2-8hr": 0, "8-24hr": 0, ">24hr": 0}
    for t in timed_trades:
        s = t['holding_seconds']
        if s < 300: buckets["<5min"] += 1
        elif s < 1800: buckets["5-30min"] += 1
        elif s < 7200: buckets["30min-2hr"] += 1
        elif s < 28800: buckets["2-8hr"] += 1
        elif s < 86400: buckets["8-24hr"] += 1
        else: buckets[">24hr"] += 1
    
    print("\n    Holding time distribution:")
    for bucket, cnt in buckets.items():
        pct = cnt / len(timed_trades) * 100
        bar = "#" * int(pct / 2)
        print(f"      {bucket:10s}  {cnt:3d}  ({pct:5.1f}%)  {bar}")

# --- PnL vs Fee Analysis ---
print("\n  --- PnL vs Estimated Fee Analysis ---")
if trades:
    total_pnl = sum(t['pnl'] for t in trades)
    total_recorded_fee = sum(t['recorded_fee'] for t in trades)
    total_estimated_fee = sum(t['estimated_fee'] for t in trades)
    
    fee_negative_count = 0
    fee_negative_trades = []
    for t in trades:
        if abs(t['pnl']) < t['estimated_fee'] and t['pnl'] != 0:
            fee_negative_count += 1
            fee_negative_trades.append(t)
    
    winning_trades = [t for t in trades if t['pnl'] > 0]
    losing_trades = [t for t in trades if t['pnl'] < 0]
    zero_trades = [t for t in trades if t['pnl'] == 0]
    
    avg_win = sum(t['pnl'] for t in winning_trades) / len(winning_trades) if winning_trades else 0
    avg_loss = sum(t['pnl'] for t in losing_trades) / len(losing_trades) if losing_trades else 0
    
    print(f"    Total completed trades: {len(trades)}")
    print(f"    Winning trades:    {len(winning_trades)} ({len(winning_trades)/len(trades)*100:.1f}%)")
    print(f"    Losing trades:     {len(losing_trades)} ({len(losing_trades)/len(trades)*100:.1f}%)")
    print(f"    Zero PnL trades:   {len(zero_trades)}")
    print(f"    Average win:       ${avg_win:.4f}")
    print(f"    Average loss:      ${avg_loss:.4f}")
    if avg_loss != 0:
        print(f"    Win/Loss ratio:    {abs(avg_win/avg_loss):.2f}")
    print(f"    Win rate:          {len(winning_trades)/len(trades)*100:.1f}%")
    print()
    print(f"    Total realized PnL:       ${total_pnl:.4f}")
    print(f"    Total recorded fees:      ${total_recorded_fee:.4f}")
    print(f"    Total estimated fees:     ${total_estimated_fee:.4f}")
    print(f"    PnL after fees:           ${total_pnl - total_recorded_fee:.4f}")
    print(f"    Fee as % of gross volume: {total_estimated_fee / sum(t['open_notional'] + t['close_notional'] for t in trades) * 100:.3f}%" if sum(t['open_notional'] + t['close_notional'] for t in trades) > 0 else "")
    print()
    print(f"    Fee-negative trades (|PnL| < estimated fees): {fee_negative_count} / {len(trades)} ({fee_negative_count/len(trades)*100:.1f}%)")
    
    if fee_negative_trades:
        print("\n    Sample fee-negative trades:")
        for t in fee_negative_trades[:10]:
            print(f"      {t['symbol']:8s} PnL=${t['pnl']:+.4f}  est_fee=${t['estimated_fee']:.4f}  hold={fmt_time(t['holding_seconds']) if t['holding_seconds'] else 'N/A':>8s}  reason={t['close_reason']}")

# --- PnL by close reason ---
print("\n  --- PnL by Close Reason ---")
reason_stats = defaultdict(lambda: {'count': 0, 'total_pnl': 0, 'total_fee': 0})
for t in trades:
    r = t['close_reason']
    reason_stats[r]['count'] += 1
    reason_stats[r]['total_pnl'] += t['pnl']
    reason_stats[r]['total_fee'] += t['estimated_fee']

for reason, stats in sorted(reason_stats.items(), key=lambda x: x[1]['total_pnl']):
    avg_pnl = stats['total_pnl'] / stats['count']
    print(f"    {reason:30s}  n={stats['count']:3d}  total_pnl=${stats['total_pnl']:+8.4f}  avg=${avg_pnl:+.4f}  fees=${stats['total_fee']:.4f}")

# --- PnL by symbol ---
print("\n  --- PnL by Symbol ---")
symbol_stats = defaultdict(lambda: {'count': 0, 'total_pnl': 0, 'total_fee': 0, 'wins': 0})
for t in trades:
    s = t['symbol']
    symbol_stats[s]['count'] += 1
    symbol_stats[s]['total_pnl'] += t['pnl']
    symbol_stats[s]['total_fee'] += t['estimated_fee']
    if t['pnl'] > 0:
        symbol_stats[s]['wins'] += 1

for sym, stats in sorted(symbol_stats.items(), key=lambda x: x[1]['total_pnl']):
    wr = stats['wins'] / stats['count'] * 100 if stats['count'] > 0 else 0
    net = stats['total_pnl'] - stats['total_fee']
    print(f"    {sym:8s}  trades={stats['count']:3d}  pnl=${stats['total_pnl']:+8.4f}  fees=${stats['total_fee']:.4f}  net=${net:+8.4f}  winrate={wr:.0f}%")


# ============================================================
# SECTION 2: AI Decision Logs Analysis
# ============================================================
print("\n" + "=" * 80)
print("  SECTION 2: AI DECISION LOGS ANALYSIS")
print("=" * 80)

c.execute("""
    SELECT operation, COUNT(*) as cnt 
    FROM ai_decision_logs 
    WHERE decision_time >= ?
    GROUP BY operation 
    ORDER BY cnt DESC
""", (three_days_ago,))
ops = c.fetchall()
total_decisions = sum(r['cnt'] for r in ops)
print(f"\n  Total decisions (last 3 days): {total_decisions}")
print("\n  --- Operation Distribution ---")
for r in ops:
    pct = r['cnt'] / total_decisions * 100
    bar = "#" * int(pct / 2)
    print(f"    {r['operation']:10s}  {r['cnt']:5d}  ({pct:5.1f}%)  {bar}")

# Action vs passive
action_ops = sum(r['cnt'] for r in ops if r['operation'] in ('buy', 'sell'))
passive_ops = sum(r['cnt'] for r in ops if r['operation'] in ('hold', 'reduce', 'close'))
print(f"\n  Active (buy/sell):       {action_ops}  ({action_ops/total_decisions*100:.1f}%)")
print(f"  Passive (hold/reduce/close): {passive_ops}  ({passive_ops/total_decisions*100:.1f}%)")
if action_ops > 0:
    print(f"  Passive:Active ratio:    {passive_ops/action_ops:.1f}:1")

# --- Direction Flipping Analysis ---
print("\n  --- Direction Flip Analysis (same symbol, within 4 hours) ---")
c.execute("""
    SELECT symbol, operation, decision_time
    FROM ai_decision_logs 
    WHERE decision_time >= ? AND operation IN ('buy', 'sell')
    ORDER BY symbol, decision_time
""", (three_days_ago,))
action_logs = c.fetchall()

flip_count = 0
flip_details = defaultdict(int)
by_symbol = defaultdict(list)
for r in action_logs:
    by_symbol[r['symbol']].append(r)

for sym, entries in by_symbol.items():
    for i in range(1, len(entries)):
        prev = entries[i-1]
        curr = entries[i]
        if prev['operation'] != curr['operation']:
            t1 = datetime.strptime(prev['decision_time'].split('.')[0], "%Y-%m-%d %H:%M:%S")
            t2 = datetime.strptime(curr['decision_time'].split('.')[0], "%Y-%m-%d %H:%M:%S")
            gap_hours = (t2 - t1).total_seconds() / 3600
            if gap_hours <= 4:
                flip_count += 1
                flip_details[sym] += 1

total_action_decisions = len(action_logs)
print(f"    Total buy/sell decisions: {total_action_decisions}")
print(f"    Direction flips within 4hr: {flip_count}")
if total_action_decisions > 0:
    print(f"    Flip rate: {flip_count/total_action_decisions*100:.1f}% of action decisions")
print(f"\n    Flips by symbol:")
for sym, cnt in sorted(flip_details.items(), key=lambda x: -x[1]):
    print(f"      {sym:8s}  {cnt} flips")

# --- Decision frequency analysis ---
print("\n  --- Decision Frequency ---")
c.execute("""
    SELECT DATE(decision_time) as dt, COUNT(*) as cnt,
        SUM(CASE WHEN operation = 'buy' THEN 1 ELSE 0 END) as buys,
        SUM(CASE WHEN operation = 'sell' THEN 1 ELSE 0 END) as sells,
        SUM(CASE WHEN operation = 'hold' THEN 1 ELSE 0 END) as holds
    FROM ai_decision_logs 
    WHERE decision_time >= ?
    GROUP BY DATE(decision_time) 
    ORDER BY dt
""", (three_days_ago,))
daily = c.fetchall()
for r in daily:
    print(f"    {r['dt']}  total={r['cnt']:5d}  buy={r['buys']:4d}  sell={r['sells']:4d}  hold={r['holds']:4d}")


# ============================================================
# SECTION 3: AI Strategies Analysis
# ============================================================
print("\n" + "=" * 80)
print("  SECTION 3: AI STRATEGIES CONFIGURATION")
print("=" * 80)

c.execute("SELECT strategy_id, name, timeframe_tier, status, genome, timeframe, default_leverage, max_leverage, stop_loss_pct, take_profit_pct FROM ai_strategies")
strategies = c.fetchall()

print(f"\n  Total strategies: {len(strategies)}")

for s in strategies:
    print(f"\n  --- Strategy: {s['name']} ({s['strategy_id']}) ---")
    print(f"    Status:          {s['status']}")
    print(f"    Timeframe:       {s['timeframe']}")
    print(f"    Timeframe tier:  {s['timeframe_tier']}")
    print(f"    Default leverage:{s['default_leverage']}")
    print(f"    Max leverage:    {s['max_leverage']}")
    print(f"    Stop loss:       {s['stop_loss_pct']}")
    print(f"    Take profit:     {s['take_profit_pct']}")
    
    if s['genome']:
        try:
            genome = json.loads(s['genome'])
            key_params = ['trade_nature', 'min_trade_interval', 'stop_loss_pct', 'take_profit_pct', 
                         'max_position_size', 'default_leverage', 'signal_threshold', 'confidence_min',
                         'trailing_activation_pct', 'trailing_distance_pct',
                         'breakeven_activation_pct', 'breakeven_buffer_pct']
            print(f"    Genome key params:")
            for k in key_params:
                if k in genome:
                    print(f"      {k}: {genome[k]}")
            
            if 'trade_nature' not in genome:
                print(f"      trade_nature: NOT SET")
                
        except json.JSONDecodeError:
            print(f"    Genome: [parse error]")


# ============================================================
# SECTION 4: Fee Impact Summary
# ============================================================
print("\n" + "=" * 80)
print("  SECTION 4: FEE IMPACT & OVERALL ASSESSMENT")
print("=" * 80)

# Total fee from all orders in last 3 days
c.execute("SELECT SUM(fee) FROM paper_orders WHERE created_at >= ?", (three_days_ago,))
total_db_fee = c.fetchone()[0] or 0

c.execute("SELECT SUM(pnl) FROM paper_orders WHERE created_at >= ? AND pnl IS NOT NULL", (three_days_ago,))
total_db_pnl = c.fetchone()[0] or 0

c.execute("SELECT SUM(filled_price * quantity) FROM paper_orders WHERE created_at >= ?", (three_days_ago,))
total_volume = c.fetchone()[0] or 0
estimated_total_fee = total_volume * TAKER_FEE_RATE

print(f"\n  Total trading volume (3 days): ${total_volume:.2f}")
print(f"  Total recorded fees:           ${total_db_fee:.4f}")
print(f"  Estimated fees (0.06% taker):  ${estimated_total_fee:.4f}")
print(f"  Total realized PnL:            ${total_db_pnl:.4f}")
print(f"  PnL after recorded fees:       ${total_db_pnl - total_db_fee:.4f}")
print(f"  PnL after estimated fees:      ${total_db_pnl - estimated_total_fee:.4f}")

if total_db_pnl != 0:
    fee_pnl_ratio = total_db_fee / abs(total_db_pnl) * 100
    print(f"  Fees as % of |PnL|:            {fee_pnl_ratio:.1f}%")

if total_volume > 0:
    print(f"  Return on volume:              {total_db_pnl / total_volume * 100:.4f}%")

# ============================================================
# KEY FINDINGS / RED FLAGS
# ============================================================
print("\n" + "=" * 80)
print("  KEY FINDINGS & RED FLAGS")
print("=" * 80)

findings = []

# Check for excessive trading
if total_orders > 100:
    findings.append(f"[HIGH] Excessive trading: {total_orders} orders in 3 days = ~{total_orders/3:.0f}/day")

# Check for short holding times
if timed_trades:
    short_holds = sum(1 for t in timed_trades if t['holding_seconds'] < 300)
    short_pct = short_holds / len(timed_trades) * 100
    if short_pct > 30:
        findings.append(f"[HIGH] {short_pct:.0f}% of trades held < 5 minutes ({short_holds}/{len(timed_trades)})")

# Check fee-negative ratio
if trades:
    fn_pct = fee_negative_count / len(trades) * 100
    if fn_pct > 30:
        findings.append(f"[HIGH] {fn_pct:.0f}% of trades are fee-negative (PnL < fees)")

# Check direction flipping
if total_action_decisions > 0 and flip_count / total_action_decisions > 0.3:
    findings.append(f"[HIGH] Direction flip rate is {flip_count/total_action_decisions*100:.0f}% - system is indecisive")

# Check hold ratio
if total_decisions > 0:
    hold_pct = sum(r['cnt'] for r in ops if r['operation'] == 'hold') / total_decisions * 100
    if hold_pct > 80:
        findings.append(f"[MED] Hold decisions are {hold_pct:.0f}% - system rarely acts")
    elif hold_pct < 30:
        findings.append(f"[HIGH] Hold decisions are only {hold_pct:.0f}% - system is over-trading")

# Check if fees overwhelm PnL
if total_db_pnl < 0 and abs(total_db_pnl) < total_db_fee:
    findings.append(f"[CRITICAL] Total PnL (${total_db_pnl:.2f}) is negative and less than fees (${total_db_fee:.2f})")
elif total_db_pnl > 0 and total_db_pnl < total_db_fee:
    findings.append(f"[HIGH] Fees (${total_db_fee:.2f}) exceed gross PnL (${total_db_pnl:.2f}) - net negative after fees")

# Check win rate
if trades:
    wr = len(winning_trades) / len(trades) * 100
    if wr < 40:
        findings.append(f"[HIGH] Low win rate: {wr:.0f}%")

# Check for dominant loss reasons
for r in close_reasons:
    if r['close_reason'] in ('sl', 'emergency_drawdown', 'drawdown_protection') and r['cnt'] / close_count > 0.3:
        findings.append(f"[HIGH] '{r['close_reason']}' accounts for {r['cnt']/close_count*100:.0f}% of closes")

if not findings:
    print("\n  No critical red flags detected.")
else:
    for f in findings:
        print(f"\n  {f}")

print("\n" + "=" * 80)
print("  END OF DIAGNOSTIC REPORT")
print("=" * 80)

conn.close()
