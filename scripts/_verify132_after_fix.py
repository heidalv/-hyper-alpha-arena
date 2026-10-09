"""轮132 验证：致命 bug 修好后，中线是否恢复开仓；辩论是否不再被省钱限制压住。"""
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

print("等待活链路跑几轮（最多 300s）…")
bl = Path("logs/backend.log")
errs, execs, fuse = [], [], []
for _ in range(30):
    time.sleep(10)
    txt = bl.read_text(encoding="utf-8", errors="replace")
    tail = txt[-400000:]
    errs = [ln for ln in tail.splitlines() if "is not defined" in ln and ln[:19] >= "2026-09-20 12:2"]
    execs = [ln for ln in tail.splitlines() if "stage=exec" in ln and ln[:19] >= "2026-09-20 12:2"]
    fuse = [ln for ln in tail.splitlines() if "熔断窄带" in ln and ln[:19] >= "2026-09-20 12:2"]
    if execs or len(fuse) >= 3:
        break

print(f"\n=== 修复后（12:20 起）===")
print(f"  'name ... is not defined' 行数 = {len(errs)}   ← 应为 0")
for ln in errs[-3:]:
    print("     ", ln[:19], ln.split(" - ", 1)[-1][:120])
print(f"  stage=exec 行数 = {len(execs)}   ← 修复前窗口内为 0")
for ln in execs[-6:]:
    print("     ", ln[:19], ln.split("[MidLong]", 1)[-1][:150])
print(f"  熔断窄带（缩仓后继续）行数 = {len(fuse)}")

from sqlalchemy import text  # noqa: E402

from backend.database.connection import analytics_engine, engine  # noqa: E402

print("\n=== 持仓/订单（修复后新开）===")
with engine.connect() as c:
    rows = c.execute(text(
        "select id, symbol, side, timeframe_tier, margin, opened_at, status from paper_positions "
        "where opened_at >= :t order by id desc limit 8"), {"t": "2026-09-20 12:20"}).fetchall()
if not rows:
    print("   （12:20 之后仍无新开仓 —— 若 stage=exec 有行则说明走到了执行体，需继续看下一环）")
for r in rows:
    print("   ", r)

print("\n=== 风控官是否开始被调用（修复后走到了它就该有记录）===")
with analytics_engine.connect() as c:
    for r in c.execute(text(
            "select ts, symbol, side, allow, reason, inputs_json from risk_officer_decisions "
            "where ts >= now() - interval '30 minutes' order by id desc limit 6")):
        print(f"   {str(r[0])[:19]} {r[1]:<8} {r[2]:<5} allow={r[3]} {str(r[4])[:50]} | {str(r[5])[:110]}")

print("\n=== 辩论是否放开（无上限/多轮/风险角色走 LLM）===")
bt = Path("logs/brain_subprocess.log").read_text(encoding="utf-8", errors="replace")
dl = [ln for ln in bt.splitlines() if "辩论" in ln and ln[:19] >= "2026-09-20 12:2"]
print(f"   12:20 之后辩论行数 = {len(dl)}")
for ln in dl[-4:]:
    print("   ", ln[:19], ln.split("辩论", 1)[-1][:150])
