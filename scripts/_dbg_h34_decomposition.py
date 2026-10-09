"""调试：H34 里 net@1s 与 (半价差@1s − |markout@1s|) 为何对不上。

理论上（买单）：
    half = (mid_fill − bid) / bid × 1e4
    mk   = (mid_{fill+τ} − bid) / bid × 1e4
    net  = half − |mk|
    ⇒ net = mid_fill − mid_{fill+τ}（同分母下）

实测：net@1s = −0.994，而 half@1s + (−|mk@1s|) = −0.029 − 0.073 = −0.102。
**差 0.89bp，无法解释。**

可能的来源（逐条排查）：
  ① `detail` 里 tau 分组与实际 net 数组不同源
  ② `net` 数组把买卖两侧混在一起，而 `half`/`mk` 的符号约定对卖单不同
  ③ `hs` 在某些分支取了错误的 mid
  ④ net 数组被多次 append（每 τ 一次），而 half/mk 只取 τ=1s 那一组
     ⇒ **数量不匹配**：net 有 3N 个元素，half/mk 只有 N 个

本脚本直接重算一遍最小样本，把上面四条逐一验证。

用法：
    .venv\\Scripts\\python.exe scripts\\_dbg_h34_decomposition.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main() -> int:
    import numpy as np
    import psycopg2
    import psycopg2.extras

    S = "BTCUSDT"
    HOURS = 2
    TAUS = [1000, 5000, 30000]
    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    since = f"(extract(epoch from now())*1000)::bigint - {HOURS*3600_000}"

    cur.execute(
        "SELECT event_ts_ms, (bids->0->>0)::float bp, (bids->0->>1)::float bq,"
        "       (asks->0->>0)::float ap, (asks->0->>1)::float aq"
        f"  FROM asterdex_depth_snapshots WHERE symbol='{S}' AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms")
    dep = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, price::float p, qty::float q, is_buyer_maker ibm"
        f"  FROM asterdex_trades WHERE symbol='{S}' AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms")
    tr = cur.fetchall()
    cur.execute(
        "SELECT event_ts_ms, bid_px::float b, ask_px::float a"
        f"  FROM asterdex_book_ticker WHERE symbol='{S}' AND event_ts_ms > {since}"
        " ORDER BY event_ts_ms")
    bt = cur.fetchall()
    cn.close()

    dts = np.array([int(x["event_ts_ms"]) for x in dep], dtype=np.int64)
    dbp = np.array([float(x["bp"] or 0) for x in dep])
    dbq = np.array([float(x["bq"] or 0) for x in dep])
    tts = np.array([int(x["event_ts_ms"]) for x in tr], dtype=np.int64)
    tpx = np.array([float(x["p"]) for x in tr])
    tq = np.array([float(x["q"]) for x in tr])
    tsell = np.array([bool(x["ibm"]) for x in tr])
    bts = np.array([int(x["event_ts_ms"]) for x in bt], dtype=np.int64)
    bmid = (np.array([float(x["b"]) for x in bt]) + np.array([float(x["a"]) for x in bt])) / 2.0

    def mid_after(t_ms, delta):
        i = int(np.searchsorted(bts, t_ms + delta, "left"))
        return float(bmid[i]) if i < len(bts) else None

    print(f"调试 H34 分解  {S}  {HOURS}h")
    print(f"  深度 {len(dts)}  逐笔 {len(tts)}  盘口 {len(bts)}\n")

    # 模拟：每 30s 报价一次，只做买单
    step = max(1, int(30_000 / max(1, int(np.median(np.diff(dts)) or 2000))))
    recs = []
    for i in range(0, len(dts), step):
        bid, la = float(dbp[i]), float(dbq[i])
        if bid <= 0 or la <= 0:
            continue
        t0 = int(dts[i])
        j = int(np.searchsorted(tts, t0, "left"))
        lim = int(np.searchsorted(tts, t0 + 120_000, "left"))
        cum = 0.0
        fb = -1
        for jj in range(j, min(lim, len(tts))):
            tj = int(tts[jj])
            di = int(np.searchsorted(dts, tj, "right")) - 1
            alive = (di >= 0 and float(dbp[di]) >= bid * (1 - 1e-4))
            if alive and tsell[jj] and abs(tpx[jj] - bid) / bid * 1e4 < 1.0:
                cum += float(tq[jj])
                if cum >= la:
                    fb = jj
                    break
        if fb < 0:
            continue
        tf = int(tts[fb])
        ii = int(np.searchsorted(bts, tf, "left"))
        if ii >= len(bts):
            continue
        mid_fill = float(bmid[ii])
        half = (mid_fill - bid) / bid * 1e4
        row = {"bid": bid, "mid_fill": mid_fill, "half": half}
        for tau in TAUS:
            mm = mid_after(tf, tau)
            if mm is None:
                row[f"mk{tau}"] = None
                row[f"net{tau}"] = None
            else:
                mk = (mm / bid - 1.0) * 1e4
                row[f"mk{tau}"] = mk
                row[f"net{tau}"] = half - abs(mk)
        recs.append(row)

    print(f"成交样本 {len(recs)} 笔（仅买侧，每 30s 报一次价）\n")
    print("%-6s %10s %10s %10s %10s %10s %10s %10s %10s"
          % ("tau", "half均值", "mk均值", "net均值", "half-|mk|", "残差", "mk中位", "net中位", "n"))
    for tau in TAUS:
        mk = np.array([r[f"mk{tau}"] for r in recs if r.get(f"mk{tau}") is not None])
        nt = np.array([r[f"net{tau}"] for r in recs if r.get(f"net{tau}") is not None])
        hf = np.array([r["half"] for r in recs if r.get(f"net{tau}") is not None])
        if not len(nt):
            continue
        lhs = hf.mean() - np.abs(mk).mean()
        print("%-6d %10.4f %10.4f %10.4f %10.4f %10.4f %10.4f %10.4f %10d"
              % (tau, hf.mean(), mk.mean(), nt.mean(), lhs, nt.mean() - lhs,
                 np.median(mk), np.median(nt), len(nt)))

    print("\n判读：")
    print("  · 若『残差』≈0 ⇒ 分解自洽，H34 的 by_tau 拼接有问题（混合了买卖/不同 tau）")
    print("  · 若『残差』≠0 ⇒ net 的算法与 half/mk 不同源，需查代码")
    print("\n另外：net = half − |mk| 用**绝对值**会在 mk 为正时也扣分，")
    print("      而理论上买单若 mid 上涨（mk>0）我们应**赚**，不该扣分。")
    print("      ⇒ 这本身就是个**公式错误**（应写成 net = half + mk，符号自带方向）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
