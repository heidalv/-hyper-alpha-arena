"""轮132 验证（二）：ETH 是否成交 + UNI 的 eval_false:v5ga 是什么闸。"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text  # noqa: E402

from backend.database.connection import engine  # noqa: E402

with engine.connect() as c:
    print("=== 12:20 之后的新开仓 ===")
    rows = c.execute(text(
        "select id, symbol, side, margin, opened_at, status from paper_positions "
        "where opened_at >= '2026-09-20 12:20' order by id desc limit 6")).fetchall()
    print("   （无）" if not rows else "")
    for r in rows:
        print("   ", r)
    print("=== 12:20 之后的订单 ===")
    rows = c.execute(text(
        "select id, symbol, side, status, quantity, created_at from paper_orders "
        "where created_at >= '2026-09-20 12:20' order by id desc limit 6")).fetchall()
    print("   （无）" if not rows else "")
    for r in rows:
        print("   ", r)

print("\n=== eval_false / v5 闸 的代码位置 ===")
pat = re.compile(r"eval_false|v5ga|v5_gate|V5_GATE|v5_governed")
hits = 0
for p in sorted((Path(__file__).resolve().parents[1] / "backend/services").rglob("*.py")):
    try:
        t = p.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        continue
    for i, ln in enumerate(t.splitlines(), 1):
        if pat.search(ln) and not ln.strip().startswith("#"):
            print(f"   {p.as_posix().replace('backend/services/', '')}:{i}: {ln.strip()[:120]}")
            hits += 1
            if hits > 12:
                break
    if hits > 12:
        break
if not hits:
    print("   （未找到 eval_false 的产生点，可能在 ai_governed_compare / v5 治理模块里）")

print("\n=== 审计里 eval_false 的完整原因（近 1h）===")
bl = Path("logs/backend.log").read_text(encoding="utf-8", errors="replace")
for ln in [x for x in bl.splitlines() if "eval_false" in x][-6:]:
    print("   ", ln[:19], ln.split("[MidLongAudit]", 1)[-1][:160])
