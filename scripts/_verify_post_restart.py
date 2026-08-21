# -*- coding: utf-8 -*-
"""重启后全面验证（2026-08-22）：错误修复核验 + 正向性核验 + 盈利转向观察清单。

用法：python scripts/_verify_post_restart.py [minutes=5]
"""
import sys
import time
import datetime
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.core.tenant import set_system_identity
from backend.database.connection import ScopedSession

MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0


def check(label, cond, detail=""):
    print(f"  [{'OK' if cond else 'FAIL'}] {label} {detail}")
    return cond


print("=" * 70)
print("A. 代码修复核验（静态/已有数据）")
print("=" * 70)
set_system_identity()
s = ScopedSession()
try:
    # A1 tenant 正确归属（旧数据已迁移，新数据应 > 0 且无 tenant=1）
    n1 = s.execute(text("SELECT count(*) FROM paper_positions WHERE tenant_id=1")).scalar()
    ndist = s.execute(text("SELECT tenant_id, count(*) FROM paper_positions GROUP BY tenant_id ORDER BY 1")).fetchall()
    check("A1 tenant 迁移无残留(tenant=1=0)", n1 == 0, f"分布={ndist}")

    # A2 时区修复（无 closed<opened）
    n2 = s.execute(text("SELECT count(*) FROM strategy_trades WHERE closed_at IS NOT NULL AND closed_at < opened_at")).scalar()
    check("A2 strategy_trades 无 closed<opened", n2 == 0, f"count={n2}")

    # A3 会话4已停止 / 147/149 归档
    st = s.execute(text("SELECT id, status, total_pnl FROM full_auto_sessions ORDER BY id")).fetchall()
    check("A3 会话4 stopped", any(r[1] == 'stopped' and r[0] == 4 for r in st), f"sessions={st}")
    names = [r[0] for r in s.execute(text("SELECT name FROM accounts WHERE id IN (147,149)")).fetchall()]
    check("A3 147/149 已归档", all("已归档" in n for n in names), f"names={names}")

    # A4 对账
    pb = s.execute(text("""
        SELECT a.id, pb.initial_balance, pb.total_equity, pb.realized_pnl, pb.total_fee_paid, pb.unrealized_pnl
        FROM paper_balances pb JOIN accounts a ON a.id=pb.account_id
    """)).fetchall()
    ok_all = True
    for r in pb:
        ev = float(r.realized_pnl or 0) - float(r.total_fee_paid or 0) + float(r.unrealized_pnl or 0)
        if abs(float(r.total_equity) - (float(r.initial_balance) + ev)) > 0.05:
            ok_all = False
    check("A4 账户三不变量对账", ok_all)

    # A5 校准关闸（生产文件）
    import json, os
    calib_p = os.path.join(r"D:\001Alpha\Hyper-Alpha-Arena", "data", "scalp_calibration.json")
    try:
        with open(calib_p, encoding="utf-8") as f:
            c = json.load(f)
        check("A5 校准文件仍是'无盈利分桶'状态", c.get("threshold") is None and c.get("high_score_ok") is False,
              f"threshold={c.get('threshold')} high_score_ok={c.get('high_score_ok')}")
    except Exception as e:
        print(f"  [WARN] A5 读校准文件失败 {e}")

    # A6 Master AI 崩溃点已修
    src = open(os.path.join(r"D:\001Alpha\Hyper-Alpha-Arena", "backend", "services", "trading_analysts.py"), encoding="utf-8").read()
    check("A6 AI 崩溃点 (or 0) 兜底", "liquidation_1h_long') or 0" in src)
finally:
    s.close()

# ================================================================
# B. 运行期正向性观察（把 .env 快照下来做前后对照）
# ================================================================
print("\n" + "=" * 70)
print(f"B. 运行期观察（等待 {MINUTES} 分钟：检查日志信号/开仓/幽灵事件）")
print("=" * 70)
log_path = r"D:\001Alpha\Hyper-Alpha-Arena\logs\backend.log"
pos_mark = os.path.getsize(log_path)
time.sleep(MINUTES * 60)

with open(log_path, encoding="utf-8", errors="ignore") as f:
    f.seek(pos_mark)
    tail = f.read()

def count_in(t, pat):
    return t.count(pat)

print(f"\n--- 观察窗口 {MINUTES:.1f} 分钟日志统计 ---")
print(f"  scalp 开仓尝试(place_order 日志): {count_in(tail, 'place_order')}")
print(f"  校准拦截警告([ScalpCalib] 校准无盈利分桶): {count_in(tail, '校准无盈利分桶')}")
print(f"  V5/门控拦截(BLOCK): {count_in(tail, 'BLOCK')}")
print(f"  reconcile_sync 假事件: {count_in(tail, 'reconcile_sync')}")
print(f"  AI 失败计数(连续.*次失败): {count_in(tail, '连续')} (希望为 0 新增)")
print(f"  trend_broken/trend_weaken 平仓: {count_in(tail, 'trend_broken') + count_in(tail, 'trend_weaken')}")
print(f"  master_running_close/reduce: {count_in(tail, 'master_running')}")
print(f"  TypeError/NoneType 崩溃: {count_in(tail, 'TypeError') + count_in(tail, 'unsupported format')}")

# C. 新数据核验（观察窗口内新行）
set_system_identity()
s = ScopedSession()
try:
    rows = s.execute(text("""
        SELECT account_id, tenant_id, count(*) FROM paper_positions
        WHERE opened_at >= now() - interval '5 minutes' GROUP BY 1,2
    """)).fetchall()
    print(f"\n--- 窗口内新仓（应为 0 或 正确 tenant=326/327） ---")
    for r in rows:
        ok = r[1] not in (0, 1)
        check(f"新仓 account={r[0]} tenant={r[1]}", ok, f"n={r[2]}")
finally:
    s.close()

print("\n验证完成。行级失败项需人工复核（见上方 [FAIL]/[WARN]）。")
