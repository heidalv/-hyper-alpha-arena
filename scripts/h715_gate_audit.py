# -*- coding: utf-8 -*-
"""[h715 阶段1 2026-10-02] 闸门自证结算:被拦腿的短窗反事实盈亏。

设计文档 §L2:闸门唯一正当理由 = 被它挡掉的腿实测是亏的。本脚本读
`data/gate_block_shadow.jsonl`(runner 阶段0a 已开始积累),用盘口 10s 短窗
markout 结算每条被拦腿"若没拦会赚/亏多少",按闸门汇总:
  counterfactual_bp(buy) = (mid[t+10s] − bid_fill) / bid_fill × 1e4
  counterfactual_bp(sell)= (ask_fill − mid[t+10s]) / ask_fill × 1e4
**拦对了** = 汇总均值 < 0(不拦会亏);**拦错了** = 均值 > 0(拦掉了利润)。
输出 data/gate_audit_last.json + 控制台报告。节流提示:样本需 ≥50/闸 才可信。
"""
from __future__ import annotations

import bisect
import importlib.util
import io
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SHADOW = ROOT / "data" / "gate_block_shadow.jsonl"
MARKOUT_SEC = 10.0
MIN_N = 30


def _market_dsn() -> str:
    from backend.services.market_maker.attribution import _market_dsn as f
    return f()


