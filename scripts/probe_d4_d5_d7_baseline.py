# -*- coding: utf-8 -*-
"""D-4/D-5/D-7 的事前核查（只读）：
  ① 备份表 `crypto_klines_bak_20260807114347` 的**真实行数**（n_live_tup 不可信）；
  ② 日志噪音规模（非加密 ticker 告警的频次与来源）；
  ③ 死表 `kline_collection_tasks` 是否真无人读写。
"""
from __future__ import annotations

import io
import re
import sys
from collections import Counter
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

print("=" * 94)
print("① 备份表的真实行数（决定能不能删）")
print("=" * 94)
try:
    from sqlalchemy import text
    from backend.database.connection import MarketSessionLocal as S

    def q(sql):
        db = S()
        try:
            db.execute(text("SET app.is_admin='on'"))
            return db.execute(text(sql)).fetchall()
        finally:
            db.close()

    for r in q("SELECT relname, n_live_tup FROM pg_stat_user_tables "
               "WHERE relname LIKE '%_bak_%' OR relname LIKE '%bak%'"):
        print(f"  [统计值 n_live_tup] {r[0]} = {r[1]}")
    print()
    for r in q("SELECT count(*) FROM crypto_klines_bak_20260807114347"):
        print(f"  [真实 COUNT(*)] crypto_klines_bak_20260807114347 = {r[0]:,} 行")
    for r in q("SELECT pg_size_pretty(pg_total_relation_size('crypto_klines_bak_20260807114347'))"):
        print(f"  [大小] {r[0]}")
    # 它是否被任何 view/FK 依赖
    for r in q("""
        SELECT DISTINCT dependent.relname AS depends_on_it
        FROM pg_depend d
        JOIN pg_rewrite rw ON rw.oid = d.objid
        JOIN pg_class dependent ON dependent.oid = rw.ev_class
        JOIN pg_class src ON src.oid = d.refobjid
        WHERE src.relname = 'crypto_klines_bak_20260807114347' AND dependent.relname <> src.relname
    """):
        print(f"  ⚠️ 被依赖: {r[0]}")
    print("  （若无『被依赖』行 ⇒ 无 view/规则依赖它）")
except Exception as exc:  # noqa: BLE001
    print(f"  （DB 失败: {type(exc).__name__}: {str(exc)[:120]}）")

print()
print("=" * 94)
print("② 日志噪音规模：'拒绝写入非加密 ticker'")
print("=" * 94)
p = ROOT / "logs" / "data-center.log"
lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
hits = [ln for ln in lines if "拒绝写入非加密 ticker" in ln]
print(f"  全文件命中 = {len(hits):,} 条 / 总行 {len(lines):,} = {len(hits)/max(1,len(lines)):.2%}")
sym = Counter()
ex = Counter()
for ln in hits:
    m = re.search(r"ticker: (\S+) \((\w+)\)", ln)
    if m:
        sym[m.group(1)] += 1
        ex[m.group(2)] += 1
print(f"  按交易所: {dict(ex.most_common())}")
print(f"  按标的 top10: {dict(sym.most_common(10))}")
print(f"  去重后标的数 = {len(sym)}")
# 同类：已下架告警（这是**安全**信息，必须保留）
dl = [ln for ln in lines if "拒绝写入已下架交易对" in ln]
print(f"  『拒绝写入已下架交易对』= {len(dl):,} 条（安全信息，保留）")

print()
print("=" * 94)
print("③ 死表 kline_collection_tasks 是否真无人读写")
print("=" * 94)
src_hits = []
for f in (ROOT / "backend").rglob("*.py"):
    if any(x in f.parts for x in (".venv", "site-packages", "__pycache__")):
        continue
    try:
        t = f.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        continue
    if "kline_collection_tasks" in t or "KlineCollectionTask" in t:
        src_hits.append(str(f.relative_to(ROOT)))
print("  引用它的文件：")
for h in src_hits:
    print("   ", h)
try:
    for r in q("SELECT count(*) FROM kline_collection_tasks"):
        print(f"  表内行数 = {r[0]}")
except Exception as exc:  # noqa: BLE001
    print(f"  （表查询失败: {type(exc).__name__}）")
