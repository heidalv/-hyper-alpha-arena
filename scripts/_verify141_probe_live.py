"""轮141 验证：六域探针方向是否在活链路生效（小仓试探 + 审计留痕 + 未进候选分布变化）。"""
import sys
import time
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SINCE = "2026-09-20 15:0"
bl = Path("logs/backend.log")
print(f"等待活链路（最多 300s，观察 {SINCE}+）…")
probe6, skip6 = [], []
for _ in range(30):
    time.sleep(10)
    tail = bl.read_text(encoding="utf-8", errors="replace")[-600000:]
    probe6 = [ln for ln in tail.splitlines() if "六域信号给方向" in ln and ln[:19] >= SINCE]
    skip6 = [ln for ln in tail.splitlines() if "[MidLongAudit]" in ln and ln[:19] >= SINCE]
    if probe6 or len(skip6) >= 5:
        break

tail = bl.read_text(encoding="utf-8", errors="replace")[-600000:]
print(f"\n=== 1) 六域信号给方向（{SINCE}+）===")
for ln in probe6[-6:]:
    print("   ", ln[:19], ln.split("] ", 1)[-1][:150])
print(f"   共 {len(probe6)} 行")

print(f"\n=== 2) 小仓试探（含 analyst_signal 原因）===")
tri = [ln for ln in tail.splitlines() if "小仓试探" in ln and ln[:19] >= SINCE]
for ln in tri[-6:]:
    print("   ", ln[:19], ln.split("] ", 1)[-1][:150])
print(f"   共 {len(tri)} 行")

print(f"\n=== 3) 未进候选分布（扫单行）===")
scan = [ln for ln in tail.splitlines() if "开仓扫描" in ln and ln[:19] >= SINCE]
for ln in scan[-3:]:
    print("   ", ln[:19], ln.split("] ", 1)[-1][:160])

print(f"\n=== 4) 审计原因分布（{SINCE}+）===")
c = Counter(ln.split("reason=")[-1][:46] for ln in skip6)
for k, v in c.most_common(8):
    print(f"   {v:>4}  {k}")

from sqlalchemy import text  # noqa: E402

from backend.database.connection import engine  # noqa: E402

with engine.connect() as c2:
    n = c2.execute(text("select count(*) from paper_positions where opened_at >= :t"),
                   {"t": f"{SINCE}:00"}).scalar()
    print(f"\n=== 5) {SINCE} 后新开仓 = {n} ===")
    for r in c2.execute(text(
            "select id, symbol, side, margin, opened_at from paper_positions "
            "where opened_at >= :t order by id desc limit 5"), {"t": f"{SINCE}:00"}):
        print("   ", r)