def main() -> int:
    import psycopg

    try:
        lines = SHADOW.read_text(encoding="utf-8").splitlines()
    except Exception:
        print("影子文件为空/不存在(阶段0a 正在积累,几小时后再跑)")
        return 0
    entries = [json.loads(l) for l in lines if l.strip()]
    print(f"影子条目 {len(entries)} 条")
    syms = sorted({e["symbol"] for e in entries})
    t_min = min(e["ts"] for e in entries)
    now = time.time()
    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        book: Dict[str, Tuple[List[float], List[float]]] = {}
        for s in syms:
            cur.execute(
                "SELECT event_ts_ms, (bid_px+ask_px)/2 FROM asterdex_book_ticker"
                " WHERE symbol=%s AND event_ts_ms >= %s AND event_ts_ms <= %s"
                " AND bid_px>0 AND ask_px>bid_px ORDER BY event_ts_ms",
                (s + "USDT", int((t_min - 5) * 1000), int(now * 1000)))
            rows = cur.fetchall()
            book[s] = ([float(r[0]) / 1000.0 for r in rows],
                       [float(r[1]) for r in rows])
            print(f"  {s}: {len(rows)} 盘口行")

    gates: Dict[str, List[float]] = {}
    for e in entries:
        tms, mids = book.get(e["symbol"], ([], []))
        if not tms:
            continue
        i = bisect.bisect_right(tms, e["ts"]) - 1
        j = bisect.bisect_right(tms, e["ts"] + MARKOUT_SEC) - 1
        if i < 0 or j <= i or mids[i] <= 0:
            continue
        m_fut = mids[j]
        side = e.get("side")
        if side == "buy":
            fill = e.get("bid") or mids[i]
            cf = (m_fut - fill) / fill * 1e4
            gates.setdefault(e["skip"], []).append(cf)
        elif side == "sell":
            fill = e.get("ask") or mids[i]
            cf = (fill - m_fut) / fill * 1e4
            gates.setdefault(e["skip"], []).append(cf)
        elif side == "both":
            # 双边各算一条(拟同时挂双边)
            bid_f = e.get("bid") or mids[i]
            ask_f = e.get("ask") or mids[i]
            cf_b = (m_fut - bid_f) / bid_f * 1e4
            cf_s = (ask_f - m_fut) / ask_f * 1e4
            gates.setdefault(e["skip"], []).append((cf_b + cf_s) / 2.0)

    print(f"\n== 闸门自证报告(短窗 {MARKOUT_SEC:.0f}s 反事实;n≥{MIN_N} 才可信)== ")
    print(f"  {'闸门':<22}{'n':>5}{'若不拦 均值':>12}{'判定':>10}")
    out = {"ts": now, "markout_sec": MARKOUT_SEC, "gates": {}}
    for g, v in sorted(gates.items(), key=lambda kv: -len(kv[1])):
        mean = sum(v) / len(v)
        verdict = ("拦对了(省下亏损)" if mean < 0 else "拦错了(挡了利润)") if len(v) >= MIN_N else "样本不足"
        print(f"  {g:<22}{len(v):>5}{mean:>+12.2f}bp{verdict:>14}")
        out["gates"][g] = {"n": len(v), "mean_cf_bp": round(mean, 3), "verdict": verdict}
    (ROOT / "data" / "gate_audit_last.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # [h720 阶段1 2026-10-02] **自动提案**:某闸反事实证明"拦掉了利润"
    # (n≥MIN_N 且 mean_cf_bp ≥ +1.0) ⇒ 生成参数放宽提案(幅度 ≤30%,单变量,
    # 带回滚),供 DSH 桥/快判执行。闸门→参数映射:
    _GATE_PARAM = {
        "trend_h697_both": ("trend_pause_bp", "raise"),
        "trend_h697_buy": ("trend_pause_bp", "raise"),
        "trend_h697_sell": ("trend_pause_bp", "raise"),
        "trend_add_block": ("trend_add_block_min_bp", "raise"),
        "ofi_toxic_sell": ("ofi_block_threshold", "raise"),
        "ofi_toxic_buy": ("ofi_block_threshold", "raise"),
        "vpin_high": ("vpin_pause_threshold", "raise"),
        "mp_skew_buy": ("mp_block_bp", "raise"),
        "mp_skew_sell": ("mp_block_bp", "raise"),
    }
    proposals = []
    try:
        from backend.services import lane_registry as reg
        params = dict(((reg.get_lane("mm_asterdex") or {}).get("meta") or {}).get("params") or {})
    except Exception:
        params = {}
    # [h724 防棘轮] 参数若在快判网内(verdict_at 未过)或刚被本系统改过
    # (pending 里的 applied_at < 6h)⇒ 本次不再为它生成新提案。否则闸门反事实
    # 持续为正时,审计器会每 2h 再提 +30%,参数被无限棘轮放宽。
    # [h730 2026-10-02 修 bug] pending 条目在**裁决后会被移除** ⇒ 护栏失效,
    # 实测 trend_pause_bp 15→19.5 判"保持"仅 1.6h 后审计又提 19.5→25.35。
    # 补上判史(self_tuner_history.jsonl):该参数 6h 内有裁决记录同样抑制。
    try:
        _pend_raw = json.loads((ROOT / "logs" / "self_tuner_pending.json").read_text(encoding="utf-8"))
        _recent_pending = {
            str(p.get("param")) for p in _pend_raw
            if float(p.get("verdict_at") or 0) > now
            or now - float(p.get("applied_at") or 0) < 6 * 3600
        }
    except Exception:
        _recent_pending = set()
    try:
        _hist_path = ROOT / "logs" / "self_tuner_history.jsonl"
        if _hist_path.exists():
            for _line in _hist_path.read_text(encoding="utf-8").splitlines():
                try:
                    _h = json.loads(_line)
                except Exception:
                    continue
                if now - float(_h.get("ts") or 0) < 6 * 3600:
                    _recent_pending.add(str(_h.get("param") or ""))
    except Exception:
        pass
    for g, info in out["gates"].items():
        if info["n"] < MIN_N or info["mean_cf_bp"] < 1.0:
            continue
        entry = _GATE_PARAM.get(g)
        if not entry:
            continue
        param, _dir = entry
        if param in _recent_pending:
            continue          # [h724] 防棘轮:该参数刚改过/在快判网内
        # 有效值:注册表缺键时用数据类默认(h709 的 8.0 默认只在 LaneRiskLimits)
        cur = float(params.get(param) or 0.0)
        if cur <= 0:
            from backend.services.market_maker.core import LaneRiskLimits
            cur = float(getattr(LaneRiskLimits(), param, 0.0) or 0.0)
        if cur <= 0:
            continue
        new = round(cur * 1.3, 3)
        # 显著性:每样本 σ 与 t 值,供桥复核判断(均值>1bp 但 t<1.5 视为弱证据)
        vals = gates[g]
        mu = info["mean_cf_bp"]
        import statistics as _st
        sd = _st.pstdev(vals) if len(vals) > 1 else 0.0
        tval = mu / (sd / (len(vals) ** 0.5)) if sd > 0 else 0.0
        proposals.append({
            "gate": g, "n": info["n"], "mean_cf_bp": info["mean_cf_bp"],
            "std_bp": round(sd, 2), "t": round(tval, 2),
            "param": param, "current": cur, "proposed": new,
            "rollback": cur, "change_pct": 30,
            "reason": f"影子反事实 {info['n']} 次拦截均值 {info['mean_cf_bp']:+.2f}bp>0 ⇒ 拦掉了利润,放宽 30%",
        })
    if proposals:
        (ROOT / "data" / "gate_proposal_last.json").write_text(
            json.dumps({"ts": now, "proposals": proposals},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[h720] 生成 {len(proposals)} 条放宽提案 → data/gate_proposal_last.json"
              f" (DSH 桥复核后落库)")
        for p in proposals:
            print(f"  {p['gate']:<20} {p['param']} {p['current']} → {p['proposed']}")
    else:
        (ROOT / "data" / "gate_proposal_last.json").write_text(
            json.dumps({"ts": now, "proposals": []}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print("\n[h720] 无达标提案(样本不足或闸门拦对了)")
    print("\n✓ 已写 data/gate_audit_last.json")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
