"""轮137 验证：分析师规模通道是否在活链路生效 + v5gate 硬拦是否保持为 0。"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SINCE = "2026-09-20 13:05"
bl = Path("logs/backend.log")
print(f"等待活链路（最多 300s，观察 {SINCE}+）…")
for _ in range(30):
    time.sleep(10)
    tail = bl.read_text(encoding="utf-8", errors="replace")[-600000:]
    if any("[AnalystSize]" in ln and ln[:19] >= SINCE for ln in tail.splitlines()):
        break

tail = bl.read_text(encoding="utf-8", errors="replace")[-600000:]
for pat, label in (("[AnalystSize]", "规模通道生效"),
                   ("[V5Gate] BLOCK", "v5gate 拦截"),
                   ("[MidLong] stage=exec", "执行阶段"),
                   ("[MidLongAudit] skip stage=exec", "执行漏斗 skip"),
                   ("[MidLongBrain] 开仓扫描", "扫单")):
    hits = [ln for ln in tail.splitlines() if pat in ln and ln[:19] >= SINCE]
    print(f"\n{label}：{len(hits)} 行")
    for ln in hits[-3:]:
        print("   ", ln[:19], ln.split("] ", 1)[-1][:140])

from sqlalchemy import text  # noqa: E402

from backend.database.connection import engine  # noqa: E402

with engine.connect() as c:
    n = c.execute(text("select count(*) from paper_positions where opened_at >= :t"),
                  {"t": "2026-09-20 13:00"}).scalar()
    print(f"\n13:00 后开仓 = {n}")
    rows = c.execute(text(
        "select id, symbol, side, margin, opened_at from paper_positions "
        "where opened_at >= :t order by id desc limit 5"), {"t": "2026-09-20 13:00"}).fetchall()
    for r in rows:
        print("   ", r)
