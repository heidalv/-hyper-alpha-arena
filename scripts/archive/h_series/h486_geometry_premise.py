"""h486：入场几何前提量化（h474 的前置证据）。

问题（研究结论/手续费根因与被动出场_20260929.md §1）：
  运行态实测**报价半宽 `avg_width_bp ≈ 0.97bp`**，而入场腿账面的**价差捕获只有 +0.40bp**
  ⇒ 差额 0.57bp 去哪了？这决定了"加宽报价"到底能不能赚到更多。

本脚本用只读数据回答三问：
  Q1 入场腿的价差捕获（`spread_bp`）分布，与运行态报价半宽对比 ⇒ **捕获率**；
  Q2 成交是否发生在**挂单价上**（`meta_json.px_exact_hit`）⇒ 判断差额来自
     "成交价劣于挂单价" 还是 "成交时中价已向挂单移动"（后者是逆向选择的机械结果）；
  Q3 `quote_ts` 覆盖率（挂单时刻是否入库）⇒ 决定能否把"成交时刻"与"挂单时刻"分开归因。

判读（用于 h474 的加宽实验设计）：
  · 若捕获率 ≈1（成交价≈挂单价、价差≈半宽）⇒ 加宽**按比例**增加每腿毛利，
    代价只是成交率下降 ⇒ 值得试；
  · 若捕获率 <<1（如 0.4）⇒ 差额主要来自"价格走到挂单才成交"这一机械事实，
    加宽对已成交腿的毛利提升**小于按比例**，且会进一步压低成交率 ⇒
    必须靠实测才知道净效果，不能靠模型外推。

用法：python scripts/h486_geometry_premise.py [--hours 3]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
STATUS = ROOT / "logs" / "mm_lane_status.json"
OUT = ROOT / "research_l1" / "out" / "h486_geometry.json"


def read_env_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def q(v, p):
    return v[min(len(v) - 1, int(p * (len(v) - 1)))] if v else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=3.0)
    a = ap.parse_args()
    stj = json.loads(STATUS.read_text(encoding="utf-8"))
    w = dict(stj.get("avg_width_bp") or {})
    print(f"运行态报价：半宽 bid={w.get('bid')} ask={w.get('ask')} bp | "
          f"σ={stj.get('avg_sigma')} | base_bp={stj.get('avg_base_bp')} | "
          f"模式={stj.get('quote_modes')}")
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(meta_json->>'exit_path',''), spread_bp::float8, "
                "price_bp::float8, net_bp::float8, fee_bp::float8, "
                "meta_json->>'px_exact_hit', meta_json->>'quote_ts', notional::float8 "
                "FROM lane_ledger WHERE lane_id=%s "
                "AND ts > now() - make_interval(mins => %s::int) "
                "AND symbol = ANY(%s)", (LANE, int(a.hours * 60),
                                         [str(s) for s in (stj.get("symbols") or [])]))
            rows = cur.fetchall()
    ent = [r for r in rows if r[0] == ""]
    exi = [r for r in rows if r[0] != ""]
    print(f"\n近 {a.hours:.0f}h：入场腿 {len(ent)} / 出场腿 {len(exi)}")
    if not ent:
        print("无入场腿")
        return 1
    sp = sorted(r[1] or 0.0 for r in ent)
    px = [r[2] or 0.0 for r in ent]
    net = [r[3] or 0.0 for r in ent]
    fee = [r[4] or 0.0 for r in ent]
    hit = [str(r[5] or "") for r in ent]
    qts = [r[6] for r in ent]
    print("=" * 88)
    print(f"入场腿 价差捕获 spread_bp: p10/p50/p90 = {q(sp,.1):+.2f}/{q(sp,.5):+.2f}/"
          f"{q(sp,.9):+.2f}bp  均值 {st.mean(sp):+.2f}")
    print(f"        价格项 price_bp : 均值 {st.mean(px):+.2f}bp | "
          f"净 net_bp: 均值 {st.mean(net):+.2f}bp | 费 fee_bp: 均值 {st.mean(fee):+.2f}bp")
    _hit_true = sum(1 for h in hit if h == "true")
    _hit_false = sum(1 for h in hit if h == "false")
    _hit_none = len(hit) - _hit_true - _hit_false
    print(f"        px_exact_hit: true={_hit_true} false={_hit_false} 缺={_hit_none}")
    print(f"        quote_ts 覆盖率: {sum(1 for x in qts if x)}/{len(qts)}"
          f"（{100.0*sum(1 for x in qts if x)/len(qts):.0f}%）")
    half = 0.5 * ((w.get("bid") or 0.0) + (w.get("ask") or 0.0))
    cap = (st.mean(sp) / half) if half else float("nan")
    print("=" * 88)
    print(f"**捕获率 = 入场腿价差捕获均值 / 运行态报价半宽 = {st.mean(sp):+.2f} / "
          f"{half:.2f} = {cap:.2f}**")
    if cap >= 0.8:
        verdict = ("捕获率 ≥0.8 ⇒ 成交基本吃满挂单距离 ⇒ **加宽按比例提升每腿毛利**，"
                   "代价只是成交率下降 ⇒ h474 的加宽实验值得做，且可用模型预估方向")
    elif cap >= 0.5:
        verdict = ("捕获率 0.5–0.8 ⇒ 有可观漏损（成交时中价已向挂单移动 = 逆向选择的机械结果）"
                   "⇒ 加宽对已成交腿的毛利提升**小于按比例**；净效果必须实测")
    else:
        verdict = ("捕获率 <0.5 ⇒ 账面捕获主要不是「挂单距离」而是「被动成交时的价格位置」"
                   "⇒ **单纯加宽报价的收益会远低于预期**；应优先查"
                   "「为什么成交价吃不到挂单距离」（成交判定口径 / 挂单被穿越时的成交价）")
    print("⇒", verdict)
    # 出场腿对照
    if exi:
        esp = [r[1] or 0.0 for r in exi]
        print(f"\n（对照）出场腿 价差 {st.mean(esp):+.2f}bp | "
              f"净 {st.mean([r[3] or 0.0 for r in exi]):+.2f}bp | "
              f"费 {st.mean([r[4] or 0.0 for r in exi]):+.2f}bp")
    OUT.write_text(json.dumps(
        {"hours": a.hours, "entry_legs": len(ent), "exit_legs": len(exi),
         "quoted_half_width_bp": half,
         "entry_spread_bp": {"p10": q(sp, .1), "p50": q(sp, .5), "p90": q(sp, .9),
                             "mean": round(st.mean(sp), 3)},
         "entry_price_bp_mean": round(st.mean(px), 3),
         "entry_net_bp_mean": round(st.mean(net), 3),
         "entry_fee_bp_mean": round(st.mean(fee), 3),
         "px_exact_hit": {"true": _hit_true, "false": _hit_false, "missing": _hit_none},
         "quote_ts_coverage": round(sum(1 for x in qts if x) / len(qts), 3),
         "capture_ratio": round(cap, 3), "verdict": verdict},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
