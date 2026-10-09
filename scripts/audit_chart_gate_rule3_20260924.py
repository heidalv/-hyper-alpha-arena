# -*- coding: utf-8 -*-
"""图审闸第 3 条规则的前提检验：**"亏损后 24h 内同向重开"在 09-15+ 到底好不好？**

背景：
  - `midlong_chart_gate` 规则 3：24h 内该币该方向有"亏损全平"记录时，同向再开**必须**拿到
    图审同向支持（direction 一致且 strength ≥ 4），否则拒开。
  - 该规则依据是 **2026-08-31 周报**——属用户「09-15 之前一律作废」的样本。
  - 实测现状：日志里 24 条 `chart_gate_veto` **全部**是这条规则，且图审读数
    **`dir=0`（中性）**⇒ "没有表态"被当作"反对"。
  - 而该图审源 `dual:trend_chart_review` 被系统自己的复盘判为
    **命中率 32.7%、超额 −109.7bp、95% 上界 < 0（显著为负）并建议 disable**。

本脚本用 09-15+ 的真实成交回答：**"亏损后 24h 内同向重开"这一群体的前向结果，
与"非重开"群体比，是好是坏？**（若并不更差 ⇒ 规则 3 的前提在作废后不成立。）

口径：long 车道（mid+long），入场时刻 T；查同 symbol 在 [T−24h, T) 内是否有
`status=closed` 且 pnl<0 的 long 仓；前向取 24h 收盘对收盘（binance/okx 双源）。
只读。
"""
from __future__ import annotations

import bisect
import datetime as dt
import io
import statistics as st
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
SINCE = int(dt.datetime(2026, 9, 15, tzinfo=CST).timestamp())
H = 3600
FEE_PP = 0.10


def main() -> int:
    with psycopg.connect(ARENA, autocommit=True) as ac, psycopg.connect(MARKET, autocommit=True) as mc:
        acur = ac.cursor(); acur.execute("SET app.is_admin='on'")
        acur.execute(
            """select symbol, timeframe_tier, entry_price, original_size, opened_at, closed_at,
                      status, unrealized_pnl, partial_realized_pnl, close_price, mark_price
               from paper_positions
               where account_id=14 and side='long' and opened_at >= %s
               order by opened_at""",
            (dt.datetime.fromtimestamp(SINCE, CST).replace(tzinfo=None),))
        rows = acur.fetchall()
        mcur = mc.cursor()
        px: dict = {}
        recs = []
        for sym, tier, entry, size, opened, closed, status, upnl, part, cpx, mark in rows:
            if not entry or not size:
                continue
            t0 = int(opened.replace(tzinfo=CST).timestamp()) if opened.tzinfo is None else int(opened.timestamp())
            # 补 entry 时间（未平仓的用 now）
            end = closed.replace(tzinfo=CST).timestamp() if closed else dt.datetime.now(CST).timestamp()
            if sym not in px:
                mcur.execute(
                    """select timestamp, close_price from crypto_klines where symbol=%s
                       and exchange='binance' and period='1h' and environment='mainnet' order by timestamp""", (sym,))
                b = [(int(r[0]), float(r[1])) for r in mcur.fetchall()]
                px[sym] = ([x[0] for x in b], [x[1] for x in b])
            tss, cl = px[sym]
            j = bisect.bisect_left(tss, t0)
            if j >= len(tss) - 24:
                continue
            fwd24 = None
            if tss[-1] >= t0 + 24 * H:
                k = bisect.bisect_left(tss, t0 + 24 * H)
                if k < len(tss) and cl[j] > 0:
                    fwd24 = (cl[k] / cl[j] - 1.0) * 100.0
            p = float(cpx) if cpx else float(mark or entry)
            realized = ((p - float(entry)) * float(size) + float(part or 0))
            recs.append({"sym": sym, "tier": (tier or "").lower(), "t": t0, "end": end,
                         "realized_pct": realized / abs(float(entry) * float(size)) * 100 - FEE_PP,
                         "fwd24": fwd24, "status": status,
                         "pnl": float(upnl or 0) + float(part or 0)})
        # 判定"该次入场前 24h 内同 symbol 是否有亏损全平"
        closes = [r for r in recs if r["status"] == "closed"]
        for r in recs:
            prior_loss = False
            for c in closes:
                if c["sym"] != r["sym"]:
                    continue
                if 0 < (r["t"] - c["end"]) <= 24 * H and c["pnl"] < 0:
                    prior_loss = True
                    break
            r["after_loss"] = prior_loss
        a = [r for r in recs if r["after_loss"]]
        b = [r for r in recs if not r["after_loss"]]
        print("09-15 后 long 车道开仓 %d 笔" % len(recs))
        print("  **亏损后 24h 内同向重开**：n=%d ｜ 其余：n=%d\n" % (len(a), len(b)))
        print("  %-22s %5s %12s %12s %10s %10s" % ("分组", "n", "实现均值%", "f24h均值%", "实现胜率", "f24胜率"))
        for lab, seg in (("亏损后重开", a), ("非重开", b)):
            if not seg:
                print("  %-22s %5d  （无样本）" % (lab, 0)); continue
            rp = [x["realized_pct"] for x in seg]
            fw = [x["fwd24"] for x in seg if x["fwd24"] is not None]
            print("  %-22s %5d %+12.3f %12s %9.0f%% %9s"
                  % (lab, len(seg), st.mean(rp),
                     ("%+.3f" % st.mean(fw)) if fw else "n/a",
                     100.0 * sum(1 for x in rp if x > 0) / len(rp),
                     ("%.0f%%" % (100.0 * sum(1 for x in fw if x > 0) / len(fw))) if fw else "n/a"))
        if a and b:
            d_real = st.mean([x["realized_pct"] for x in a]) - st.mean([x["realized_pct"] for x in b])
            fa = [x["fwd24"] for x in a if x["fwd24"] is not None]
            fb = [x["fwd24"] for x in b if x["fwd24"] is not None]
            d_fwd = (st.mean(fa) - st.mean(fb)) if (fa and fb) else float("nan")
            print("\n  ⇒ 重开组 − 非重开组：实现 %+.3fpp ｜ 前向24h %+.3fpp" % (d_real, d_fwd))
            verdict = ("重开组更差 ⇒ 规则3前提在这段样本里**成立**（闸有理由）"
                       if (d_real < 0 and (d_fwd != d_fwd or d_fwd < 0))
                       else "重开组**并不更差** ⇒ 规则3的前提在这段样本里**不成立**")
            print("  ⇒ 判定：%s" % verdict)
        print("\n  逐笔（重开组）：")
        for x in a[:20]:
            print("    %s %-8s %-5s 实现 %+7.2f%%  f24 %s"
                  % (dt.datetime.fromtimestamp(x["t"], CST).strftime("%m-%d %H:%M"), x["sym"], x["tier"],
                     x["realized_pct"], ("%+.2f%%" % x["fwd24"]) if x["fwd24"] is not None else "n/a"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
