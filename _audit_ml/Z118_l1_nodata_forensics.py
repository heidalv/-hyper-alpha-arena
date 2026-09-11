# -*- coding: utf-8 -*-
"""Z118: §57.5-C —— 「L1≠up」拦截里有多少其实是**数据不足**（而非真震荡）？

证据链：
  1. `trend_layer.classify()`：`len(df) < _MIN_BARS(260)` 时返回
     `{"state": "sideways", "score": 0, "reason": "数据不足(N<260)"}` —— 与真实震荡
     **在 state/score 上不可区分**；
  2. `long_trend_v2` 的入场闸：`state != "up"` ⇒ 拒（`reason=f"L1={state}(score={score}) 非 up"`）；
  3. `mlto_cycle` 把它写进漏斗审计（stage=trend, tier=long）。
本脚本统计审计里 `L1=` 行的 score 分布与标的分布，并与 1d K 线存量交叉验证。
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from backend.services.mlto.midlong_direction_audit import _iter_rows  # noqa: E402

RX = re.compile(r"^L1=(?P<state>\w+)(?:\(score=(?P<score>-?\d+(?:\.\d+)?)\))?\s*(?P<tail>.*)$")
states = Counter()
scores = Counter()
by_sym = Counter()
score_by_sym = {}
n_l1 = 0
zero_score = 0
total = 0
for row in _iter_rows():
    total += 1
    reason = str(row.get("reason") or "")
    m = RX.match(reason)
    if not m:
        continue
    n_l1 += 1
    st = m.group("state")
    sc = m.group("score")
    states[st] += 1
    if sc is not None:
        scores[sc] += 1
        if float(sc) == 0.0:
            zero_score += 1
    sym = str(row.get("symbol") or "?")
    by_sym[sym] += 1
    m2 = re.match(r"L1=(\w+)\(score=(-?\d+)", reason)
    if m2:
        score_by_sym.setdefault(sym, Counter())[f"{m2.group(1)}/{m2.group(2)}"] += 1

print(f"审计总行 {total}；其中 L1= 行 {n_l1}")
print("state 分布:", dict(states))
print(f"\nscore 分布（前 12）: {dict(scores.most_common(12))}")
print(f"score==0 的行数: {zero_score} / {sum(scores.values())} = "
      f"{zero_score / max(1, sum(scores.values())):.1%}")
print("\n按标的 top12:", dict(by_sym.most_common(12)))
print("\n各标的的 state/score 组合（前 10 标的）:")
for sym, c in by_sym.most_common(10):
    print(f"  {sym:<10} {dict(score_by_sym.get(sym, {}).most_common(5))}")

# ── 交叉验证：1d K 线存量（<260 根 ⇒ 必然"数据不足"）──
print("\n=== 1d K 线存量（market DB）===")
try:
    import os
    from sqlalchemy import create_engine, text
    url = os.getenv("MARKET_DATABASE_URL") or os.getenv("DATABASE_URL")
    eng = create_engine(url, connect_args={"connect_timeout": 10})
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = c.execute(text(
            "select symbol, count(*) from crypto_klines where period='1d' "
            "and symbol in ('BTC','ETH','SOL','BNB','XRP','DOGE','LINK','AVAX','UNI','VIRTUAL',"
            "'XPL','ASTER','ONDO','LTC','DOT','ATOM','HYPE','ADA','AKE','AMZN') "
            "group by 1 order by 2"
        )).fetchall()
        for sym, n in rows:
            flag = "  ← <260 必然数据不足" if n < 260 else ""
            print(f"  {sym:<10} {n:>6}{flag}")
except Exception as e:
    print("  市场库查询失败:", str(e)[:140])
