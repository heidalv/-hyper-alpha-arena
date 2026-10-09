import re, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
from sqlalchemy import text
from backend.database.connection import analytics_engine

FROZEN = set()
with analytics_engine.connect() as c:
    for r in c.execute(text("""SELECT symbol FROM mlto_thesis WHERE session_id='fa_7e12e7a1b6'
                               AND coalesce(analysis_run_id,'')='' """)):
        FROZEN.add(str(r[0]).upper())
print("活会话冻结标的:", sorted(FROZEN))

p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\logs\backend.log")
skip = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)[^\n]*?skip open (\S+) (\w+) reason=(\S+)")
CUT = "2026-09-24 13:05:00"     # 过滤器上线（§56 重启）
pre = Counter(); post = Counter(); pre_fz = Counter(); post_fz = Counter()
with p.open(encoding="utf-8", errors="ignore") as fh:
    for line in fh:
        m = skip.match(line)
        if not m: continue
        ts, sym, tier, reason = m.group(1), m.group(2).upper(), m.group(3), m.group(4)
        tgt, fz = (post, post_fz) if ts >= CUT else (pre, pre_fz)
        if reason == "not_tradeable_fresh":
            tgt[sym] += 1
            if sym in FROZEN: fz[sym] += 1
print(f"\n=== not_tradeable_fresh：按标的 ===")
print(f"  过滤器上线前: 合计={sum(pre.values())}  其中冻结标的={sum(pre_fz.values())}  {dict(pre_fz)}")
print(f"  过滤器上线后: 合计={sum(post.values())}  其中冻结标的={sum(post_fz.values())}  {dict(post_fz)}")
print(f"  上线后 Top6 标的: {post.most_common(6)}")
