# -*- coding: utf-8 -*-
r"""[h896 2026-10-07] 逐币成绩单 → 数据驱动的逐币仓位闸门(自愈式,不永久拉黑)。

用户要求:「选币 / 币宇宙 / 交易选择需要优化」。病根(实测 lane_ledger):
宇宙 39 币靠「成交量 + 价差」选进来,**从不看"这个币在我们车道上实际赚不赚钱"**
⇒ 持续亏损的币(USELESS 24h −$153、SI −121bp/腿)一直全尺寸交易,
entry_block_symbols 空着不用,per_symbol_size_mult 是过时的手工档。

本工具每小时从 lane_ledger 算逐币成绩单(24h + 72h 双窗口),按统计规则生成
per_symbol_size_mult 写进 lane meta.params(worker 60s 内热采用,无需重启):

  灾难腿   n72≥5  且 avg72 < −30bp          ⇒ 0.15(探针量,伤害可忽略,数据不断)
  持续亏损 n72≥12 且 avg72 < −5bp 且 24h<0  ⇒ 0.25
  轻度亏损 n72≥12 且 −5 ≤ avg72 < −2bp      ⇒ 0.5
  持续赚钱 n72≥12 且 avg72 > +5bp 且 t>1.5  ⇒ 1.3(激进:赢家加码)
  其余(样本不足/不显著)                     ⇒ 1.0(默认,不写进表)

自愈设计(不用永久拉黑):所有币都保留交易 ⇒ 账本数据持续更新 ⇒ 亏损币一旦
转好,下一小时成绩单自动提权回来。永久拉黑 = 数据断流 = 永远翻不了身。

用法:
  .venv\Scripts\python.exe scripts\tools\symbol_scorecard.py            # dry-run(默认)
  .venv\Scripts\python.exe scripts\tools\symbol_scorecard.py --apply    # 真写进车道
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

LANE = "mm_asterdex"
AUDIT = ROOT / "data" / "symbol_scorecard.json"
LOG = ROOT / "logs" / "symbol_scorecard.log"


def log(msg: str) -> None:
    line = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " [scorecard] " + msg
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _stats(db, hours: float) -> dict:
    """逐币:n / 名义加权均 bp / 腿级 std / t 值 / pnl_usd。"""
    rows = db.execute(
        __import__("sqlalchemy").text(
            "SELECT symbol, COUNT(*) n,"
            " SUM(net_bp*notional)/NULLIF(SUM(notional),0) avg_bp,"
            " SUM(net_bp*notional)/10000.0 pnl_usd,"
            " STDDEV_SAMP(net_bp) sd_bp "
            "FROM lane_ledger WHERE lane_id=:l AND event='fill' "
            "AND ts > NOW() - make_interval(hours => :h) "
            "GROUP BY symbol"),
        {"l": LANE, "h": int(hours)}).fetchall()
    out = {}
    for sym, n, avg_bp, pnl, sd in rows:
        n = int(n or 0)
        avg = float(avg_bp or 0.0)
        sd = float(sd or 0.0)
        t = (avg / (sd / math.sqrt(n))) if (n >= 2 and sd > 0) else 0.0
        out[str(sym)] = {"n": n, "avg_bp": round(avg, 3), "t": round(t, 2),
                         "pnl_usd": round(float(pnl or 0.0), 2)}
    return out


def decide(s72: dict, s24: dict) -> tuple:
    """(size_mult, rule_name)。规则见模块 docstring。"""
    n72 = s72.get("n", 0)
    a72 = s72.get("avg_bp", 0.0)
    t72 = s72.get("t", 0.0)
    a24 = s24.get("avg_bp", 0.0)
    if n72 >= 5 and a72 < -30.0:
        return 0.15, "disaster_probe"
    if n72 >= 12 and a72 < -5.0 and a24 < 0.0:
        return 0.25, "persistent_loser"
    if n72 >= 12 and -5.0 <= a72 < -2.0:
        return 0.5, "mild_loser"
    if n72 >= 12 and a72 > 5.0 and t72 > 1.5:
        return 1.3, "winner_boost"
    return 1.0, "default"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真写进车道(默认 dry-run)")
    args = ap.parse_args()

    from sqlalchemy import text
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal

    with system_identity():
        with SessionLocal() as db:
            s72 = _stats(db, 72)
            s24 = _stats(db, 24)
            lane = db.execute(text(
                "SELECT meta_json FROM lane_registry WHERE lane_id=:l"),
                {"l": LANE}).fetchone()
    if not lane:
        log(f"车道不存在: {LANE}")
        return 1
    meta = json.loads(lane[0]) if isinstance(lane[0], str) else dict(lane[0] or {})
    universe = [str(s) for s in (meta.get("symbols") or [])]

    # 全宇宙逐币判定(账本里有的币 + 宇宙里的币取并集)
    proposal: dict = {}
    report = []
    for sym in sorted(set(universe) | set(s72.keys())):
        r72 = s72.get(sym, {"n": 0, "avg_bp": 0.0, "t": 0.0, "pnl_usd": 0.0})
        r24 = s24.get(sym, {"n": 0, "avg_bp": 0.0, "t": 0.0, "pnl_usd": 0.0})
        mult, rule = decide(r72, r24)
        if mult != 1.0:
            proposal[sym] = mult
        report.append({"symbol": sym, "mult": mult, "rule": rule,
                       "n72": r72["n"], "avg72_bp": r72["avg_bp"], "t72": r72["t"],
                       "pnl72": r72["pnl_usd"], "n24": r24["n"],
                       "avg24_bp": r24["avg_bp"], "pnl24": r24["pnl_usd"]})

    # 展示:按 72h pnl 升序(最亏的在前)
    report.sort(key=lambda r: r["pnl72"])
    log(f"{'symbol':12} {'mult':>5} {'rule':18} {'n72':>4} {'avg72bp':>8} "
        f"{'t72':>6} {'pnl72':>9} {'pnl24':>8}")
    for r in report:
        mark = " " if r["mult"] == 1.0 else ("+" if r["mult"] > 1.0 else "-")
        log(f"{mark} {r['symbol']:11} {r['mult']:5.2f} {r['rule']:18} "
            f"{r['n72']:4} {r['avg72_bp']:+8.2f} {r['t72']:6.2f} "
            f"{r['pnl72']:+9.2f} {r['pnl24']:+8.2f}")

    changed = {k: v for k, v in proposal.items()}
    log(f"提案: {json.dumps(changed, ensure_ascii=False)}")

    # 审计落盘
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.write_text(json.dumps({
        "ts": datetime.datetime.now().isoformat(), "lane": LANE,
        "applied": bool(args.apply), "proposal": changed, "report": report,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    if not args.apply:
        log("dry-run(未写入)。加 --apply 真写。")
        return 0

    # 写进 meta.params.per_symbol_size_mult(保留其它 params 键)
    params = dict(meta.get("params") or {})
    old = params.get("per_symbol_size_mult")
    params["per_symbol_size_mult"] = changed
    meta["params"] = params
    with system_identity():
        with SessionLocal() as db:
            db.execute(text(
                "UPDATE lane_registry SET meta_json = CAST(:m AS jsonb),"
                " updated_at = now() WHERE lane_id=:l"),
                {"m": json.dumps(meta, ensure_ascii=False), "l": LANE})
            db.commit()
    log(f"已写入 lane meta.params.per_symbol_size_mult(旧值 {old} ⇒ 新值 {changed});"
        f" worker 60s 内热采用,无需重启。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
