# -*- coding: utf-8 -*-
"""H329 选币 v4 —— 评分口径升级为"当前策略的影子模拟净 bp/腿"。

# 为什么推翻 v3 的"反转 corr"
   v3（F345）用 reversal_corr（120s→60s，corr<−0.02 才保留）选币。
   h328 实测（主动入场口径、96h）：**r60 反转信号对所有币的毛边际都 ≈0**
   ⇒ corr<−0.02 的币几乎不存在 ⇒ v3 会把所有币判"无反转"并误选
   [1000SHIB,PENDLE,XLM,LTC]（实测重亏币），把真赚钱的 ETH/BNB 挤出槽位
   （2026-09-26 15:5x 实测发生，已回滚并禁用任务）。

# 本文件（v4）：不再用任何"信号强度"代理，直接用**与线上同参数的影子模拟**
   —— 对每个候选币跑一遍当前策略（fade_rlb 信号 + trend_only(20bp) 时段闸 +
   封逆势侧 + OFI 0.15 + 贴盘口 0.07bp 被动入场 + 衰减3/止盈30/超时120 出口，
   与 h284 的 h324/h325/h331 配置一致），得分 = 每腿净 bp。
   与线上唯一的差别：成交模型（1s mid 贴盘口 chase），这正是 h323 已对齐的口径。

# 防误伤护栏（比 v3 更严）：
   · 样本不足（n<30 腿）→ 不动（thin），不允许新币靠小样本上位；
   · 得分 < min_net_bp（默认 −1.0bp/腿）→ 摘除；
   · 每次最多换 max_new=2 个币，避免一次洗牌把测量打乱；
   · 6 小时冷却（标记文件），任务每 30 分钟跑一次也只会每 6h 实际评估一次。

# 用法
   python scripts/h329_selector_v4.py                     # 预览
   python scripts/h329_selector_v4.py --apply --lane mm_asterdex [--force]
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
MARKER = ROOT / "research_l1" / "out" / "h329_last_run.txt"
COOLDOWN_SEC = 6 * 3600

# 与线上/h284 当前配置一致（h324/h325/h331）
LOOKBACK, THR = 60.0, 2.0
OFI_CONF = 0.15
TREND_ONLY_BP, GATE_LB = 20.0, 300.0
FILL_D = 0.07
DECAY, DECAY_WIN, TP, TIMEOUT = 3.0, 30.0, 30.0, 120.0
EXCLUDE_SUFFIX = ("USD1",)


def _excluded(sym: str) -> bool:
    return any(str(sym).upper().endswith(suf) for suf in EXCLUDE_SUFFIX)


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def market_url_of(url: str) -> str:
    return url.rsplit("/", 1)[0] + "/alpha_market"


def sim_symbol(murl: str, symbol: str, hours: float) -> dict | None:
    """对单币跑当前策略的影子模拟，返回 {net_bp, gross_bp, n, mae}。"""
    tk = str(symbol).upper()
    if not tk.endswith("USDT"):
        tk += "USDT"
    try:
        with psycopg.connect(murl) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                    FROM (SELECT (event_ts_ms/1000) AS bucket, bid_px, ask_px
                          FROM asterdex_book_ticker
                          WHERE event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                            AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                    ORDER BY bucket, bid_px
                """, (hours, tk))
                recs = cur.fetchall()
    except Exception:
        return None
    d = {int(b): ((float(x) + float(y)) / 2.0) for b, x, y in recs}
    ks = sorted(d)
    px = [d[k] for k in ks]
    n = len(ks)
    if n < 500:
        return None
    bare = tk[:-4] if tk.endswith("USDT") else tk
    ofi = {}
    try:
        with psycopg.connect(murl) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT timestamp, taker_buy_notional, taker_sell_notional
                    FROM market_trades_aggregated
                    WHERE symbol=%s
                      AND timestamp >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                    ORDER BY timestamp
                """, (bare, hours))
                rows = cur.fetchall()
    except Exception:
        rows = []
    for ts_ms, bn, sn in rows:
        tot = float(bn or 0) + float(sn or 0)
        if tot > 0:
            ofi[int(ts_ms) // 15000] = (float(bn or 0) - float(sn or 0)) / tot

    res_n, gross, net, mae = 0, [], [], []
    last = -1e18
    for i in range(n):
        if ks[i] - last < 60.0:
            continue
        last = ks[i]
        j = i
        while j >= 0 and ks[i] - ks[j] < LOOKBACK:
            j -= 1
        if j < 0 or ks[i] - ks[j] < LOOKBACK * 0.9 or px[j] <= 0:
            continue
        rlb = (px[i] - px[j]) / px[j] * 1e4
        if abs(rlb) < THR:
            continue
        # OFI 衰竭确认（与线上同向语义：流与 60s 趋势同向 → 不进场）
        o = ofi.get(ks[i] // 15, 0.0)
        if (rlb > 0 and o > OFI_CONF) or (rlb < 0 and o < -OFI_CONF):
            continue
        # trend_only + 封逆势（h324/h331）：|300s 趋势| < 20 → 全停；
        # 否则只做与趋势同向的一侧
        j3 = i
        while j3 >= 0 and ks[i] - ks[j3] < GATE_LB:
            j3 -= 1
        if j3 < 0 or ks[i] - ks[j3] < GATE_LB * 0.9 or px[j3] <= 0:
            continue
        gtr = (px[i] - px[j3]) / px[j3] * 1e4
        if abs(gtr) < TREND_ONLY_BP:
            continue
        sign = -1.0 if rlb > 0 else 1.0
        with_trend = (sign > 0 and gtr > 0) or (sign < 0 and gtr < 0)
        if not with_trend:
            continue
        # 贴盘口 chase 入场（1s 网格，60s 窗口）
        tf = None
        entry_px = 0.0
        for tt in range(i + 1, min(n, i + 61)):
            lvl = px[tt - 1] * (1 - sign * FILL_D / 1e4)
            if (sign > 0 and px[tt] <= lvl) or (sign < 0 and px[tt] >= lvl):
                tf = tt
                entry_px = lvl
                break
        if tf is None:
            continue
        # 出口：衰减 3bp/30s 窗（maker 0 费）、止盈 30（taker 4bp）、超时 120（0 费）
        exit_type, exit_bp = "timeout", None
        t = tf
        while t + 1 < n and ks[t + 1] - ks[tf] <= max(TIMEOUT, 300.0):
            t += 1
            mv = (px[t] - entry_px) / entry_px * 1e4 * sign
            if mv >= TP:
                exit_type, exit_bp = "tp", mv
                break
            jd = t
            while jd >= 0 and ks[t] - ks[jd] < DECAY_WIN:
                jd -= 1
            if jd >= 0 and px[jd] > 0:
                rd = (px[t] - px[jd]) / px[jd] * 1e4
                if rd * sign <= -DECAY:
                    exit_type, exit_bp = "decay", mv
                    break
            if ks[t] - ks[tf] >= TIMEOUT:
                exit_type, exit_bp = "timeout", (px[t] - entry_px) / entry_px * 1e4 * sign
                break
        if exit_bp is None:
            exit_bp = (px[t] - entry_px) / entry_px * 1e4 * sign
        fee = 4.0 if exit_type == "tp" else 0.0
        res_n += 1
        gross.append(exit_bp)
        net.append(exit_bp - fee)
        worst = 0.0
        for tt in range(tf, t + 1):
            mv = (px[tt] - entry_px) / entry_px * 1e4 * sign
            if mv < worst:
                worst = mv
        mae.append(worst)
    if res_n < 30:
        return None
    return {"symbol": bare, "n": res_n,
            "gross_bp": round(sum(gross) / res_n, 3),
            "net_bp": round(sum(net) / res_n, 3),
            "mae_bp": round(sum(mae) / res_n, 3)}


def pool(murl: str, *, hours: int = 4) -> list:
    """候选池：2h 盘口有更新 + 有深度快照的币（去掉 USD1）。"""
    rows = []
    with psycopg.connect(murl) as mc:
        with mc.cursor() as cur:
            cur.execute("""
                SELECT symbol, count(*) AS book_n,
                       percentile_disc(0.25) WITHIN GROUP (
                           ORDER BY (ask_px-bid_px)/NULLIF((ask_px+bid_px)/2,0)*1e4) AS spread_p25
                FROM asterdex_book_ticker
                WHERE event_ts_ms >= (extract(epoch from now())-%s*3600)*1000
                  AND ask_px > bid_px AND bid_px > 0
                GROUP BY symbol HAVING count(*) > 500
            """, (hours,))
            for sym, n, p25 in cur.fetchall():
                rows.append({"symbol": str(sym).replace("USDT", ""), "book_n": int(n),
                             "spread_p25_bp": float(p25 or 0.0)})
    return [r for r in rows if not _excluded(r["symbol"])]


def current_universe(url: str) -> list:
    try:
        with psycopg.connect(url) as c:
            with c.cursor() as cur:
                cur.execute("SELECT meta_json->'symbols' FROM lane_registry WHERE lane_id=%s",
                            ("mm_asterdex",))
                r = cur.fetchone()
        return [str(s) for s in (r[0] or [])] if r else []
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default="")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true", help="忽略 6h 冷却")
    ap.add_argument("--slots", type=int, default=4)
    ap.add_argument("--min-net-bp", type=float, default=-1.0,
                    help="影子模拟每腿净 bp 低于此值 → 摘除")
    ap.add_argument("--max-new", type=int, default=2)
    ap.add_argument("--hours", type=float, default=48.0, help="影子模拟窗口（小时）")
    a = ap.parse_args()

    if not a.force and MARKER.exists():
        age = time.time() - MARKER.stat().st_mtime
        if age < COOLDOWN_SEC:
            print(f"H329 冷却中（上次评估 {age/3600:.1f}h 前），跳过。--force 可强制。")
            return 0

    url = dsn()
    murl = market_url_of(url)
    cur_syms = current_universe(url)
    cands = pool(murl)
    print("=" * 96)
    print("H329 选币 v4 —— 评分 = 当前策略影子模拟每腿净 bp"
          f"（{a.hours:g}h，trend_only20 + 封逆势 + OFI0.15 + 贴盘口0.07bp + "
          f"衰减{DECAY:g}/止盈{TP:g}/超时{TIMEOUT:g}）")
    print("=" * 96)
    syms = sorted(set(cur_syms + [c["symbol"] for c in cands]))
    print(f"  评估 {len(syms)} 个币 …")
    results = {}
    for s in syms:
        r = sim_symbol(murl, s, a.hours)
        if r is not None:
            results[r["symbol"]] = r
    ranked = sorted(results.items(), key=lambda kv: -kv[1]["net_bp"])
    print(f"\n  {'币':<10} {'n':>5} {'毛bp':>8} {'净bp':>8} {'MAE':>8}  判定")
    keep, remove, thin = [], [], []
    for s, r in ranked:
        cur = " ←在营" if s in cur_syms else ""
        if r["n"] < 30:
            thin.append(s)
            tag = "样本不足"
        elif r["net_bp"] < a.min_net_bp:
            remove.append((s, f"影子净 {r['net_bp']:+.3f}bp/腿 < {a.min_net_bp}"))
            tag = "摘除"
        else:
            keep.append((s, r["net_bp"]))
            tag = "保留"
        print(f"  {s:<10} {r['n']:>5} {r['gross_bp']:>+8.3f} {r['net_bp']:>+8.3f}"
              f" {r['mae_bp']:>8.2f}  {tag}{cur}")

    uni = [s for s, _ in keep[:a.slots]]
    # 全部低于门槛时：仍取相对最好的（不低于硬地板），避免宇宙被清空瘫痪
    if not uni and ranked:
        hard = min(float(a.min_net_bp), -3.0)
        soft = [(s, r["net_bp"]) for s, r in ranked
                if r["n"] >= 30 and r["net_bp"] >= hard]
        uni = [s for s, _ in soft[:a.slots]]
        if uni:
            print(f"\n  ! 无人过线，启用相对最优（硬地板 {hard}bp）：{uni}")
    news = [s for s in uni if s not in cur_syms]
    # 每次最多换 max_new 个新币：超出则用当前宇宙里的保留币回填
    if len(news) > a.max_new:
        kept_cur = [s for s in uni if s in cur_syms]
        news = news[:a.max_new]
        uni = kept_cur + news
        uni = uni[:a.slots]
    dropped = [s for s, _ in keep[len(uni):]]
    if not uni:
        print("\n  ✗ 没有可保留的币（全部低于门槛）→ 不写库，维持现状。")
        return 2

    print(f"\n  ── 摘除 ──")
    for s, why in remove:
        print(f"    {s:<10} {why}")
    if not remove:
        print("    (无)")
    print(f"\n  => 新宇宙（{len(uni)} 槽）：**{uni}**  新进 {news}  丢弃 {dropped}  样本不足不动 {thin}")

    if not (a.apply and a.lane):
        print("\n  （预览）加 --apply --lane mm_asterdex 才写库")
        MARKER.write_text(datetime.now().isoformat(), encoding="utf-8")
        return 0

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (a.lane,))
            row = cur.fetchone()
            if not row:
                print(f"  错误：车道 {a.lane} 不存在")
                return 1
            meta = dict(row[0] or {})
            before = list(meta.get("symbols") or [])
            meta["symbols"] = uni
            meta["h329_rollback"] = {"symbols": before, "by": "h329_selector_v4"}
            meta["universe"] = {
                "fixed": [],
                "ai": uni,
                "note": "H329 v4：评分＝当前策略影子模拟每腿净bp（trend_only/封逆势/OFI/贴盘口，"
                        "与线上同参数）；样本<30不动；<−1.0bp摘除；每次最多换2个。",
                "as_of": datetime.now().astimezone().isoformat(),
                "net_bp_per_cycle": {s: results.get(s, {}).get("net_bp") for s in uni},
            }
            ops = list(meta.get("ops_changes") or [])
            ops.append({"by": "h329_selector_v4", "op": "set_symbols",
                        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "before": before, "after": uni,
                        "reason": "评分口径：反转corr(全币≈0) → 影子模拟净bp/腿；"
                                  "护栏：n<30不动、<−1.0bp摘除、max_new=2"})
            meta["ops_changes"] = ops[-40:]
            cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now()"
                        " WHERE lane_id=%s",
                        (json.dumps(meta, ensure_ascii=False, default=str), a.lane))
        conn.commit()
    MARKER.write_text(datetime.now().isoformat(), encoding="utf-8")
    print(f"\n  => 已写入 {a.lane}：{before} → {uni}")
    return 0


if __name__ == "__main__":
    # Windows 计划任务默认 GBK，箭头等字符会把整轮评估弄崩、结果写不进库
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
