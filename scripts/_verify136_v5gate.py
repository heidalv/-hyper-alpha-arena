"""轮136 验证：v5gate 的 confidence 硬拦是否消失；中线是否恢复成交。"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SINCE = "2026-09-20 13:00"
print(f"等待活链路跑几轮（最多 300s，观察窗口 {SINCE}+）…")
bl = Path("logs/backend.log")
blocks, records, execs = [], [], []
for _ in range(30):
    time.sleep(10)
    tail = bl.read_text(encoding="utf-8", errors="replace")[-500000:]
    blocks = [ln for ln in tail.splitlines()
              if "[V5Gate] BLOCK" in ln and "rule=confidence" in ln and ln[:19] >= SINCE]
    records = [ln for ln in tail.splitlines()
               if "辩论折减记录" in ln and ln[:19] >= SINCE]
    execs = [ln for ln in tail.splitlines()
             if ("stage=exec" in ln or "开仓扫描" in ln) and ln[:19] >= SINCE]
    if execs and (records or blocks):
        break

print(f"\n=== 1) confidence 硬拦（{SINCE}+）===")
print(f"   [V5Gate] BLOCK rule=confidence = {len(blocks)} 行   ← 修复前 20 行（confidence 25~28%<30%）")
for ln in blocks[-4:]:
    print("   ", ln[:19], ln.split("[V5Gate] BLOCK", 1)[-1][:120])

print(f"\n=== 2) 辩论折减现在只记录（不写回 confidence）===")
print(f"   命中 = {len(records)} 行")
for ln in records[-4:]:
    print("   ", ln[:19], ln.split("] ", 1)[-1][:140])

print("\n=== 3) 扫单/执行 ===")
for ln in execs[-8:]:
    print("   ", ln[:19], ln.split("] ", 1)[-1][:140])

from sqlalchemy import text  # noqa: E402

from backend.database.connection import engine  # noqa: E402

print("\n=== 4) 新开仓（13:00 之后）===")
with engine.connect() as c:
    rows = c.execute(text(
        "select id, symbol, side, margin, opened_at, status from paper_positions "
        "where opened_at >= '2026-09-20 13:00' order by id desc limit 6")).fetchall()
if not rows:
    print("   （仍无新开仓）")
for r in rows:
    print("   ", r)
print("\n=== 5) 各拦截码（13:00 后日志）===")
from collections import Counter  # noqa: E402
tail = bl.read_text(encoding="utf-8", errors="replace")[-500000:]
c2 = Counter()
for ln in tail.splitlines():
    if "[MidLongAudit]" in ln and ln[:19] >= SINCE:
        c2[ln.split("reason=")[-1][:44]] += 1
print("   ", c2.most_common(8) or "（无）")
