"""h529：重启前的**持仓体检**——确认 worker 是否空仓（重启会触发 orphan 强平付费）。

重启 worker 时若账上有持仓，F90 的 orphan 逻辑会**强制退出**这些仓位（= 付 taker 费
4bp + 穿价差）⇒ 必须挑空仓时刻重启，或明确接受这笔成本。

用法：python scripts/h529_preflight_positions.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATUS = ROOT / "logs" / "mm_lane_status.json"
OUT = ROOT / "research_l1" / "out" / "h529_preflight.json"


def main() -> int:
    if not STATUS.exists():
        print("状态文件不存在:", STATUS)
        return 1
    age = dt.datetime.now().timestamp() - STATUS.stat().st_mtime
    d = json.loads(STATUS.read_text(encoding="utf-8"))
    print(f"状态文件年龄 {age:.1f}s（<60s ⇒ worker 在跑）")
    print(f"顶层键：{sorted(d.keys())}")
    for k in ("ok", "ticks", "equity", "updated_at", "last_tick_ts"):
        if k in d:
            print(f"  {k} = {d[k]}")
    st = d.get("states") or {}
    now = dt.datetime.now().timestamp()
    open_pos = []
    print("\n逐币持仓：")
    for s, v in sorted(st.items()):
        if not isinstance(v, dict):
            print(f"  {s:>6s} （非 dict：{type(v).__name__}）")
            continue
        q = float(v.get("qty") or 0.0)
        op = float(v.get("opened_ts") or 0.0)
        age_s = (now - op) if op > 0 else 0.0
        flag = ""
        if abs(q) > 1e-12:
            flag = f"  ← **有持仓**（持有 {age_s:.0f}s）"
            open_pos.append(s)
        print(f"  {s:>6s} qty={q:+.8f} opened_age={age_s:7.0f}s "
              f"quote_age={(now - float(v.get('quote_ts') or 0)) if v.get('quote_ts') else 0:6.0f}s{flag}")
    print()
    if open_pos:
        print(f"⇒ **不宜立即重启**：{[*open_pos]} 有持仓，重启会走 orphan 强平（付 4bp taker）。")
        print("   建议：等这些币自然平掉（或接受该成本并记录为 ops 断点）。")
    else:
        print("⇒ **空仓**：此刻重启 worker 不产生 orphan 强平成本 ✓")
    OUT.write_text(json.dumps({"status_age_s": round(age, 1), "open_positions": open_pos,
                               "states": st}, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
