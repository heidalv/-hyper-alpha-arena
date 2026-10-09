"""h510：**逐币 markout**——逆选择到底发生在哪些币上（币种重配的核心判据）。

背景（h509 逐币画像）：高噪声币给我们的**绝对捕获更大**
（NEAR 1.13bp / ARB 1.15bp / ENA 1.30bp vs BNB 0.14bp / XRP 0.19bp），
但止损率是 BNB 的 12–15 倍（NEAR 24.7%、ENA 29.4% vs BNB 2.0%）。
这与 Glosten-Milgrom 的经典关系一致：**价差宽本身就是"知情流"的补偿**，
而我们的挂宽是 `0.5 × 半价差`（**在价差宽的地方少收**）⇒ 系统性错配。

本脚本直接量**逐币的填单逆向选择**：每笔入场腿的
`markout@30s / @60s = 有向（t+h 中价 − 填单价）`（bp，正=填完朝我们要的方向走）。
  · markout 明显为负的币 ⇒ 我们的每一笔填单都在被知情流挑走 ⇒
    **在该币上做市（在当前挂宽下）结构性亏钱**，应减少参与或显著加宽；
  · markout ≈0/为正的币 ⇒ 可继续。

用法：python scripts/h510_coin_markout.py [--hours 48]
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
OUT = ROOT / "research_l1" / "out" / "h510_coin_markout.json"
LAG_S = 45


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
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'symbols' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            r = cur.fetchone()
            syms = [str(s) for s in (r[0] if r and isinstance(r[0], list) else [])]
            cur.execute(
                "SELECT ts, symbol, COALESCE(meta_json->>'side',''), "
                "ABS(COALESCE((meta_json->>'qty')::float8,0))::float8, "
                "COALESCE(meta_json->>'exit_path',''), COALESCE(net_bp,0)::float8, "
                "COALESCE((meta_json->>'fill_px')::float8,0)::float8, "
                "COALESCE(spread_bp,0)::float8, COALESCE(notional,0)::float8 "
                "FROM lane_ledger WHERE lane_id=%s AND symbol = ANY(%s) "
                "AND ts > now() - make_interval(hours => %s::int) ORDER BY symbol, ts",
                (LANE, syms, int(a.hours)))
            rows = cur.fetchall()
    by_sym = collections.OrderedDict()
    for r in rows:
        by_sym.setdefault(r[1], []).append(r)
    ents = []
    for sym, rs in by_sym.items():
        cum = 0.0
        for ts, _s, side, qty, ep, net, px, sp, noti in rs:
            signed = abs(qty) if str(side).lower() == "buy" else -abs(qty)
            before = cum
            cum += signed
            if ep == "" and (abs(before) < 1e-9 or (before > 0) == (signed > 0)) and px > 0:
                ents.append({"sym": sym, "ts": ts, "side": str(side).lower(),
                             "px": float(px), "net": float(net or 0.0),
                             "sp": float(sp or 0.0), "noti": float(noti or 0.0)})
    print(f"近 {a.hours:.0f}h 入场腿 {len(ents)}（在役币 {syms}）")
    per: dict = collections.defaultdict(lambda: {"mo30": [], "mo60": [], "net": [],
                                                 "sp": [], "usd": 0.0})
    mk = read_env_dsn().replace("/alpha_arena", "/alpha_market")
    with psycopg.connect(mk, autocommit=True) as cm:
        with cm.cursor() as cur:
            for e in ents:
                t_lag = e["ts"] - dt.timedelta(seconds=LAG_S)
                t_ms = int(t_lag.timestamp() * 1000)
                m30 = mid_at(cur, e["sym"], t_ms + 30000)
                m60 = mid_at(cur, e["sym"], t_ms + 60000)
                if m30 is None or m60 is None:
                    continue
                sgn = 1.0 if e["side"] == "buy" else -1.0
                g = per[e["sym"]]
                g["mo30"].append(sgn * (m30 - e["px"]) / e["px"] * 1e4)
                g["mo60"].append(sgn * (m60 - e["px"]) / e["px"] * 1e4)
                g["net"].append(e["net"])
                g["sp"].append(e["sp"])
                g["usd"] += e["net"] * e["noti"] / 1e4
    print("=" * 108)
    print(f"{'币':6s} {'腿数':>6s} {'markout30s':>11s} {'t':>6s} {'markout60s':>11s} "
          f"{'t':>6s} {'捕获bp':>8s} {'已实现净bp':>10s} {'净额$':>8s}")
    res = {}
    for s in syms:
        g = per.get(s)
        if not g or len(g["mo60"]) < 5:
            print(f"{s:6s} {0 if not g else len(g['mo60']):6d}  （样本不足）")
            continue
        def ms(v):
            m = st.mean(v)
            sd = st.stdev(v) if len(v) > 1 else 0.0
            t = m / (sd / math.sqrt(len(v))) if sd > 0 else 0.0
            return m, t
        m30, t30 = ms(g["mo30"])
        m60, t60 = ms(g["mo60"])
        print(f"{s:6s} {len(g['mo60']):6d} {m30:+11.2f} {t30:+6.2f} {m60:+11.2f} "
              f"{t60:+6.2f} {st.mean(g['sp']):8.2f} {st.mean(g['net']):10.2f} "
              f"{g['usd']:8.2f}")
        res[s] = {"n": len(g["mo60"]), "mo30": round(m30, 3), "t30": round(t30, 2),
                  "mo60": round(m60, 3), "t60": round(t60, 2),
                  "capture_bp": round(st.mean(g["sp"]), 3),
                  "net_bp": round(st.mean(g["net"]), 3), "usd": round(g["usd"], 3)}
    print("=" * 108)
    bad = [s for s, v in res.items() if v["mo60"] < -1.0 and v["t60"] < -1.5]
    good = [s for s, v in res.items() if abs(v["mo60"]) <= 1.0 or v["t60"] > 1.5]
    if bad:
        verdict = (f"**markout 显著为负的币：{bad}** ⇒ 我们的每一笔填单都被知情流挑走"
                   f"（在当前挂宽下结构性亏钱）⇒ 应减少参与或对该币显著加宽；"
                   f"相对尚可的币：{good}")
    elif res:
        verdict = (f"没有币的 markout 显著为负（最好/最差：" +
                   "、".join(f"{s} {v['mo60']:+.2f}bp(t={v['t60']:+.1f})"
                            for s, v in sorted(res.items(),
                                               key=lambda kv: kv[1]["mo60"])) +
                   "）⇒ 币种间逆选择差异不足以支持'删币'，"
                   "应优先做入场状态侧（③/趋势闸）")
    else:
        verdict = "样本不足"
    print("⇒ 裁决:", verdict)
    OUT.write_text(json.dumps({"hours": a.hours, "entries": len(ents), "per_coin": res,
                               "negative_markout": bad, "ok_coins": good,
                               "verdict": verdict}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
