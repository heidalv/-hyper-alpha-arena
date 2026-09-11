# -*- coding: utf-8 -*-
"""审计工具：持仓计数与事件流的一致性对账（第 8 轮起纳入周期巡检）。

背景（§45.2）：`reduce_count` 被 5 处以上当作**减仓节流判据**，但主流减仓路径不更新该列，
导致判据恒为 0、单仓减仓上限形同不存在（实证：524 笔有减仓事件、仅 26 笔计数>0；
近 75 天 46 笔减仓 ≥4 次、最多 10 次）。已在 `paper_trading_engine._partial_close` 补自增。

本脚本作为**长期护栏**：任一计数与事件流长期不一致即报警（可周期复跑）。

用法：
  .venv\\Scripts\\python.exe backend/scripts/audit_position_event_consistency.py [--days 30]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL",
                      "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
OUT = ROOT / "data" / "position_event_consistency.json"
MAX_REDUCE_ASSUMED = 3  # 与 unified_exit_state_machine.max_reduce_count 默认值一致


def summarize(rows, max_reduce: int = MAX_REDUCE_ASSUMED) -> dict:
    """rows: [{id, symbol, tier, reduce_count, n_partial, n_final, closed_at}]（纯函数）。"""
    mism = [r for r in rows if int(r.get("reduce_count") or 0) != int(r.get("n_partial") or 0)]
    over = [r for r in rows if int(r.get("n_partial") or 0) > max_reduce]
    missing_final = [r for r in rows if int(r.get("n_final") or 0) == 0 and r.get("closed_at")]
    dup_final = [r for r in rows if int(r.get("n_final") or 0) > 1]
    n = len(rows)
    worst = []
    for r in sorted(rows, key=lambda x: -int(x.get("n_partial") or 0))[:5]:
        worst.append({
            "id": r.get("id"), "symbol": r.get("symbol"), "tier": r.get("tier"),
            "n_partial": int(r.get("n_partial") or 0),
            "reduce_count": int(r.get("reduce_count") or 0),
            "closed_at": str(r.get("closed_at") or ""),
        })
    return {
        "n": n,
        "mismatch_n": len(mism), "mismatch_rate": round(len(mism) / n, 3) if n else None,
        "over_max_n": len(over), "max_reduce_max": max_reduce,
        "missing_final_n": len(missing_final),
        "dup_final_n": len(dup_final),
        "worst": worst,
        "verdicts": [
            {"name": "reduce_count 与减仓事件数一致率 ≥95%",
             "ok": (len(mism) / n <= 0.05) if n else True,
             "detail": f"不一致 {len(mism)}/{n}"},
            {"name": "无仓位超过单仓减仓上限",
             "ok": len(over) == 0,
             "detail": f"> {max_reduce} 次的仓位 {len(over)} 笔"},
            {"name": "已平仓仓位均有终局事件",
             "ok": len(missing_final) == 0, "detail": f"缺终局事件 {len(missing_final)} 笔"},
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        raw = [dict(r._mapping) for r in c.execute(text(f"""
            select p.id, p.symbol, p.timeframe_tier as tier,
                   coalesce(p.reduce_count,0) as reduce_count, p.closed_at,
                   (select count(*) from position_exit_events e
                    where e.position_id=p.id and e.event_type='partial_exit_event') n_partial,
                   (select count(*) from position_exit_events e
                    where e.position_id=p.id and e.event_type='final_trade_outcome') n_final
            from paper_positions p
            where p.timeframe_tier in ('mid','long')
              and coalesce(p.closed_at, p.opened_at) >= now() - interval '{int(args.days)} days'
        """)).fetchall()]
    rep = summarize(raw)
    rep.update({"generated_at": datetime.now(timezone.utc).isoformat(), "days": args.days})
    print(f"=== 持仓计数 vs 事件流一致性（近 {args.days} 天，n={rep['n']}）===")
    print(f"  reduce_count ≠ 减仓事件数：{rep['mismatch_n']} 笔（{rep['mismatch_rate']}）")
    print(f"  减仓超过 {rep['max_reduce_max']} 次：{rep['over_max_n']} 笔")
    print(f"  已平仓缺终局事件：{rep['missing_final_n']} 笔；重复终局事件：{rep['dup_final_n']} 笔")
    print("  减仓次数最多的 5 笔：")
    for r in rep["worst"]:
        print(f"    #{r['id']} {r['symbol']} {r['tier']} 减仓={r['n_partial']} "
              f"计数列={r['reduce_count']}")
    print("\n验收判定：")
    for v in rep["verdicts"]:
        print(f"  [{'达标' if v['ok'] else '未达标'}] {v['name']} — {v['detail']}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
