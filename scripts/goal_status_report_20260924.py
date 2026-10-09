# -*- coding: utf-8 -*-
"""目标④ 的落地物：**一条命令给出全量状态**（可反复跑，供每轮验收与用户自查）。

输出六块，全部现查现算、不引用任何缓存结论：
  1) 账户：权益 / 持仓 / 浮盈 / 近 1h 委托
  2) 车道盈亏（09-19 起，含费）：mid / long 的笔数、毛、费、净、均净
  3) 实时闸门普查：最近 N 小时 stage=fuse 决策数、各拦截原因占比
  4) regime 快照（10 主流币）：日线 regime / mom60 / chg24 / pos24 / 当前会命中哪条闸
  5) 本目标已落地改动：逐项读 .env 现值 + 代码默认，标出开关与回滚键
  6) 待成熟样本：learned 窄带闸命中里有多少已攒满 24h 前向窗口（决定下次复核时点）

用法：.venv\\Scripts\\python.exe scripts\\goal_status_report_20260924.py [日志回看小时数，默认 8]
只读。
"""
from __future__ import annotations

import bisect
import datetime as dt
import io
import re
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))
H = 3600
K200 = 2.0 / 201.0
MAJORS = ["BTC", "ETH", "SOL", "XRP", "BNB", "LINK", "AVAX", "UNI", "VIRTUAL", "ASTER"]
LOG = r"logs\backend.log"
FUSE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?\[MidLong\] stage=fuse")
REASON = re.compile(r"（(?:paper_probe×[\d.]+: )?([a-z_]+)")
LEARNED = re.compile(r"learned_long_up_chg24_([+-]?[0-9.]+)<")
WINDOW_H = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0


