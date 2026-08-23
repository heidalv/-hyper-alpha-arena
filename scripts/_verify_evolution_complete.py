# -*- coding: utf-8 -*-
"""进化完成后的整链路核验（M0-E1 收尾用）。

核验项：
1. backtest_runs 今日冠军行（is_champion）
2. evolution_events weekly 成功记录
3. strategy_templates 晋升字段
4. runtime_tuning.json 的 min_risk_reward（gates 下发结果）
5. runtime_tuning_intents.json 的 evolution_gc 意图
6. 交易活跃度（strategy_trades 最近平仓 / paper_positions 持仓）
"""
import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text

_env = {}
for _line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        _env[_k] = _v

eng = create_engine(_env["DATABASE_URL"], pool_pre_ping=True)

ok = True
def check(name, cond, detail=""):
    global ok
    mark = "PASS" if cond else "FAIL"
    if not cond:
        ok = False
    print(f"[{mark}] {name} {detail}")

with eng.connect() as c:
    c.execute(text("SET app.is_admin='on'"))
    c.execute(text("SET app.tenant_id=326"))

    # 1. 冠军行（今日）
    today = datetime.now(timezone.utc) - timedelta(hours=36)
    champs = c.execute(text(
        "SELECT run_id, template_id, generation, sharpe_ratio, win_rate, completed_at "
        "FROM backtest_runs WHERE is_champion AND completed_at >= :since ORDER BY completed_at"
    ), {"since": today}).fetchall()
    print(f"\n== 冠军行（近36h）: {len(champs)} 个 ==")
    for r in champs:
        print(f"   {r[0]} {r[1]} gen={r[2]} sharpe={r[3]:.2f} wr={r[4]:.2%} @ {r[5]}")
    check("backtest_runs 冠军落库", len(champs) >= 1, f"n={len(champs)}")

    # 2. evolution_events
    evs = c.execute(text(
        "SELECT evolution_type, success, template_count, promoted_count, best_fitness, created_at "
        "FROM evolution_events WHERE created_at >= :since ORDER BY created_at"
    ), {"since": today}).fetchall()
    print(f"\n== 进化事件（近36h）: {len(evs)} 条 ==")
    for e in evs:
        print(f"   {e[0]} success={e[1]} templates={e[2]} promoted={e[3]} fitness={e[4]} @ {e[5]}")
    weekly = [e for e in evs if e[0] == "weekly" and e[1]]
    check("evolution_events weekly 成功", len(weekly) >= 1, f"n={len(weekly)}")

    # 3. 模板晋升
    tpls = c.execute(text(
        "SELECT template_id, rating, backtest_sharpe, backtest_win_rate, backtest_total_trades "
        "FROM strategy_templates WHERE backtest_sharpe IS NOT NULL ORDER BY backtest_sharpe DESC LIMIT 5"
    )).fetchall()
    print("\n== 已晋升模板 Top5 ==")
    for t in tpls:
        print(f"   {t[0]} rating={t[1]} sharpe={t[2]:.2f} wr={t[3]:.2%} trades={t[4]}")

    # 4. 交易活跃度
    t = c.execute(text(
        "SELECT count(*), max(closed_at) FROM strategy_trades WHERE closed_at >= :since"
    ), {"since": today}).fetchone()
    print(f"\n== 近36h 平仓笔数: {t[0]}，最新: {t[1]} ==")
    pos = c.execute(text("SELECT count(*) FROM paper_positions WHERE account_id=14")).scalar()
    print(f"account14 paper_positions: {pos}")

# 5. runtime_tuning.json
rt_path = ROOT / "data" / "runtime_tuning.json"
if rt_path.exists():
    rt = json.loads(rt_path.read_text(encoding="utf-8"))
    mrr = rt.get("min_risk_reward")
    print(f"\n== runtime_tuning.min_risk_reward = {mrr} ==")
    print(f"   max_daily_trades={rt.get('max_daily_trades')} scalp_min_confidence={rt.get('scalp_min_confidence')}")
else:
    print("\nruntime_tuning.json 不存在")

# 6. evolution_gc 意图
ip = ROOT / "data" / "runtime_tuning_intents.json"
if ip.exists():
    intents = json.loads(ip.read_text(encoding="utf-8"))
    arr = intents if isinstance(intents, list) else intents.get("intents", [])
    gc = [i for i in arr if isinstance(i, dict) and i.get("source") == "evolution_gc"]
    print(f"\n== evolution_gc 意图: {len(gc)} 条 ==")
    for i in gc[-3:]:
        print("   ", json.dumps(i, ensure_ascii=False)[:200])

print("\nOVERALL:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
