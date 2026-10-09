"""h512：逐币**盈亏平衡挂宽**——把 h509/h510 的机制结论变成可执行的定价规则。

机制（h510 已证）：
  入场侧净额 ≈ `捕获 − 逆向漂移` = `mult × 半价差 − markout60`，
  其中 `mult` = `spread_mult`（现行 0.5）、`markout60` = 该币的填单后 60s 有向漂移。
  ⇒ **盈亏平衡所需倍数** `mult* = markout60 / 半价差`。
  · `mult* > 0.5` ⇒ 现行挂宽太窄（每笔填单亏）；
  · `mult* < 0.5` ⇒ 现行太宽（本可更窄、换更多腿量）；
  · `mult* < 0` ⇒ 漂移本身有利（可更激进）。

频率代价用 h505 实测的**腿数对挂宽弹性 β = −0.91**（腿数 ∝ 挂宽^β）估算：
  只对"受影响的档位"（`mult × 半价差 < 该币实际挂宽` 的部分）按比例折算。

产出：逐币 `多*` / 建议倍数 / 预测净额 / 预测腿速变化，以及组合层面的净效果。
**本脚本只算不部署**——逐币倍数需要引擎支持（`QuoteParams` 目前是全局的），
属代码改动，必须先由你裁定是否做。

用法：python scripts/h512_coin_breakeven_width.py [--hours 48]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import pathlib
import statistics as st
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h512_breakeven_width.json"
LAG_S = 45
BETA = -0.91          # h505 实测弹性
CUR_MULT = 0.5


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


def mid_at(cur, sym, t_ms, tol=20000):
    cur.execute(
        "SELECT (ARRAY_AGG((bid_px+ask_px)/2 ORDER BY event_ts_ms DESC))[1]::float8 "
        "FROM asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > %s "
        "AND event_ts_ms <= %s AND bid_px>0 AND ask_px>bid_px",
        (sym + "USDT", t_ms - tol, t_ms + tol))
    r = cur.fetchone()
    return float(r[0]) if r and r[0] else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48.0)
    a = ap.parse_args()
    dsn = read_env_dsn()
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'symbols' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            r = cur.fetchone()
            syms = [str(s) for s in (r[0] if r and isinstance(r[0], list) else [])]
            cur.execute(
                "SELECT symbol, "
                "COALESCE(AVG(spread_bp) FILTER "
                "  (WHERE COALESCE(meta_json->>'exit_path','')=''),0)::float8 "
                "FROM lane_ledger WHERE lane_id=%s AND symbol = ANY(%s) "
                "AND ts > now() - make_interval(hours => %s::int) GROUP BY 1",
                (LANE, syms, int(a.hours)))
            cap_by_coin = {r[0]: float(r[1] or 0.0) for r in cur.fetchall()}
            cur.execute(
                "SELECT ts, symbol, COALESCE(meta_json->>'side',''), "
                "ABS(COALESCE((meta_json->>'qty')::float8,0))::float8, "
                "COALESCE(meta_json->>'exit_path',''), "
                "COALESCE((meta_json->>'fill_px')::float8,0)::float8, "
                "COALESCE(notional,0)::float8 "
                "FROM lane_ledger WHERE lane_id=%s AND symbol = ANY(%s) "
                "AND ts > now() - make_interval(hours => %s::int) ORDER BY symbol, ts",
                (LANE, syms, int(a.hours)))
            rows = cur.fetchall()
    by_sym = collections.OrderedDict()
    for r in rows:
        by_sym.setdefault(r[1], []).append(r)
    ents = []
    legs_per_coin = collections.Counter()
    for sym, rs in by_sym.items():
        cum = 0.0
        for ts, _s, side, qty, ep, px, noti in rs:
            signed = abs(qty) if str(side).lower() == "buy" else -abs(qty)
            before = cum
            cum += signed
            legs_per_coin[sym] += 1
            if ep == "" and (abs(before) < 1e-9 or (before > 0) == (signed > 0)) and px > 0:
                ents.append({"sym": sym, "ts": ts, "side": str(side).lower(), "px": float(px)})
    mk = dsn.replace("/alpha_arena", "/alpha_market")
    per: dict = collections.defaultdict(list)
    per300: dict = collections.defaultdict(list)
    with psycopg.connect(mk, autocommit=True) as cm:
        with cm.cursor() as cur:
            for e in ents:
                t_ms = int((e["ts"] - dt.timedelta(seconds=LAG_S)).timestamp() * 1000)
                m60 = mid_at(cur, e["sym"], t_ms + 60000)
                m300 = mid_at(cur, e["sym"], t_ms + 300000)
                if m60 is None:
                    continue
                sgn = 1.0 if e["side"] == "buy" else -1.0
                per[e["sym"]].append(sgn * (m60 - e["px"]) / e["px"] * 1e4)
                if m300 is not None:
                    per300[e["sym"]].append(sgn * (m300 - e["px"]) / e["px"] * 1e4)
            hs = {}
            for s in syms:
                now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
                cur.execute(
                    "SELECT COALESCE(AVG((ask_px-bid_px)/2.0/"
                    "NULLIF((ask_px+bid_px)/2.0,0))*1e4,0)::float8 "
                    "FROM asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > %s "
                    "AND bid_px>0 AND ask_px>bid_px",
                    (s + "USDT", now_ms - int(a.hours * 3600_000)))
                hs[s] = float(cur.fetchone()[0] or 0.0)
    print(f"近 {a.hours:.0f}h 逐币盈亏平衡挂宽（现行 spread_mult={CUR_MULT}，"
          f"腿数弹性 β={BETA}）")
    print("⚠️ 代数口径（第一版写错过，这里记下来）：**markout 本身已含捕获**"
          "（`markout = (mid_{t+60} − fill_px)` 有向），所以不能再做「捕获 − markout」。"
          "正确的加宽推算：`net(f) = markout + (f − 1) × 实测捕获`，"
          "盈亏平衡倍数 `f* = 1 − markout/捕获`（f*=1 表示现行宽度刚好打平）。")
    print("=" * 118)
    print(f"{'币':6s} {'腿数':>6s} {'入场腿':>6s} {'半价差':>7s} {'实测捕获':>8s} "
          f"{'markout60':>10s} {'t':>6s} {'盈亏平衡f*':>10s} {'建议f':>6s} "
          f"{'预测净bp':>9s} {'腿数变化':>8s}")
    rowsout = []
    for s in syms:
        mo = per.get(s) or []
        cap = cap_by_coin.get(s)
        if len(mo) < 20 or not cap or hs.get(s, 0) <= 0:
            continue
        m = st.mean(mo)
        sd = st.stdev(mo) if len(mo) > 1 else 0.0
        t = m / (sd / math.sqrt(len(mo))) if sd > 0 else 0.0
        h = hs[s]
        # 盈亏平衡倍数：net(f) = m + (f−1)·cap = 0 ⇒ f* = 1 − m/cap
        f_star = 1.0 - m / cap if cap > 0 else float("inf")
        if f_star > 3.0:
            sug = 3.0
            note = "不可行"
        elif f_star < 0.35:
            sug = 0.35
            note = "可收窄"
        else:
            sug = max(0.35, min(3.0, f_star * 1.15))
            note = ""
        pred = m + (sug - 1.0) * cap
        legs = legs_per_coin[s]
        dlegs = legs * ((sug / CUR_MULT) ** BETA) - legs
        print(f"{s:6s} {legs:6d} {len(mo):6d} {h:7.2f} {cap:8.2f} {m:+10.2f} "
              f"{t:+6.2f} {f_star:10.2f} {sug:6.2f} {pred:+9.2f} {dlegs:+8.0f} {note}")
        rowsout.append({"sym": s, "legs": legs, "entries": len(mo),
                        "half_spread_bp": round(h, 3),
                        "capture_bp": round(cap, 3),
                        "markout60_bp": round(m, 3), "t": round(t, 2),
                        "breakeven_f": round(f_star, 3) if f_star != float("inf") else None,
                        "suggested_f": round(sug, 3),
                        "suggested_net_bp": round(pred, 3),
                        "legs_delta": round(dlegs, 1), "note": note})
    print("=" * 118)
    print("★ 关键附加检验：**60s 的负漂移在 300s 是否回摆**"
          "（若回摆 ⇒ 问题是「出场太早」，不是「这个币不能做」）")
    for s in syms:
        a60, a300 = per.get(s) or [], per300.get(s) or []
        if len(a60) < 20 or len(a300) < 20:
            continue
        m60, m300 = st.mean(a60), st.mean(a300)
        rev = m300 - m60
        flag = "**回摆**" if (m60 < -0.3 and rev > 0.3) else (
            "继续恶化" if (m60 < -0.3 and rev < -0.3) else "基本持平")
        print(f"  {s:6s} n={len(a300):5d}  markout60={m60:+6.2f}  markout300={m300:+6.2f}  "
              f"Δ(300−60)={rev:+6.2f}bp  ⇒ {flag}")
    print("=" * 118)
    total_legs = sum(r["legs"] for r in rowsout) or 1
    d_legs = sum(r["legs_delta"] for r in rowsout)
    print(f"组合估算（仅按 β={BETA} 的腿数弹性，只算入场侧 60s 口径）：")
    print(f"  腿数 {total_legs} → {total_legs + d_legs:.0f}"
          f"（{100*d_legs/total_legs:+.1f}%）")
    unviable = [r["sym"] for r in rowsout if r["note"] == "不可行"]
    tighten = [r["sym"] for r in rowsout if r["note"] == "可收窄"]
    if unviable or tighten:
        verdict = (f"**贴 touch/半价差以内挂单不可行的币：{unviable}**"
                   f"（盈亏平衡倍数 >3×，即无论怎么加宽都赚不回来）；"
                   f"**可收窄换腿量的币：{tighten}** ⇒ "
                   f"逐币挂宽倍数比「换币」改动更小、且两头都受益"
                   f"（不可行的少做、有余量的多做），但需要引擎支持（`QuoteParams` 目前全局）")
    else:
        verdict = "现行宽度在各币上都接近打平 ⇒ 挂宽不是主要矛盾"
    print("\n⇒ 裁决:", verdict)
    print("⚠️ 只算不部署；且入场侧 60s 口径 ≠ 往返净额（出场成本另计）。")
    OUT.write_text(json.dumps({"hours": a.hours, "beta": BETA,
                               "current_mult": CUR_MULT, "rows": rowsout,
                               "portfolio": {"legs_before": total_legs,
                                             "legs_after": round(total_legs + d_legs, 1),
                                             "legs_delta_pct": round(100 * d_legs / total_legs, 2)},
                               "verdict": verdict}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