def env_val(key: str, default: str = "(未设)") -> str:
    try:
        with open(".env", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if s.startswith(key + "="):
                    return s.split("=", 1)[1].strip()
    except OSError:
        pass
    return default


def main() -> int:
    now = int(dt.datetime.now(CST).timestamp())
    print("=" * 100)
    print("目标状态报告  %s  （日志回看 %.0f 小时）"
          % (dt.datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"), WINDOW_H))
    print("=" * 100)

    with psycopg.connect(ARENA, autocommit=True) as ac, psycopg.connect(MARKET, autocommit=True) as mc:
        acur = ac.cursor(); acur.execute("SET app.is_admin='on'")
        mcur = mc.cursor()

        # ── 1) 账户 ──
        acur.execute("select count(*), coalesce(sum(unrealized_pnl),0) from paper_positions "
                     "where account_id=14 and status='open'")
        n_open, fl = acur.fetchone()
        acur.execute("select total_equity, available_balance from paper_balances where account_id=14")
        eq, avail = acur.fetchone()
        acur.execute("select count(*) from paper_orders where account_id=14 and created_at > now() - interval '1 hour'")
        n_ord = acur.fetchone()[0]
        print("\n[1] 账户")
        print("    权益 %.2f ｜ 可用 %.2f ｜ 持仓 %s 笔 ｜ 浮盈 %+.2f ｜ 近 1h 委托 %s"
              % (float(eq), float(avail), n_open, float(fl), n_ord))

        # ── 2) 车道盈亏（09-19 起，含费）──
        print("\n[2] 车道盈亏（09-19 起，毛=持仓账本，费=名义×0.10pp，净=毛−费）")
        print("    %-6s %5s %11s %10s %11s %11s" % ("车道", "n", "毛$", "费$", "净$", "均净$/笔"))
        for tier in ("mid", "long"):
            acur.execute(
                """select count(*), coalesce(sum(coalesce(unrealized_pnl,0)+coalesce(partial_realized_pnl,0)),0),
                          coalesce(sum(abs(entry_price*original_size)),0)
                   from paper_positions where account_id=14 and side='long' and timeframe_tier=%s
                     and opened_at >= '2026-09-19'""", (tier,))
            n, g, notional = acur.fetchone()
            n = int(n or 0); g = float(g or 0); fee = float(notional or 0) * 0.0010
            print("    %-6s %5d %+11.2f %10.2f %+11.2f %+11.3f"
                  % (tier, n, g, fee, g - fee, (g - fee) / n if n else 0))

        # ── 3) 实时闸门普查 ──
        cut = dt.datetime.fromtimestamp(now - WINDOW_H * H, CST).strftime("%Y-%m-%d %H:%M:%S")
        cnt: dict = {}
        tot = 0
        if __import__("os").path.exists(LOG):
            with open(LOG, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not FUSE.match(line):
                        continue
                    if line[:19] < cut:
                        continue
                    tot += 1
                    m = REASON.search(line)
                    key = m.group(1) if m else "(放行/其它)"
                    cnt[key] = cnt.get(key, 0) + 1
        print("\n[3] 实时闸门普查（近 %.0f 小时，stage=fuse 共 %d 条）" % (WINDOW_H, tot))
        for k, v in sorted(cnt.items(), key=lambda kv: -kv[1])[:8]:
            print("    %-34s %5d  %4.0f%%" % (k, v, 100.0 * v / tot if tot else 0))

        # ── 4) regime 快照 ──
        print("\n[4] regime 快照（10 主流，日线口径 EMA200+60日动量）")
        print("    %-8s %-6s %9s %9s %8s %-28s"
              % ("币", "regime", "mom60", "chg24", "pos24", "现况（会命中哪条闸）"))
        for s in MAJORS:
            mcur.execute("""select timestamp, close_price from crypto_klines where symbol=%s
                            and exchange='binance' and period='1d' and environment='mainnet'
                            order by timestamp""", (s,))
            d = mcur.fetchall()
            if len(d) < 260:
                print("    %-8s 数据不足" % s); continue
            dcl = [float(x[1]) for x in d]
            k = K200; e = dcl[0]
            for v in dcl[1:]:
                e = v * k + e * (1 - k)
            px = dcl[-1]
            mom60 = (px / dcl[-61] - 1.0) * 100 if len(dcl) > 61 else 0.0
            reg = "up" if (px > e and mom60 > 5) else ("down" if (px < e and mom60 < -5) else "chop")
            mcur.execute("""select close_price from crypto_klines where symbol=%s and exchange='binance'
                            and period='1h' and environment='mainnet' order by timestamp desc limit 25""", (s,))
            hrs = [float(x[0]) for x in mcur.fetchall()]
            if len(hrs) < 25:
                print("    %-8s %-6s %+8.1f%%  1h数据不足" % (s, reg, mom60)); continue
            chg24 = (hrs[0] / hrs[24] - 1.0) * 100
            hi, lo = max(hrs), min(hrs)
            pos = (hrs[0] - lo) / (hi - lo) * 100 if hi > lo else 50.0
            if reg == "down":
                note = "多头被拦（down）"
            elif reg == "up":
                if chg24 < 3.0:
                    note = "learned 窄带→缩仓×0.25"
                elif chg24 >= 6.0:
                    note = "spike 上限→缩仓×0.25"
                else:
                    note = "全尺寸放行"
            else:
                note = "chop：需 pos24≥60 且 chg24≥2" + ("→过" if (pos >= 60 and chg24 >= 2) else "→**拦**")
            if abs(chg24) >= 5.0:
                note += " ＋位置闸接刀/追高"
            print("    %-8s %-6s %+8.1f%% %+8.2f%% %7.0f%% %-28s"
                  % (s, reg, mom60, chg24, pos, note))

        # ── 5) 本目标已落地改动 ──
        print("\n[5] 本目标已落地改动（开关现值 / 回滚键）")
        rows = [
            ("位置闸 chg24 口径修复", "MIDLONG_LOCATION_CHG24_ABS_MAX", env_val("MIDLONG_LOCATION_CHG24_ABS_MAX", "60"),
             env_val("MIDLONG_LOCATION_CHG24_LEGACY_FRAC", "false"), "回滚=true 恢复旧的 ×100 猜测口径"),
            ("regime 缓存 TTL", "MIDLONG_REGIME_TTL_S", env_val("MIDLONG_REGIME_TTL_S", "1800"),
             env_val("MIDLONG_REGIME_TTL_S", "1800"), "回滚=1800 或删键"),
            ("宏观事件闸", "MACRO_EVENT_GUARD_ENABLED", env_val("MACRO_EVENT_GUARD_ENABLED", "false"),
             env_val("MACRO_EVENT_KINDS", "cpi"), "回滚=false"),
            ("中线分档止盈", "MID_STAGED_TP_ENABLED", env_val("MID_STAGED_TP_ENABLED", "false"), "-", "保持关（13 轮否证）"),
            ("长线 SL 上限", "MIDLONG_SL_MAX_PCT_LONG", env_val("MIDLONG_SL_MAX_PCT_LONG", "0.03"),
             env_val("MIDLONG_MAX_SL_PCT_LONG", "0.03"),
             "R8 落地 0.03→0.08（双样本过预登记五条）；回滚=两键同改回 0.03"),
            ("中线空头", "MIDLONG_OPEN_SHORT_ENABLED", env_val("MIDLONG_OPEN_SHORT_ENABLED", "false"), "-",
             "保持关（R2 全部确认型过滤器为负）"),
            ("paper 熔断记账", "MIDLONG_CIRCUIT_PAPER_LOCK", env_val("MIDLONG_CIRCUIT_PAPER_LOCK", "true(默认)"), "-",
             "回滚=false（回到 09-11 口径）"),
        ]
        for name, key, val, extra, rb in rows:
            print("    %-22s %-32s = %-10s %s" % (name, key, val, ("｜" + rb)))

        # ── 6) 待成熟样本 ──
        acur.execute("select 1")  # 保持连接
        tot_hit = 0; due = 0
        if __import__("os").path.exists(LOG):
            with open(LOG, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "midlong_long_learned_block: learned_long_up_chg24_" not in line:
                        continue
                    m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                    if not m:
                        continue
                    try:
                        ts = int(dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST).timestamp())
                    except ValueError:
                        continue
                    tot_hit += 1
                    if ts + 24 * H <= now:
                        due += 1
        print("\n[6] 待成熟样本（learned 窄带闸命中）")
        print("    日志命中行 %d ｜ 其中已攒满 24h 前向窗口 %d（%.0f%%）｜ 未成熟 %d"
              % (tot_hit, due, 100.0 * due / tot_hit if tot_hit else 0, tot_hit - due))
        if due:
            print("    ⇒ 可跑 scripts/audit_learned_band_hits_20260924.py 复核（命中口径）")
    print("\n" + "=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
