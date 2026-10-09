# -*- coding: utf-8 -*-
"""H284 出场政策模拟：固定 6bp 止损 vs vol 缩放止损 vs 时间止损 vs 反转衰减离场。

# 背景：止损腿实盘平均 −22bp（jump 滑穿），止盈 +11bp —— 出场是最大漏点。
# 文献（lit_review）：废除固定 6bp 硬止损 → 波动率缩放 + 时间止损 + 反转衰减。
# 本脚本在 48h tick 上回放四种出场政策，量化每腿净额与止损深度。

# 口径（与 H280 一致）：1s mid 网格、60s 去重叠事件；
#   入场：|r90| ≥ 3bp 时逆势（对应新参数 lookback=90s），记入场 mid。
#   出场（逐秒推进，最长 300s）：
#     P0 固定：TP +5bp / SL −6bp / 超时 90s
#     P1 vol缩放：SL = max(6, 1.5×σ90)，TP = 0.8×SL，超时 90s
#     P2 时间止损：无价格 SL，TP +5bp，超时 90s
#     P3 反转衰减：TP +5bp；当 30s 趋势重新朝入场不利方向延伸 ≥4bp 即离场（衰减检测），
#          无固定 SL，超时 120s 兜底
#   净额：mid-to-mid；taker 出场（SL/TP 触发类）再扣 4bp 费；超时/衰减类按 maker 0 费。

# 用法

    python scripts/h284_exit_policy.py --hours 48
"""
from __future__ import annotations

import argparse
import bisect
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h284_exit_policy.json"
CUR = ["SOLUSDT", "DOGEUSDT", "ETHUSDT", "BNBUSDT"]


