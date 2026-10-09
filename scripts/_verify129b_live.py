"""轮129 运行时验证：活主脑是否真的拿到了 analysts 层 + 信号表是否在被定时刷新。"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

t = Path("logs/brain_subprocess.log").read_text(encoding="utf-8", errors="replace")
hits = [ln for ln in t.splitlines() if "[context_pack]" in ln]
print(f"context_pack 构建行总数 = {len(hits)}")
print("--- 最后 5 条（只看 layers=… 部分）---")
for ln in hits[-5:]:
    m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", ln)
    tail = ln.split("构建完成", 1)[-1]
    print(" ", m.group(1) if m else "?", tail[:170])

witha = [ln for ln in hits if "analysts" in ln]
print(f"\n含 analysts 层的构建行 = {len(witha)}")
if witha:
    print("  示例:", witha[-1].split("构建完成", 1)[-1][:220])
else:
    print("  ✗ 尚无 → 主脑还没跑到下一轮 context_pack，或层未生效")

from sqlalchemy import text  # noqa: E402

from backend.database.connection import analytics_engine  # noqa: E402

with analytics_engine.connect() as c:
    r = c.execute(text("select max(ts), count(*) from analyst_signals")).fetchone()
    print(f"\n信号表：最新 ts = {r[0]}  总行数 = {r[1]}")
    print("近 20 分钟按域/质量：")
    for row in c.execute(text(
            "select domain, data_quality, count(*) from analyst_signals "
            "where ts > now() - interval '20 minutes' group by 1,2 order by 1")):
        print(f"   {row[0]:<12} {row[1]:<8} {row[2]}")
