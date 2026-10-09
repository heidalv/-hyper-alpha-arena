# -*- coding: utf-8 -*-
"""[h669 2026-10-01] 修复 lane_runtime_state 持仓成本价损坏:
现场:UNI qty=-1.4 / ENA qty=49 但 avg_px=0(面板显示"开仓 0.000000")。
做法:对每个"有仓位但无成本价"的币,从 lane_ledger 回放最近成交重建成本价,
写回状态表;worker 重启后加载。回放与引擎同口径(InventoryBook.apply_fill)。"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LANE = "mm_asterdex"
# [h669 修正] 只回放 h669 修复上线(2026-10-01 15:07+08)之后的腿:此前 h667
# 记账取整污染窗口的旧腿会把 13:30 的 0.217 当当前成本价 ⇒ 重启后止盈瞬间
# "满足" ⇒ 幽灵 +6.5U(15:13:58 ENA,已删)。另加 ±15% 现价 sanity:成本价与
# 现价偏离过大的回放结果一律拒绝(宁可显示缺省也不注入错成本)。
REPLAY_SINCE = "2026-10-01 15:07:00+08"
AVG_SANITY = 0.15


def main() -> int:
    import importlib.util

    import psycopg

    from backend.services.market_maker.core import InventoryBook

    _spec = importlib.util.spec_from_file_location(
        "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
    h = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(h)
    with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute("SELECT symbol, state_json FROM lane_runtime_state WHERE lane_id=%s",
                    (LANE,))
        rows = dict(cur.fetchall())
        healed = 0
        for sym, sj in rows.items():
            st = dict(sj or {})
            qty = float(st.get("qty") or 0.0)
            avg = float(st.get("avg_px") or 0.0)
            if abs(qty) <= 1e-12:
                continue
            if avg > 0:
                continue
            # 回放账本重建(仅回放 h669 修复后的干净腿)
            cur.execute(
                "SELECT ts, meta_json->>'side' AS side,"
                " (meta_json->>'qty')::float AS qty,"
                " (meta_json->>'fill_px')::float AS px,"
                " (meta_json->>'mid_px')::float AS mid_px"
                " FROM lane_ledger WHERE lane_id=%s AND symbol=%s AND event='fill'"
                " AND ts >= %s"
                " ORDER BY ts", (LANE, sym, REPLAY_SINCE))
            book = InventoryBook()
            for ts, side, q, px, mid in cur.fetchall():
                q2 = float(q or 0.0)
                p2 = float(px or 0.0)
                m2 = float(mid or 0.0)
                if q2 <= 0 or p2 <= 0:
                    continue
                if side not in ("buy", "sell"):
                    continue
                try:
                    book.apply_fill(symbol=sym, side=str(side), qty=q2,
                                    fill_px=p2, mid_px=(m2 or p2),
                                    fee_rate=0.0, now_ts=float(ts.timestamp()))
                except Exception:
                    pass
            pos = book.positions.get(sym)
            bq = book.qty(sym)
            if pos and abs(bq - qty) < max(1e-9, abs(qty) * 0.02):
                # [h669 修正] 现价 sanity:回放成本价偏离当前盘口 >15% ⇒ 拒绝
                # (0.217 对 0.272 的注入曾造成 +2430bp 幽灵止盈)。
                cur.execute(
                    "SELECT bid_px, ask_px FROM asterdex_book_ticker"
                    " WHERE symbol=%s AND bid_px>0 AND ask_px>bid_px"
                    " ORDER BY event_ts_ms DESC LIMIT 1", (f"{sym}USDT",))
                row = cur.fetchone()
                if row:
                    _mid_now = (float(row[0]) + float(row[1])) / 2.0
                    if _mid_now > 0 and abs(float(pos.avg_px) / _mid_now - 1.0) > AVG_SANITY:
                        print(f"!! {sym} 回放成本价 {pos.avg_px:.6f} 偏离现价 "
                              f"{_mid_now:.6f} 超 {AVG_SANITY:.0%},拒绝注入")
                        continue
                st["avg_px"] = float(pos.avg_px)
                st["avg_mid"] = float(pos.avg_mid)
                st["opened_ts"] = float(pos.opened_ts)
                cur.execute(
                    "UPDATE lane_runtime_state SET state_json=%s, updated_ts=now()"
                    " WHERE lane_id=%s AND symbol=%s",
                    (json.dumps(st, ensure_ascii=False), LANE, sym))
                print(f"ok 修复 {sym}: qty={bq} avg_px={pos.avg_px:.6f}")
                healed += 1
            else:
                print(f"!! {sym} 账本回放 qty={bq} 与状态 {qty} 不一致,跳过"
                      f"(需人工核查;账本自 {REPLAY_SINCE} 可能未含全部仓位)")
        print(f"完成:修复 {healed} 个币")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
