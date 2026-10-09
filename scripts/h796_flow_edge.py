# -*- coding: utf-8 -*-
"""[h796 2026-10-04 用户"选币机制是重头"] 流可预测性选币分。

超短流交易范式的选币标准(替代/优先于做市标准):
  · **流边(flow_edge_bp)** = 每币"买侧实现 markout − 卖侧实现 markout"的绝对值
    —— 该币的方向能否被区分出来(h358/h361:流对齐 t=10~22,逆流 t=−7~−18)。
    edge 大 = 这个币的方向可预测 = 超短交易有 edge;
  · **好侧为正(best_bp>0)** = 只交易好侧时确实赚钱;
  · 成交量/价差/跳空过滤保留为门槛(不是排序依据)。

数据:近 60 分钟开仓腿(direction_card.fetch_open_legs)+ 该窗口内盘口中价
(asterdex_book_ticker 采样)。复用 direction_rows_from_legs(与方向卡同口径)。
输出 data/flow_edge_last.json:每币 {n, buy_bp, sell_bp, edge_bp, best_bp,
flow_tradeable}。任务每 10 分钟跑;选择器下一版把它并入评分。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


def _dsn():
    _spec = importlib.util.spec_from_file_location(
        "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
    h = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(h)
    return h.read_env_dsn()


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from backend.services.market_maker.direction_card import fetch_open_legs
    from backend.services.market_maker.attribution import _market_dsn
    import psycopg

    legs = fetch_open_legs(lane_id=LANE, minutes=60)
    syms = sorted({str(l.get("symbol") or "").upper() for l in legs if l.get("symbol")})
    mids = {}
    if syms:
        with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
            for s in syms:
                cur.execute(
                    "SELECT (bid_px+ask_px)/2.0 FROM asterdex_book_ticker"
                    " WHERE symbol=%s AND bid_px>0 AND ask_px>bid_px"
                    " AND event_ts_ms > (extract(epoch from now())-120)*1000"
                    " ORDER BY event_ts_ms DESC LIMIT 1", (s + "USDT",))
                r = cur.fetchone()
                if r and r[0]:
                    mids[s] = float(r[0])

    # 每币:开仓腿"成交→现在"的 markout(买腿为正号方向),按腿归"顺流/逆流"
    # (以该币近期主导移动方向为流;同向=with,反向=against)。
    agg = {}
    for lg in legs:
        sym = str(lg.get("symbol") or "").upper()
        side = str(lg.get("side") or "").lower()
        if side in ("long", "b"):
            side = "buy"
        elif side in ("short", "s"):
            side = "sell"
        mid_now = float(mids.get(sym) or 0.0)
        mid_fill = float(lg.get("mid_px") or 0.0)
        notional = float(lg.get("notional") or 0.0)
        if not sym or side not in ("buy", "sell") or mid_now <= 0 or mid_fill <= 0:
            continue
        sgn = 1.0 if side == "buy" else -1.0
        mo = sgn * (mid_now - mid_fill) / mid_fill * 1e4
        b = agg.setdefault(sym, {})
        b.setdefault("mo", []).append((mo, notional))
    score = []
    for sym, b in agg.items():
        mos = b["mo"]
        if not mos:
            continue
        tot_w = sum(n for _, n in mos) or 1.0
        avg = sum(mo * n for mo, n in mos) / tot_w
        with_ = [(mo, n) for mo, n in mos if mo * avg >= 0]
        ag_ = [(mo, n) for mo, n in mos if mo * avg < 0]
        w_avg = sum(mo * n for mo, n in with_) / (sum(n for _, n in with_) or 1.0)
        a_avg = sum(mo * n for mo, n in ag_) / (sum(n for _, n in ag_) or 1.0)
        edge = abs(w_avg - a_avg)
        n = len(mos)
        tradeable = bool(edge >= 3.0 and w_avg > 0.0 and n >= 8)
        score.append({"symbol": sym, "n": n, "with_bp": round(w_avg, 2),
                      "against_bp": round(a_avg, 2), "edge_bp": round(edge, 2),
                      "flow_tradeable": tradeable})
    score.sort(key=lambda x: -x["edge_bp"])
    out = {"ts": time.time(), "as_of": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "n_coins": len(score),
           "flow_tradeable": [x["symbol"] for x in score if x["flow_tradeable"]],
           "detail": score}
    (ROOT / "data" / "flow_edge_last.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"== 流可预测性选币分(近 60 分钟开仓腿,{len(score)} 币)== ")
    for x in score[:18]:
        tag = "✓流交易" if x["flow_tradeable"] else " "
        print(f"  {tag} {x['symbol']:<10} n={x['n']:>3} 顺流 {x['with_bp']:+6.2f}bp "
              f"逆流 {x['against_bp']:+6.2f}bp **流边 {x['edge_bp']:>5.2f}bp**")
    print(f"  流可交易币({len(out['flow_tradeable'])}): {out['flow_tradeable']}")
    print("  ✓ 已写 data/flow_edge_last.json")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