def market_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    base = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        base = base.replace(j, "")
    return base.rsplit("/", 1)[0] + "/alpha_market"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48.0)
    ap.add_argument("--ago", type=float, default=0.0, help="窗口前移小时数（交叉验证）")
    ap.add_argument("--lookback", type=float, default=90.0, help="入场回看秒（60=模型模式/90=现行/120=旧）")
    ap.add_argument("--thr", type=float, default=3.0, help="入场 |r_lookback| 最小幅度 bp")
    ap.add_argument("--only-p3", action="store_true", help="只跑反转衰减政策（影子对照用）")
    ap.add_argument("--decay", type=float, default=4.0, help="衰减离场阈值 bp（P3 用）")
    ap.add_argument("--timeout", type=float, default=120.0, help="P3 超时秒")
    ap.add_argument("--tp", type=float, default=5.0, help="P3 止盈 bp")
    ap.add_argument("--decay-window", type=float, default=30.0,
                    help="衰减趋势窗口秒（快栈 30 / 慢栈 120）")
    ap.add_argument("--fill-d", type=float, default=0.0,
                    help="报价距离 bp（0=旧口径 mid 即成交；>0=挂 mid∓d，60s 内打穿才成交）")
    ap.add_argument("--fill-mode", default="fixed", choices=["fixed", "chase"],
                    help="fixed=信号时点价位固定等 60s；chase=每 tick 贴当前 mid 重挂（线上真实行为）")
    ap.add_argument("--ofi-conf", type=float, default=0.0,
                    help="OFI 衰竭确认阈值（>0：流与趋势同向且|OFI|≥此值 → 不进场）")
    ap.add_argument("--ofi-conf-long", type=float, default=None,
                    help="多头侧独立阈值（默认跟随 --ofi-conf）")
    ap.add_argument("--ofi-conf-short", type=float, default=None,
                    help="空头侧独立阈值（默认跟随 --ofi-conf）")
    ap.add_argument("--symbols", default=None,
                    help="逗号分隔币种子集（默认 SOLUSDT,DOGEUSDT,ETHUSDT,BNBUSDT）")
    # [h327] 入场方式：maker=被动挂单（现行，等价格来碰） / taker=信号触发即吃价差主动入场。
    # 动机：h323 markout 显示"顺势回调"信号有 +4~+9bp 边际，但被动入场只在价格主动
    # 回来时才成交（那时已被逆选择）。主动入场成本 = 半价差 + 4bp taker 费 ≈ 4.1bp，
    # 若边际真有 8bp，主动入场应显著优于被动。用同一信号、同一出口政策直接对比。
    ap.add_argument("--entry-mode", default="maker", choices=["maker", "taker"])
    ap.add_argument("--half-spread-bp", type=float, default=0.07,
                    help="主动入场的半价差成本（bp）")
    # [h330] 信号选择（30s-5min 域）：
    #   fade_rlb    = 现行 r60 反转（--lookback/--thr 控制回看与阈值）
    #   trend_r60   = r60 反转且与 300s 趋势同向（h324 顺势回调复合）
    #   breakout300 = 突破 300s 区间高点/低点追势（h328: 120s +0.56 / 300s +0.96bp, t≈2.6）
    #   r300_mom    = 300s 动量（--thr 为 |r300| 阈值）
    ap.add_argument("--signal", default="fade_rlb",
                    choices=["fade_rlb", "trend_r60", "breakout300", "r300_mom",
                             "vwap_revert"])
    ap.add_argument("--trend-confirm", type=float, default=20.0,
                    help="trend_r60 信号的 300s 趋势确认阈值 bp")
    # [h324] 趋势闸门（与 runner.trend_blocked_side 同语义）：
    #   block_with    = 封"顺势侧"（现行/F204 行为：涨禁买、跌禁卖）
    #   block_counter = 封"逆势侧"（涨禁卖加仓、跌禁买加仓；h322/h323 markout 指向的方向）
    ap.add_argument("--gate-mode", default="none",
                    choices=["none", "block_with", "block_counter", "trend_only"])
    ap.add_argument("--gate-thr", type=float, default=20.0, help="闸门趋势阈值 bp")
    ap.add_argument("--gate-lookback", type=float, default=300.0, help="闸门趋势回看秒（线上=300s）")
    # [h339] VPIN 毒性暂停（与线上 h338 同口径）：滚动 20 桶 |OFI| 均值 > 阈值 → 不进场
    ap.add_argument("--vpin-thr", type=float, default=0.0,
                    help="VPIN_20 阈值（0=关闭；线上 0.65）")
    # [h352] OFI 方向模式：fade=流与趋势同向时不进场（旧）；follow=强流时只做流方向侧（P7）
    ap.add_argument("--ofi-mode", default="fade", choices=["fade", "follow"])
    ap.add_argument("--ofi-follow-thr", type=float, default=0.6,
                    help="follow 模式的 |OFI| 阈值（h351: θ=0.6 时 +0.74bp@30s）")
    a = ap.parse_args()
    global CUR
    if getattr(a, "symbols", None):
        CUR = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]

    import psycopg

    series = {}
    vwap_series = {}
    for sym in CUR:
        with psycopg.connect(market_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                    FROM (SELECT (event_ts_ms/1000) AS bucket, bid_px, ask_px
                          FROM asterdex_book_ticker
                          WHERE event_ts_ms >= (extract(epoch from now())*1000 - (%s+%s)*3600*1000)::bigint
                            AND event_ts_ms <  (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                            AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                    ORDER BY bucket, bid_px
                """, (a.hours, a.ago, a.ago, sym))
                recs = cur.fetchall()
                # [h353] 成交流（P2 VWAP 回归用）：1s 桶 VWAP 前缀和
                cur.execute("""
                    SELECT (event_ts_ms/1000)::bigint AS b, sum(price*qty), sum(qty)
                    FROM asterdex_trades
                    WHERE event_ts_ms >= (extract(epoch from now())*1000 - (%s+%s)*3600*1000)::bigint
                      AND event_ts_ms <  (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                      AND symbol = %s
                    GROUP BY b ORDER BY b
                """, (a.hours, a.ago, a.ago, sym))
                trecs = cur.fetchall()
        d = {int(b): ((float(x) + float(y)) / 2.0) for b, x, y in recs}
        ks = sorted(d)
        series[sym] = (ks, [d[k] for k in ks])
        tt = [int(r[0]) for r in trecs]
        vn, vd = [0.0], [0.0]
        for r in trecs:
            vn.append(vn[-1] + float(r[1]))
            vd.append(vd[-1] + float(r[2]))
        vwap_series[sym] = (tt, vn, vd)
        print(f"  {sym:10} {len(ks)} 点", flush=True)

    # [h316] OFI 桶（15s，market_trades_aggregated 用裸符号）
    ofi_map = {}
    thr_long = a.ofi_conf_long if a.ofi_conf_long is not None else a.ofi_conf
    thr_short = a.ofi_conf_short if a.ofi_conf_short is not None else a.ofi_conf
    if max(thr_long, thr_short) > 0:
        for sym in CUR:
            bare = sym[:-4] if sym.endswith("USDT") else sym
            with psycopg.connect(market_dsn()) as c:
                with c.cursor() as cur:
                    cur.execute("""
                        SELECT timestamp, taker_buy_notional, taker_sell_notional
                        FROM market_trades_aggregated
                        WHERE symbol=%s
                          AND timestamp >= (extract(epoch from now())*1000 - (%s+%s)*3600*1000)::bigint
                          AND timestamp <  (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                        ORDER BY timestamp
                    """, (bare, a.hours, a.ago, a.ago))
                    recs2 = cur.fetchall()
            ofi_map[sym] = {}
            for ts_ms, buy_n, sell_n in recs2:
                b = int(ts_ms) // 15000
                tot = float(buy_n or 0) + float(sell_n or 0)
                ofi_map[sym][b] = (float(buy_n or 0) - float(sell_n or 0)) / tot if tot > 0 else 0.0
        print(f"  OFI 桶已加载（{len(ofi_map)} 币）", flush=True)

    # [h339] VPIN_20（滚动 20 桶 |OFI| 均值），与线上 h338 同口径
    vpin_map = {}
    if a.vpin_thr > 0:
        for sym in CUR:
            buckets = sorted(ofi_map.get(sym, {}))
            vals = {}
            for i, b in enumerate(buckets):
                lo = max(0, i - 19)
                vals[b] = sum(abs(ofi_map[sym][buckets[j]]) for j in range(lo, i + 1)) / (i - lo + 1)
            vpin_map[sym] = vals
        print(f"  VPIN 桶已构建（{len(vpin_map)} 币）", flush=True)

    # 每币把时间戳做成连续数组，方便逐秒推进
    def run_policy(name, sl_fn, tp_fn, timeout, decay_fn=None, lookback=a.lookback, thr=a.thr):
        res = {"n": 0, "gross_bp": [], "net_bp": [], "mae_bp": [], "exit_types": {},
               "side": [], "blocked": {"long": 0, "short": 0}, "gate_blocked": 0}
        for sym, (ks, px) in series.items():
            n = len(ks)
            last = -1e18
            for i in range(n):
                if ks[i] - last < 60.0:
                    continue
                last = ks[i]
                j = i
                while j >= 0 and ks[i] - ks[j] < lookback:
                    j -= 1
                # [h330] 信号分派（30s-5min 域）
                sign = None
                rlb = None
                if a.signal == "fade_rlb":
                    if j < 0 or ks[i] - ks[j] < lookback * 0.9 or px[j] <= 0:
                        continue
                    rlb = (px[i] - px[j]) / px[j] * 1e4
                    if abs(rlb) < thr:
                        continue
                    # [h316] OFI 衰竭确认：流与趋势同向且 |OFI|≥阈值 → 不进场
                    if sym in ofi_map and (thr_long > 0 or thr_short > 0 or a.ofi_mode == "follow"):
                        ofi = ofi_map[sym].get(ks[i] // 15, 0.0)
                        if a.ofi_mode == "follow":
                            # [h352] 顺流挂单（P7）：强流时只做流方向一侧
                            if ofi > a.ofi_follow_thr:
                                sign = 1.0
                            elif ofi < -a.ofi_follow_thr:
                                sign = -1.0
                            else:
                                continue
                        else:
                            if rlb > 0 and thr_short > 0 and ofi > thr_short:
                                res["blocked"]["short"] += 1
                                continue
                            if rlb < 0 and thr_long > 0 and ofi < -thr_long:
                                res["blocked"]["long"] += 1
                                continue
                            sign = -1.0 if rlb > 0 else 1.0
                    if sign is None:
                        sign = -1.0 if rlb > 0 else 1.0
                elif a.signal == "vwap_revert":
                    # [h353] P2：价格偏离 60s 成交 VWAP ≥2bp → 回归 VWAP
                    _tt, _vn, _vd = vwap_series[sym]
                    i1 = bisect.bisect_right(_tt, ks[i] - 60)
                    i2 = bisect.bisect_right(_tt, ks[i])
                    den = _vd[i2] - _vd[i1]
                    vwap = (_vn[i2] - _vn[i1]) / den if den > 0 else px[i]
                    dev = (px[i] - vwap) / vwap * 1e4
                    if abs(dev) < 2.0:
                        continue
                    sign = -1.0 if dev > 0 else 1.0
                else:
                    # 需要 300s 上下文（trend_r60 / breakout300 / r300_mom）
                    j300 = i
                    while j300 >= 0 and ks[i] - ks[j300] < 300.0:
                        j300 -= 1
                    if j300 < 0 or ks[i] - ks[j300] < 270 or px[j300] <= 0:
                        continue
                    seg300 = px[j300:i + 1]
                    r300 = (px[i] - px[j300]) / px[j300] * 1e4
                    if a.signal == "trend_r60":
                        if j < 0 or ks[i] - ks[j] < lookback * 0.9 or px[j] <= 0:
                            continue
                        r60 = (px[i] - px[j]) / px[j] * 1e4
                        if abs(r60) < thr or abs(r300) < a.trend_confirm:
                            continue
                        s = -1.0 if r60 > 0 else 1.0
                        if (s > 0) != (r300 > 0):
                            continue    # 只做与 5min 趋势同向的回调（h324 结论）
                        sign = s
                    elif a.signal == "breakout300":
                        hi300 = max(seg300[:-1])
                        lo300 = min(seg300[:-1])
                        if px[i] > hi300:
                            sign = 1.0
                        elif px[i] < lo300:
                            sign = -1.0
                        else:
                            continue
                    elif a.signal == "r300_mom":
                        if abs(r300) < thr:
                            continue
                        sign = 1.0 if r300 > 0 else -1.0
                # 波动率 σ90（过去 90s 每秒 |ret| 和）
                vj = i
                while vj >= 0 and ks[i] - ks[vj] < 90.0:
                    vj -= 1
                sig = 0.0
                if vj >= 0 and i - vj > 5:
                    seg = px[vj:i + 1]
                    sig = sum(abs((seg[t + 1] - seg[t]) / seg[t]) * 1e4 for t in range(len(seg) - 1))
                # [h339] VPIN 毒性暂停（线上 h338 同口径：VPIN_20 > 阈值 → 不进场）
                if a.vpin_thr > 0:
                    _vp = vpin_map.get(sym, {}).get(ks[i] // 15)
                    if _vp is not None and _vp > a.vpin_thr:
                        res["gate_blocked"] += 1
                        continue
                # [h324] 趋势闸门（与 runner.trend_blocked_side 同语义，作用在加仓进场侧）
                if a.gate_mode != "none" and a.gate_thr > 0:
                    gj = i
                    while gj >= 0 and ks[i] - ks[gj] < a.gate_lookback:
                        gj -= 1
                    if gj >= 0 and ks[i] - ks[gj] >= a.gate_lookback * 0.9 and px[gj] > 0:
                        gtr = (px[i] - px[gj]) / px[gj] * 1e4
                        if a.gate_mode == "trend_only":
                            # [h331] 只在强趋势时段交易（|趋势|≥阈值），且只做顺势侧。
                            # 依据：h323 实测"顺势回调"成交 markout +4~+8bp，而平缓时段
                            # 成交 markout −0.8~−1.6bp（占总成交 89%）——时段开关比方向开关更本质。
                            side = "sell" if sign < 0 else "buy"
                            with_trend = (side == "buy" and gtr > 0) or (side == "sell" and gtr < 0)
                            if abs(gtr) < a.gate_thr or not with_trend:
                                res["gate_blocked"] += 1
                                continue
                        elif abs(gtr) >= a.gate_thr:
                            side = "sell" if sign < 0 else "buy"
                            with_trend = (side == "buy" and gtr > 0) or (side == "sell" and gtr < 0)
                            if (a.gate_mode == "block_with" and with_trend) or \
                               (a.gate_mode == "block_counter" and not with_trend):
                                res["gate_blocked"] += 1
                                continue
                # [h315 扩展] --fill-d > 0：挂 mid∓d，未来 60s 内打穿才成交；
                # 未打穿 = 未成交撤单（不产生交易）。成交价 = 挂单价。
                # [h332] --fill-mode chase：动态贴盘口重报价（与线上 runner 行为一致：
                # 每 tick 按当前 mid 重挂，价格向下 tick 0.07bp 即成交）。fixed（默认）
                # = 固定在信号时刻价位等 60s —— 实测无法复现线上 +8bp 的顺势腿
                # （快速趋势里价位不回踩旧价，fixed 只在"假趋势回头"时成交，符号反转）。
                entry_px = px[i]
                i_entry = i
                entry_fee_bp = 0.0
                if a.entry_mode == "taker":
                    # [h327] 主动入场：立即以对手价成交（吃半价差）+ 4bp taker 费
                    entry_px = px[i] * (1.0 + sign * a.half_spread_bp / 1e4)
                    entry_fee_bp = 4.0
                elif a.fill_d > 0:
                    tf = None
                    if a.fill_mode == "chase":
                        for tt in range(i + 1, min(n, i + 61)):
                            lvl = px[tt - 1] * (1 - sign * a.fill_d / 1e4)
                            if (sign > 0 and px[tt] <= lvl) or (sign < 0 and px[tt] >= lvl):
                                tf = tt
                                entry_px = lvl
                                break
                    else:
                        lvl = px[i] * (1 - sign * a.fill_d / 1e4)
                        for tt in range(i + 1, min(n, i + 61)):
                            if (sign > 0 and px[tt] <= lvl) or (sign < 0 and px[tt] >= lvl):
                                tf = tt
                                break
                        if tf is not None:
                            entry_px = lvl
                    if tf is None:
                        continue  # 未成交
                    i_entry = tf
                sl = sl_fn(sig)
                tp = tp_fn(sig)
                exit_type = "timeout"
                t = i_entry
                exit_bp = None
                while t + 1 < n and ks[t + 1] - ks[i_entry] <= max(timeout, 300.0):
                    t += 1
                    mv = (px[t] - entry_px) / entry_px * 1e4 * sign  # 有利方向为正
                    if mv <= -sl:
                        exit_type = "sl"
                        exit_bp = mv
                        break
                    if mv >= tp:
                        exit_type = "tp"
                        exit_bp = mv
                        break
                    if decay_fn is not None:
                        d = decay_fn(ks, px, i_entry, t, sign)
                        if d is not None:
                            exit_type = "decay"
                            exit_bp = mv
                            break
                    if ks[t] - ks[i_entry] >= timeout:
                        exit_type = "timeout"
                        exit_bp = (px[t] - entry_px) / entry_px * 1e4 * sign
                        break
                if exit_bp is None:
                    exit_bp = (px[t] - entry_px) / entry_px * 1e4 * sign
                res["n"] += 1
                res["side"].append("short" if sign < 0 else "long")
                res["gross_bp"].append(exit_bp)
                # 费用：出场 SL/TP 记 taker 4bp（超时/衰减按 maker 0）+ 入场腿费
                # （maker 模式 0；taker 模式 4bp，见 --entry-mode）
                fee = entry_fee_bp + (4.0 if exit_type in ("sl", "tp") else 0.0)
                res["net_bp"].append(exit_bp - fee)
                # MAE：整个持有期最不利漂移
                mae = 0.0
                for tt in range(i_entry, t + 1):
                    mv = (px[tt] - entry_px) / entry_px * 1e4 * sign
                    if mv < mae:
                        mae = mv
                res["mae_bp"].append(mae)
                res["exit_types"][exit_type] = res["exit_types"].get(exit_type, 0) + 1
        g = res["gross_bp"]
        net = res["net_bp"]
        side_stats = {}
        for sd in ("long", "short"):
            idx = [i for i, s in enumerate(res["side"]) if s == sd]
            if idx:
                side_stats[sd] = {
                    "n": len(idx),
                    "gross": round(sum(g[i] for i in idx) / len(idx), 3),
                    "net": round(sum(net[i] for i in idx) / len(idx), 3),
                }
        return {
            "name": name, "n": res["n"],
            "gross_mean_bp": round(sum(g) / len(g), 3) if g else None,
            "net_mean_bp": round(sum(net) / len(net), 3) if net else None,
            "mae_mean_bp": round(sum(res["mae_bp"]) / len(res["mae_bp"]), 3) if res["mae_bp"] else None,
            "exit_types": res["exit_types"],
            "side": side_stats, "blocked": res["blocked"],
            "gate_blocked": res["gate_blocked"],
        }

    def sl_fixed(sig):
        return 6.0

    def tp_fixed(sig):
        return a.tp

    def sl_vol(sig):
        return max(6.0, 1.5 * max(sig, 1.0))

    def tp_vol(sig):
        return 0.8 * sl_vol(sig)

    def sl_none(sig):
        return 1e9

    def decay_r30(ks, px, i, t, sign):
        # 30s（或 --decay-window）趋势重新朝不利方向延伸 ≥ decay_bp → 离场（H266 反转衰减）。
        j = t
        while j >= 0 and ks[t] - ks[j] < a.decay_window:
            j -= 1
        if j < 0 or px[j] <= 0:
            return None
        r30 = (px[t] - px[j]) / px[j] * 1e4
        if r30 * sign <= -a.decay:
            return True
        return None

    policies = [
        run_policy("P0 固定6/5 超时90s", sl_fixed, tp_fixed, 90.0),
        run_policy("P1 vol缩放SL/TP 超时90s", sl_vol, tp_vol, 90.0),
        run_policy("P2 时间止损 TP5 超时90s", sl_none, tp_fixed, 90.0),
        run_policy(f"P3 反转衰减 TP{a.tp:.0f} 超时{a.timeout:.0f}s",
                   sl_none, tp_fixed, a.timeout, decay_fn=decay_r30),
    ]
    if a.only_p3:
        policies = [policies[0], policies[3]]

    print(f"\n{'政策':<28} {'n':>6} {'毛均值bp':>9} {'净均值bp':>9} {'MAE均值':>8}  出口分布")
    for p in policies:
        print(f"  {p['name']:<28} {p['n']:>6} {p['gross_mean_bp']:>9} {p['net_mean_bp']:>9}"
              f" {p['mae_mean_bp']:>8}  {p['exit_types']}"
              + (f"  闸门拦截{p['gate_blocked']}" if p.get("gate_blocked") else ""))
        if p.get("side"):
            print(f"  {'':28} 多 {p['side'].get('long',{}).get('n',0):>5} 毛{p['side'].get('long',{}).get('gross',0):>7} 净{p['side'].get('long',{}).get('net',0):>7}  |"
                  f" 空 {p['side'].get('short',{}).get('n',0):>5} 毛{p['side'].get('short',{}).get('gross',0):>7} 净{p['side'].get('short',{}).get('net',0):>7}"
                  f"  | OFI拦截 多{p['blocked'].get('long',0)} 空{p['blocked'].get('short',0)}")

    OUT.write_text(json.dumps(policies, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
