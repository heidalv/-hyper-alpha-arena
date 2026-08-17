# -*- coding: utf-8 -*-
"""离线验证三个新 Agent（不改运行中进程，只读/落盘约束文件）。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from backend.database.connection import SessionLocal  # noqa: E402

ok = True

# ── 1. CausalFeedback：trade_facts 聚合 + 落盘 + 文本 ──
from backend.services.full_auto import causal_feedback as cf  # noqa: E402
db = SessionLocal()
try:
    res = cf.rebuild(db, hours=24)
    print(f"[1 CausalFeedback] rebuild → {res}")
    path = os.path.join(os.getcwd(), cf.CONSTRAINTS_PATH)
    exists = os.path.exists(path)
    txt = cf.constraints_text()
    print(f"[1 CausalFeedback] file exists={exists}, constraints_text={len(txt)} chars")
    if exists:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[1 CausalFeedback] persisted {len(data.get('constraints') or [])} constraints")
    if not exists:
        ok = False
finally:
    db.close()

# ── 2. ExitAgent：读真实持仓跑巡检（advisory）──
from backend.services.full_auto.exit_agent import run_exit_pass  # noqa: E402
from backend.services.paper_trading_engine import paper_engine  # noqa: E402
db = SessionLocal()
try:
    positions = paper_engine.get_positions(db, 1, status="open") or []
    # 找真实 account_id：直接查 trade_facts 最近的账户，回退 1
    from sqlalchemy import text as _t
    row = db.execute(_t("SELECT account_id FROM trade_facts ORDER BY ts DESC LIMIT 1")).first()
    acct = int(row[0]) if row else 1
    positions = paper_engine.get_positions(db, acct, status="open") or []
    print(f"[2 ExitAgent] account={acct}, open positions={len(positions)}")
    summary = {"BTC": {"current_price": 60000}, "ETH": {"current_price": 3000}}
    res2 = run_exit_pass(db, positions, summary)
    print(f"[2 ExitAgent] run_exit_pass → {res2}")
except Exception as e:
    ok = False
    print(f"[2 ExitAgent] FAIL: {type(e).__name__}: {e}")
finally:
    db.close()

# ── 3. ArbGate：反向视图 fail-closed（置信度为 0-100 百分制，阈值 55）──
from backend.services.full_auto import decision_arbitration as arb  # noqa: E402
arb.register_view("BTC", "short", "master", "sell", 60)
arb.register_view("BTC", "short", "scalp", "buy", 70)
_allowed, _reason = arb.check_entry("BTC", "short", "scalp", "buy", 70)
print(f"[3 ArbGate] opposite views → allowed={_allowed} reason={_reason}")
if _allowed:
    ok = False
arb.register_view("BTC", "short", "scalp", "buy", 70)
_allowed2, _reason2 = arb.check_entry("BTC", "short", "scalp", "buy", 70)
print(f"[3 ArbGate] aligned views → allowed={_allowed2} reason={_reason2}")
# 低置信度冲突（<55）不拦截
arb.register_view("BTC", "short", "master", "buy", 40)
_allowed3, _reason3 = arb.check_entry("BTC", "short", "scalp", "sell", 70)
print(f"[3 ArbGate] low-conf conflict → allowed={_allowed3} reason={_reason3}")
# 非开仓动作（hold/close）直通
_allowed4, _reason4 = arb.check_entry("BTC", "short", "scalp", "hold", 70)
print(f"[3 ArbGate] hold passthrough → allowed={_allowed4} reason={_reason4}")

print("ALL OK" if ok else "SOME FAILURES")
sys.exit(0 if ok else 1)
