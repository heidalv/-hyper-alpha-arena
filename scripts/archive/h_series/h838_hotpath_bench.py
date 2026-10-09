# -*- coding: utf-8 -*-
"""[h838 用户"怎么调高速度/优化,先做调研"] 热路径基准测试。

测三件事(分开计时,才能定位该优化谁):
  ① `plan_tick` 单币单次耗时(纯 Python 决策逻辑);
  ② 其中的文件 I/O(situation 77KB / gate 每次解析);
  ③ 一个完整 tick 的墙钟耗时(来自 worker 日志的 ticks vs 时间)。
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

from backend.services.market_maker.runner import (  # noqa: E402
    SymbolState, TickDecision, plan_tick,
)
from backend.services.market_maker.core import InventoryBook  # noqa: E402

st_doc = json.loads((ROOT / "logs" / "mm_lane_status.json").read_text(encoding="utf-8"))
states = st_doc.get("states") or {}
sym = next(iter(states)) if states else "BTC"
raw = states.get(sym) or {}
print(f"用 {sym} 的实时状态做基准:qty={raw.get('qty')} avg_px={raw.get('avg_px')}")

st = SymbolState(str(sym))
st.qty = float(raw.get("qty") or 0.0)
st.avg_px = float(raw.get("avg_px") or 0.0)
st.avg_mid = float(raw.get("avg_mid") or 0.0)
st.opened_ts = float(raw.get("opened_ts") or 0.0)
st.quote_bid = float(raw.get("quote_bid") or 0.0)
st.quote_ask = float(raw.get("quote_ask") or 0.0)
st.quote_ts = float(raw.get("quote_ts") or 0.0)
st.mid_hist = [0.0] * 40

book = InventoryBook()
NOW = time.time()
mid = float(raw.get("quote_mid") or 1.0) or 1.0


def bench(n=300):
    t0 = time.perf_counter()
    for i in range(n):
        plan_tick(state=st, mid=mid, seg_low=mid, seg_high=mid,
                  seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=NOW + i,
                  limits=None, equity=150.0, fill_notional=0.0,
                  book=book, half_spread=mid * 0.0003, sigma_norm=0.0,
                  ofi=0.1, bid_qty=100.0, ask_qty=100.0,
                  vol_le_bid=1000.0, vol_ge_ask=1000.0,
                  book_stale=False)
    return (time.perf_counter() - t0) / n * 1000


# 预热 + 正式
bench(20)
ms = bench(300)
print(f"① plan_tick 单币单次: **{ms:.2f} ms**")
print(f"   ⇒ 33 币一个 tick 的纯决策 ≈ {ms * 33:.0f} ms")

# ② 文件 I/O
p_sit = ROOT / "data" / "flow_situation_last.json"
p_gate = ROOT / "data" / "flow_gate_last.json"
t0 = time.perf_counter()
for _ in range(100):
    json.loads(p_sit.read_text(encoding="utf-8"))
t_sit = (time.perf_counter() - t0) / 100 * 1000
t0 = time.perf_counter()
for _ in range(100):
    json.loads(p_gate.read_text(encoding="utf-8"))
t_gate = (time.perf_counter() - t0) / 100 * 1000
print(f"② 情况表解析 {t_sit:.2f} ms + 生产门解析 {t_gate:.2f} ms = "
      f"{(t_sit + t_gate) * 33:.0f} ms/tick(若每币都读)")

# ③ worker 实际节拍
log = ROOT / "logs" / "mm_lane_worker.log"
lines = [l for l in log.read_text(encoding="utf-8", errors="replace").splitlines()
         if "ticks=" in l and "[mm-worker]" in l][-2:]
if len(lines) == 2:
    def _pick(l, k):
        import re
        m = re.search(k + r"=(\d+)", l)
        return int(m.group(1)) if m else None
    import re
    ts = [re.search(r"^(\S+ \S+)", l) for l in lines]
    t0 = time.mktime(time.strptime(ts[0].group(1), "%Y-%m-%d %H:%M:%S"))
    t1 = time.mktime(time.strptime(ts[1].group(1), "%Y-%m-%d %H:%M:%S"))
    n0, n1 = _pick(lines[0], "ticks"), _pick(lines[1], "ticks")
    if n0 and n1 and t1 > t0:
        per = (t1 - t0) / max(1, (n1 - n0))
        print(f"③ worker 实测: {per*1000:.0f} ms/tick({n1 - n0} tick / {t1 - t0:.0f}s)"
              f" ⇒ 单币 {per*1000/33:.1f} ms")
        print(f"   ⇒ 文件 I/O 占比 ≈ {(t_sit + t_gate) / (per * 1000) * 100:.0f}%")
