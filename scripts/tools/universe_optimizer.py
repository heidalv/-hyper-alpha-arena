# -*- coding: utf-8 -*-
r"""[h897 2026-10-07] 宇宙优化器 —— 让币宇宙自动向「此刻活跃 + 能赚钱」的币集中。

用户痛点:「宇宙这么多候选,怎么还是对着 BTC/ETH 两个交易对?」
实测根因(2026-10-07 审计):39 币宇宙里 ~15 个此刻没有实时逐笔流
(ARB/FIL/SKY/VELVET/SOLUSD1 等 30 分钟 0 成交)⇒ OFI 方向算不出、
挂单也等不到穿越 ⇒ 它们死在宇宙里,交易自然集中在有流的 BTC/ETH/QNT/PLAY。

宇宙筛选原来用「24h 成交额」(粗粒度、滞后)——高频 maker 策略要的是
「**此刻的逐笔成交率**」(细粒度、实时)。本优化器每 15 分钟重选宇宙:

  入选规则(全部满足):
    · 实时流:近 30 分钟逐笔 ≥ FLOW_MIN(默认 8 笔)——够算 OFI + 够成交
    · 可交易价差:0.3bp ≤ spread ≤ 25bp(太窄没肉、太宽是跳空币)
    · 非灾难:成绩单 72h avg < −30bp 且 n≥5 的币排除(SI 那种单腿灾难)
    · 非死后缀:USD1/USDC 等(永续里这些是死代码)
  排序:实时逐笔率 × 成绩单加权(赢家优先),取 top N(默认 40)。
  驻留保护:有持仓的币不移除(防丢仓,orphan 逻辑会强平)。

写 lane meta.symbols(worker 60s 热采用,无需重启)。
用法:
  .venv\Scripts\python.exe scripts\tools\universe_optimizer.py            # dry-run
  .venv\Scripts\python.exe scripts\tools\universe_optimizer.py --apply    # 真写
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

LANE = "mm_asterdex"
AUDIT = ROOT / "data" / "universe_optimizer.json"
LOG = ROOT / "logs" / "universe_optimizer.log"

FLOW_MIN = 8            # 近30分钟逐笔下限(有流才算活币)
SPREAD_MAX_BP = 50.0    # 价差上限(只排"盘口坏掉"的;**不设下限**——
                        # 快进快出策略里价差越窄越好:突破价差即可盈利,
                        # BTC/ETH 0.01bp 窄价差 + 每秒成交流 正是最佳 scalp 标的)
TOP_N = 40              # 宇宙上限
DEAD_SUFFIX = ("USD1", "USDC", "BUSD", "DAI", "FDUSD", "TUSD")
SCORECARD = ROOT / "data" / "symbol_scorecard.json"


def log(msg: str) -> None:
    line = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " [universe] " + msg
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _bare(symbol: str) -> str:
    s = str(symbol or "").upper()
    return s[:-4] if s.endswith("USDT") else s


def _losing_now(bare: str, sc: dict, tail_ban: set) -> str | None:
    """正在亏的币。返回原因；没有问题则返回 None，可以留下或新进。"""
    if bare in tail_ban:
        return "tail_48h"
    rule = str(sc.get("rule") or "")
    if rule in ("persistent_loser", "mild_loser", "disaster_probe"):
        return rule
    n24 = int(sc.get("n24") or 0)
    a24 = float(sc.get("avg24_bp") or 0.0)
    if n24 >= 8 and a24 < -2.0:
        return f"bleed_24h={a24:.1f}bp"
    n72 = int(sc.get("n72") or 0)
    a72 = float(sc.get("avg72_bp") or 0.0)
    if n72 >= 12 and a72 < -5.0 and a24 <= 0.0:
        return f"bleed_72h={a72:.1f}bp"
    return None


def _read_entry_times(meta: dict) -> dict:
    """从 meta.ops_changes 的 set_symbols 历史推出每币入槽时间(币龄)。

    [v5 币龄曲线证据 h708] 0-30min 是最差冷启动档、2-6h 是最好档 ⇒
    换币太快会把币停在最差档。币龄 < MIN_TENURE_SEC 的币非衰减不换出。
    """
    entry: dict = {}
    for op in sorted((meta.get("ops_changes") or []), key=lambda x: x.get("ts") or ""):
        if op.get("op") not in ("set_symbols", "universe_optimizer"):
            continue
        after = op.get("after") or op.get("symbols") or []
        try:
            t = datetime.datetime.fromisoformat(
                str(op.get("ts")).replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        for s in after:
            entry.setdefault(str(s).upper(), t)
    return entry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--flow-min", type=int, default=FLOW_MIN)
    ap.add_argument("--top", type=int, default=TOP_N)
    args = ap.parse_args()

    now_ms = int(time.time() * 1000)
    from backend.services.market_maker.attribution import _market_dsn
    import psycopg

    # 1) 候选池:近 1h 有盘口数据的币 + 实时逐笔率 + 当前价差
    #    [2026-10-08 重设计] 同时取「订单流 OFI」和「10分钟净位移/区间」——
    #    趋势强度分 TrendScore 的原料。资金(OFI)是领先信号,价格是滞后信号。
    cand = {}
    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT symbol, COUNT(*) FROM asterdex_trades"
            " WHERE event_ts_ms > %s GROUP BY symbol", (now_ms - 1800_000,))
        flow = {r[0]: int(r[1]) for r in cur.fetchall()}
        # 订单流:三个窗口。60s/20s 喂趋势概率模型;5min 做排序/门槛参考。
        def _ofi_win(win_ms):
            cur.execute(
                "SELECT symbol,"
                " COALESCE(SUM(qty) FILTER (WHERE is_buyer_maker IS FALSE),0),"
                " COALESCE(SUM(qty) FILTER (WHERE is_buyer_maker IS TRUE),0)"
                " FROM asterdex_trades WHERE event_ts_ms > %s GROUP BY symbol",
                (now_ms - win_ms,))
            out = {}
            for sym, bv, sv in cur.fetchall():
                bv, sv = float(bv), float(sv)
                tot = bv + sv
                out[sym] = {"ofi": ((bv - sv) / tot) if tot > 0 else 0.0, "vol": tot}
            return out
        ofi60_map = _ofi_win(60_000)
        ofi20_map = _ofi_win(20_000)
        ofi_map = {s: v["ofi"] for s, v in _ofi_win(300_000).items()}
        # 当前价差(最新一档)
        cur.execute(
            "SELECT DISTINCT ON (symbol) symbol, bid_px, ask_px"
            " FROM asterdex_book_ticker WHERE event_ts_ms > %s AND bid_px>0"
            " ORDER BY symbol, event_ts_ms DESC", (now_ms - 3600_000,))
        for sym, bid, ask in cur.fetchall():
            bid, ask = float(bid), float(ask)
            if bid > 0 and ask > bid:
                cand[sym] = {"spread_bp": (ask - bid) / bid * 1e4,
                             "flow": flow.get(sym, 0),
                             "ofi": ofi_map.get(sym)}
        # 10分钟净位移/区间(趋势干净度) + 完整 book 行(算趋势概率 8 特征)
        from backend.services.market_maker.trend_features import compute_features
        from backend.services.market_maker.trend_prob import predict_side_edge
        for sym in list(cand.keys()):
            cur.execute(
                "SELECT event_ts_ms/1000.0, bid_px, ask_px, bid_qty, ask_qty"
                " FROM asterdex_book_ticker"
                " WHERE symbol=%s AND event_ts_ms > %s AND bid_px>0"
                " ORDER BY event_ts_ms",
                (sym, now_ms - 600_000))
            rows = [(float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]))
                    for r in cur.fetchall() if r and r[1] and r[2]]
            if len(rows) >= 2 and rows[0][1] > 0:
                mids = [(r[1] + r[2]) / 2.0 for r in rows]
                m0 = rows[0][1] and (rows[0][1] + rows[0][2]) / 2.0
                net = (mids[-1] - mids[0]) / mids[0] * 1e4
                rng = (max(mids) - min(mids)) / mids[0] * 1e4
                cand[sym]["net_bp"] = net
                cand[sym]["range_bp"] = rng
                # [2026-10-08 选B] 喂饱趋势概率 10 特征 → 拿方向+edge → 那 30 分活了
                feats = compute_features(
                    rows,
                    (ofi60_map.get(sym) or {}).get("ofi"),
                    (ofi20_map.get(sym) or {}).get("ofi"),
                    (ofi60_map.get(sym) or {}).get("vol"),
                )
                try:
                    pe = predict_side_edge(str(ROOT), feats)
                except Exception:
                    pe = None
                if pe:
                    cand[sym]["prob_side"] = pe[0]
                    cand[sym]["prob_edge"] = pe[1]
            else:
                cand[sym]["net_bp"] = None
                cand[sym]["range_bp"] = None

    # 2) 成绩单(72h edge)
    score = {}
    if SCORECARD.exists():
        try:
            score = {r["symbol"]: r for r in
                     json.loads(SCORECARD.read_text(encoding="utf-8")).get("report", [])}
        except Exception:
            score = {}

    # 2.5) [h900 根因修复·选币环] 跳空币名单 + 24h 振幅(fail-closed)
    # 实锤:灾难 −100~−422bp 全集中在 gap-prone 币;且振幅算不出的币(SI 0.0%)
    # 被旧筛选误判为"安全"。⇒ ① gap_excluded 直接排除;② 振幅 >10% 排除
    # (牛来 11.7%/PONS 12.5% 也崩过);③ 振幅算不出 ⇒ 视为危险排除(fail-closed)。
    gap_excluded: set = set()
    amp_map: dict = {}
    try:
        _vt = json.loads((ROOT / "data" / "vol_top20.json").read_text(encoding="utf-8"))
        gap_excluded = {str(x).upper() for x in (_vt.get("gap_excluded") or [])}
        amp_map = {str(d.get("symbol")).upper(): float(d.get("range_pct_24h") or 0.0)
                   for d in (_vt.get("detail") or [])}
    except Exception:
        gap_excluded, amp_map = set(), {}
    GAP_AMP_MAX = 18.0   # 24h 振幅 >18% 即排除(极端摆动);
                         # 10~18% 的币不排除(会误杀太多),它们的崩盘风险
                         # 由「成绩单降权 + 出场环快速止损」兜底,不靠振幅挡

    # 3) 当前宇宙 + 持仓(驻留保护) + 24h 灾难尾
    # 昨天实锤:USELESS 一笔 -175 美元、SI 一笔 -386bp。72h 均价门槛太慢,
    # 这种单腿已经滑穿的币必须立刻移出,不能再等成绩单平均掉到 -30bp。
    # 规则:近 48 小时净亏 ≤ -20 美元,或最差一笔 ≤ -150bp 且这段仍是亏的。
    # 用 48 小时而不是 24 小时:凌晨的灾难单到第二天上午会掉出 24 小时窗口。
    # 全天净赚的币不因中间一笔回撤被赶走(例如 TRUMP)。
    from sqlalchemy import text
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    tail_ban: set = set()
    with system_identity():
        with SessionLocal() as db:
            try:
                tail_rows = db.execute(text(
                    "SELECT symbol, SUM(net_bp*notional)/10000.0, MIN(net_bp) "
                    "FROM lane_ledger WHERE lane_id=:l AND event='fill' "
                    "AND ts > NOW() - INTERVAL '48 hours' GROUP BY symbol"
                ), {"l": LANE}).fetchall()
                for sym, pnl, worst in tail_rows:
                    pnl_f = float(pnl or 0.0)
                    worst_f = float(worst or 0.0)
                    # -20 美元约等于本金的 7%。再低会把 ETH 这种「很多小亏、没有单笔滑穿」的主流币赶走。
                    # -150bp 且仍在亏:抓住 PLAY 那种单笔滑穿、但总金额还没到 -20 的币。
                    # 净赚的币保留(PUMP/TRUMP 有过很差的一笔,但两天加总是赚的)。
                    if pnl_f <= -20.0 or (worst_f <= -150.0 and pnl_f < 0.0):
                        tail_ban.add(_bare(sym))
            except Exception as e:
                log(f"24h 灾难尾查询失败,本轮不额外禁币: {e}")
            lane = db.execute(text(
                "SELECT meta_json FROM lane_registry WHERE lane_id=:l"),
                {"l": LANE}).fetchone()
            meta = json.loads(lane[0]) if isinstance(lane[0], str) else dict(lane[0] or {})
            current = [str(s) for s in (meta.get("symbols") or [])]
            held = set()
            try:
                rows = db.execute(text(
                    "SELECT symbol FROM lane_runtime_state"
                    " WHERE lane_id=:l AND ABS(qty) > 1e-9"), {"l": LANE}).fetchall()
                held = {_bare(r[0]) for r in rows}
            except Exception:
                held = set()

    # 4) 逐币判定
    selected = []
    dropped = []
    for sym, d in cand.items():
        bare = _bare(sym)
        if bare.endswith(DEAD_SUFFIX):
            continue
        sc = score.get(bare) or {}
        # [h900 选币环] 跳空币排除(最高优先级,在灾难排除之前)
        if bare in gap_excluded:
            dropped.append((bare, "gap_excluded"))
            continue
        if bare in tail_ban:
            dropped.append((bare, "tail_24h"))
            continue
        _amp = amp_map.get(bare)
        # [2026-10-08 修] 振幅算不出不再一律排除(误杀 19 币,名单缩到 1 个)。
        # 数据缺失 ≠ 危险:交给「流动性门槛 + 趋势分 + 灾难尾」去判断。
        # 只有振幅**确实算出来且 >GAP_AMP_MAX** 才排除(真高波动)。
        if _amp is not None and _amp > GAP_AMP_MAX:
            dropped.append((bare, f"amp={_amp:.0f}%"))
            continue
        # 灾难排除
        if sc.get("n72", 0) >= 5 and sc.get("avg72_bp", 0.0) < -30.0:
            dropped.append((bare, "disaster"))
            continue
        if d["flow"] < args.flow_min:
            dropped.append((bare, f"flow={d['flow']}"))
            continue
        if d["spread_bp"] > SPREAD_MAX_BP:      # 只排盘口坏掉的(>50bp)
            dropped.append((bare, f"spread={d['spread_bp']:.1f}"))
            continue
        why = _losing_now(bare, sc, tail_ban)
        if why:
            dropped.append((bare, why))
            continue
        # [2026-10-08 重设计] 排序键 = 趋势强度分 TrendScore（实时订单流+趋势概率+干净度）。
        # 流动性只是门槛（上面 flow/spread 已卡），历史盈亏只踢灾难（上面 tail_ban/disaster）。
        from backend.services.market_maker.trend_score import (
            liquidity_ok, trend_score, SCORE_ENTER)
        if not liquidity_ok(d["flow"], d["spread_bp"]):
            dropped.append((bare, "illiquid"))
            continue
        ts = trend_score(d.get("ofi"), d.get("net_bp"), d.get("range_bp"),
                         d.get("prob_side"), d.get("prob_edge"))
        if ts["score"] < SCORE_ENTER:
            dropped.append((bare, f"weak_trend={ts['score']}"))
            continue
        # [2026-10-08 用户规则] 排序键 = 趋势分 × log(量)。量小趋势再强也排后——
        # 挂单没人吃等于白挂(MARSCOIN 3笔/ADA 7笔 就是这么卡死的)。
        # 量大的主流币(ETH 261笔)即使趋势分中等,也排在量小币前面。
        import math as _math
        rank = ts["score"] * _math.log1p(max(0, d["flow"]))
        selected.append((bare, rank, d["flow"], d["spread_bp"]))

    # 驻留保护:有持仓的币强制保留
    for h in held:
        if h not in [s[0] for s in selected] and h in [_bare(c) for c in cand]:
            selected.append((h, 1e9, 0, 0.0))   # 排最前,保仓

    # [2026-10-08 重设计] 按趋势强度分排序（x[1]=TrendScore），取前 6 槽。
    selected.sort(key=lambda x: (-x[1], -x[2]))
    slot_n = min(int(args.top), 6)
    ideal = [s[0] for s in selected[:slot_n]]

    # ── [v5 换币纪律移植] 衰减立刻换 / 币龄保护 / 每次最多换 MAX_NEW ──
    # 设计来源 h329_selector_v5(用户定的原则:市场是流动的,衰减即换,但不抖动):
    #   · 衰减(持续亏损/灾难)→ 立即换出,不吃驻留;
    #   · 币龄 < MIN_TENURE_SEC(1h)且不在衰减 ⇒ 保留(给足冷启动时间);
    #   · 非衰减的常规轮换每次最多新进 MAX_NEW 个(防一次大换血)。
    MIN_TENURE_SEC = 3600
    MAX_NEW = 2
    now_ts = time.time()
    entry_times = _read_entry_times(meta)

    def _is_decaying(bare_sym: str) -> bool:
        # 成绩单已判输、近 24h 明显在亏、或 48h 灾难尾：立刻换出，不等「够惨」。
        return _losing_now(bare_sym, score.get(bare_sym) or {}, tail_ban) is not None

    ideal_set = set(ideal)
    # ① 衰减币:立即换出(即使在理想名单里,若是衰减币也不留——除非它有流且非灾难)
    decaying_now = {s for s in current if _is_decaying(s)}
    # ② 币龄保护:在槽 <1h 且非衰减 ⇒ 保留。
    #    [2026-10-08 修] 但死代码(USD1 后缀)和没流量的币不保护——
    #    它们是上一轮 33 币大名单残留的关系户,留着只会挂不出单。
    tenure_kept = []
    flow_now = {s[0]: s[2] for s in selected}   # 趋势够格的币的实时流量
    for s in current:
        bare = s.upper()
        if bare in decaying_now:
            continue
        if bare.endswith(DEAD_SUFFIX):
            continue   # 死代码不保护
        tenure = now_ts - entry_times.get(bare, 0.0)
        in_ideal = bare in ideal_set
        # 只保护「此刻还有流量」的币;流量没了(不在 selected)就不保护
        if not in_ideal and 0 < tenure < MIN_TENURE_SEC and bare in flow_now:
            tenure_kept.append(bare)
    # ③ 组装:理想名单 + 币龄保护,再裁掉衰减币
    new_universe = [s for s in ideal if s not in decaying_now]
    for s in tenure_kept:
        if s not in new_universe:
            new_universe.append(s)
    # ④ 每次最多新进 MAX_NEW 个(按排序分取前 MAX_NEW)。
    #    例外:新进的是来**顶替衰减币**的,不受 MAX_NEW 限制——
    #    否则一次换出 4 个衰减币、只能进 2 个,名单会缩到 2 个。
    order = {s[0]: s[1] for s in selected}
    new_candidates = [s for s in new_universe if s not in current]
    if len(new_candidates) > MAX_NEW:
        n_decay_out = len([s for s in current
                           if s in decaying_now and s not in new_universe])
        cap = max(MAX_NEW, n_decay_out)
        new_candidates.sort(key=lambda s: -order.get(s, 0.0))
        allowed_new = set(new_candidates[:cap])
        new_universe = [s for s in new_universe
                        if (s in current) or (s in allowed_new) or (s in tenure_kept)]
    # 驻留保护的持仓币即使超 cap 也保留
    for h in held:
        if h not in new_universe and h in [_bare(c) for c in cand]:
            new_universe.append(h)

    added = [s for s in new_universe if s not in current]
    removed = [s for s in current if s not in new_universe]
    log(f"当前宇宙 {len(current)} 币 → 新宇宙 {len(new_universe)} 币")
    log(f"  新进({len(added)}): {added}")
    log(f"  移出({len(removed)}): {removed}")
    log(f"  衰减立刻换出: {sorted(decaying_now) if decaying_now else '无'}")
    log(f"  币龄保护(<1h 不换): {tenure_kept if tenure_kept else '无'}")
    log(f"  持仓保护: {sorted(held) if held else '无'}")
    log(f"  新宇宙(按 流×edge 排序): {new_universe}")

    # [h898 单一写者] 本优化器只做"侦察兵":算出理想宇宙写进 data/universe_optimizer.json。
    # **不再直接写 meta.symbols** —— meta.symbols 由 worker 的 _refresh_flow_universe
    # 读这份 json 后统一写(含 orphan 强平/驻留/持仓保护),避免双生产者互写打架。
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT.write_text(json.dumps({
        "ts": datetime.datetime.now().isoformat(),
        "ts_epoch": time.time(),
        "applied_via": "worker_radar",   # meta.symbols 由 worker 雷达统一写
        "before": current, "after": new_universe, "added": added,
        "removed": removed, "held": sorted(held),
        "decaying_out": sorted(decaying_now),
        "tenure_kept": tenure_kept,
        "dropped_reasons": dropped,
        "selected_detail": [{"symbol": s, "score": round(sc, 1), "flow": fl,
                             "spread_bp": round(sp, 2)}
                            for s, sc, fl, sp in selected[: args.top]],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"已写 scout 文件 data/universe_optimizer.json({len(new_universe)} 币);"
        f"worker 雷达下次刷新(≤5min)采用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
