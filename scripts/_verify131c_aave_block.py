"""轮131 副作用定位：AAVE 11:47 主脑推荐开仓却没成交 —— 是谁拦的？"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text  # noqa: E402

from backend.database.connection import analytics_engine, engine  # noqa: E402

print("=== 1) 风控官是否判定过 AAVE / 近 3h 全部判定 ===")
with analytics_engine.connect() as c:
    rows = c.execute(text(
        "select ts, symbol, side, allow, reason, inputs_json from risk_officer_decisions "
        "order by id desc limit 10")).fetchall()
for r in rows:
    inp = json.loads(r[5] or "{}")
    print(f"   {str(r[0])[:19]} {r[1]:<9} {r[2]:<5} allow={r[3]} reason={str(r[4])[:70]} "
          f"名义=${inp.get('planned_notional_usd')} 权益=${inp.get('equity_usd')}")

print("\n=== 2) 近 3h 开仓被拦的审计记录（midlong_direction_audit.jsonl 尾部）===")
p = Path("data/midlong_direction_audit.jsonl")
if p.exists():
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
    hits = []
    for ln in lines:
        try:
            d = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        ts = str(d.get("ts") or d.get("time") or "")
        if ts >= "2026-09-20 11:25":
            hits.append(d)
    print(f"   11:25 之后 {len(hits)} 条")
    for d in hits[-14:]:
        print(f"   {str(d.get('ts'))[:19]} {str(d.get('symbol')):<8} {str(d.get('tier')):<5} "
              f"stage={str(d.get('stage'))[:26]:<26} reason={str(d.get('reason'))[:60]}")
else:
    print("   （文件不存在）")

print("\n=== 3) 主脑/执行日志里 11:40 之后与 AAVE 有关的行 ===")
for f in ("logs/backend.log", "logs/brain_subprocess.log", "logs/backend-console.log"):
    fp = Path(f)
    if not fp.exists():
        continue
    txt = fp.read_text(encoding="utf-8", errors="replace").splitlines()
    hits = [ln for ln in txt[-200000:] if "AAVE" in ln and ln[:19] >= "2026-09-20 11:40"]
    print(f"   [{fp.name}] {len(hits)} 行")
    for ln in hits[-8:]:
        print("     ", ln[ln.find(" ", 20) + 1:][:150])

print("\n=== 4) 是否有 open 事件 / 拒单事件（近 3h）===")
try:
    with engine.connect() as c:
        cols = [r[0] for r in c.execute(text(
            "select column_name from information_schema.columns where table_name='full_auto_sessions'"))]
        print("   full_auto_sessions 列含:", [x for x in cols if "event" in x or "log" in x][:6])
except Exception as exc:  # noqa: BLE001
    print(f"   [warn] {type(exc).__name__}")
