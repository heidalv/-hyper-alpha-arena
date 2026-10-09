"""轮149 验证：K线深度产物是否已覆盖实盘宇宙（kline_deep 缺产物应大幅减少）。"""
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text  # noqa: E402

from backend.database.connection import analytics_engine  # noqa: E402

bl = Path("logs/backend.log").read_text(encoding="utf-8", errors="replace").splitlines()
kd = [ln for ln in bl if "K线深度本轮落库" in ln]
print("K线深度落库日志（最近 4 条）:")
for ln in kd[-4:]:
    print("  ", ln[:19], ln.split("] ", 1)[-1][:150])

with analytics_engine.connect() as c:
    n, syms = c.execute(text(
        "select count(*), count(distinct symbol) from kline_ai_analysis_logs")).fetchone()
    print(f"\n产物总行数 = {n}；覆盖标的数 = {syms}")
    rows = c.execute(text(
        "select symbol, created_at from kline_ai_analysis_logs "
        "order by id desc limit 12")).fetchall()
    print("最新产物：", [(r[0], str(r[1])[:19]) for r in rows])
    recent = c.execute(text(
        "select count(distinct symbol) from kline_ai_analysis_logs "
        "where created_at >= :t"), {"t": "2026-09-21 02:20"}).scalar()
    print(f"02:20 之后有产物的标的数 = {recent}")

bt = Path("logs/brain_subprocess.log").read_text(encoding="utf-8", errors="replace").splitlines()
rows = [ln for ln in bt if "[MidLongBrain] refresh" in ln and ln[:19] >= "2026-09-21 02:2"]
pat = re.compile(r"miss=\[(.*?)\]")
mi = Counter()
for ln in rows:
    m = pat.search(ln)
    if not m:
        continue
    for it in [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]:
        mi[it[:46]] += 1
print(f"\n02:20 之后 refresh = {len(rows)} 行；缺项 Top6：")
for k, v in mi.most_common(6):
    print(f"   {v:>3}  {k}")
