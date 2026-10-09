# -*- coding: utf-8 -*-
"""[h652 2026-09-30] 宇宙替换分析 v5 —— 滚动质量雷达(设计:研究结论/宇宙替换分析v5设计_20260930.md)。

v5.1 原则(用户指令):市场是流动的——任务=及时发现当前时段优质交易对,衰减后及时
轮换,不设任何永久禁令。静态名单一律废除,全部滚动窗口动态打分。

分层:
  L0 试跑感知冻结:lane meta.trial_active=true ⇒ 本周期冻结宇宙(治理互锁,§9.4);
  L1 滚动可行性:近 hours 小时 rel_spread ≥ SPREAD_MIN_BP 的 15s 桶占比 + 桶数门槛;
  L2 形态×币匹配:滚动 vr60(<1.3 反转族;≥1.3 仅动量族),缺数据回退种子榜;
  L3 事件研究分榜:data/coin_leaderboards_v1.json(168h 口径种子;滚动重算待批处理
    任务,TODO 见文件尾);
  L4 微试跑仲裁:新进币/历史差评回归币标记 universe_trial,预注册 12h 试跑;
  L5 槽位:反转槽 + 动量槽分族;每次最多换 max_new;6h 冷却;apply 写 symbols +
     pattern_matrix(h405 已接线,选币即配闸)+ h329_rollback + ops_changes。

用法:
  python scripts/h329_selector_v5.py                     # 预览(不写库)
  python scripts/h329_selector_v5.py --apply --lane mm_asterdex
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEED = ROOT / "data" / "coin_leaderboards_v1.json"
MARKER = ROOT / "research_l1" / "out" / "h329v5_last_run.txt"
# [h680 设计回归] v5 是"**持续运行**的质量雷达"(设计 §0/§3 L5),不是 6h 定时任务:
#   · 评估节奏 = 计划任务每 30 分钟一次(近实时发现);
#   · **衰减驱动**的替换立即执行(不吃最短驻留);
#   · 仅"择优替换"(没衰减但榜上有更强的)受最短驻留约束 ⇒ 防抖动、防刷试跑窗口。
COOLDOWN_SEC = 2 * 3600      # 兼容旧名(不再作为全局冷却)
DWELL_SEC = 2 * 3600         # 非衰减替换的最短驻留
# [h696] "明显更优"提前替换阈值:新候选 edge 比在槽最弱币高出此值(bp)
# ⇒ 不必等满驻留(防抖动的本意是防"换汤不换药",不是防"明显更好的机会")。
# [h708] "明显更优"提前替换阈值 1.5 → 3.0:实测 1.5bp 导致每 5-15 分钟换一次币
# (所有币停在最差冷启动档);3.0 只允许真正明显的优势提前替换,常规仍走 2h 驻留。
CLEAR_BETTER_BP = 3.0
# [h708 2026-10-02 币龄曲线] **最短驻留**:非亏损驱动的换币,币入槽不足
# MIN_TENURE_SEC 不得换出。实测 10-02 币龄曲线:0-30min +1.21bp / 30-120min
# +2.99bp / 2-6h +6.57bp ⇒ 每腿净随币龄上升,而雷达此前每 5-15 分钟换一次
# (CLEAR_BETTER_BP=1.5 太易触发)⇒ 所有币都停在最差的冷启动档(新币状态窗口
# 重填、闸门保护缺失)。给币至少 1h 驻留,别在收益峰值前换掉它。
MIN_TENURE_SEC = 3600
# [h728 2026-10-02 毒性 KPI 根因] 全价差下限 0.5 → 5.0bp(半价差 ≥2.5bp)。
# 病根:compute_quote 的"不穿越钳制"把报价限在 [best_bid, best_ask] 内 ⇒
# **捕获硬上限 = 该币半价差**。半价差 <2.5bp 的币无法支撑 EV 引擎的
# δ*=2.5bp(捕获 1.05bp 进 c 档)。
# [h731 22:5x 修正] 首版设 3.0(半 1.5)太松:实测 LINK(半 1.75)过了关但捕获
# 仍被钳在 0.56bp ⇒ 概率引擎无币可用(用户反馈"概率没什么表现")。
# 门槛必须 ≥ δ*:半价差 ≥2.5bp ⇒ 全价差 ≥5.0bp。回滚 = 3.0。
SPREAD_MIN_BP = 5.0          # 全价差下限;半价差 ≥2.5bp 才可入池(见 h728/h731)
SPREAD_OK_SHARE_MIN = 0.4    # 窗口内价差达标桶占比下限
MIN_BUCKETS = 60             # 近 hours 小时至少 60 个 15s 桶(≈15 分钟数据)
ROLL_HOURS = 2.0             # L1 滚动窗口(小时)
VR60_REV_MAX = 1.3           # h367:vr60 < 此值才有反转族资格
NEG_SEED_BAR = 1.0           # 历史差评币回归:max edge ≥ 此值且过 L4 微试跑
EXCLUDE_SUFFIX = ("USD1",)
# [h695 2026-10-01] **流量双边度门槛**:单边毒性行情里,趋势闸封买、流毒性闸封卖
# ⇒ 两个主闸封相反两边 ⇒ 双边都挂不出去 ⇒ 该币无成交(实测 20:20 后 11 分钟 0 成交,
# 最近 75s 拦截:trend_down +115 / ofi_toxic +60)。
# 选币器原来只看"价差可行性",不看"流量是否双边" ⇒ 加 flow_osi 判据:
#   flow_osi = 近 30 分钟逐分钟 |OFI| 的均值(0=完全双边,1=完全单边)。
FLOW_OSI_MAX = 0.85          # 均值单边度 ≥ 此值 ⇒ 本轮不入围(非禁入)
FLOW_OSI_MIN_BUCKETS = 10    # 至少 10 个有效分钟桶才判定;不足 ⇒ 不定罪


def _excluded(sym: str) -> bool:
    return any(str(sym).upper().endswith(suf) for suf in EXCLUDE_SUFFIX)


def _dsn() -> str:
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


def _market_dsn() -> str:
    return _dsn().rsplit("/", 1)[0] + "/alpha_market"


def flow_osi(sym: str, minutes: int = 30) -> Optional[float]:
    """[h695] 流量单边度 = 近 N 分钟逐分钟 |OFI| 均值(0 双边 … 1 单边)。

    数据源用**原始逐笔** `asterdex_trades`(带 is_buyer_maker;聚合表实测漏数据,
    见执行记录 h689)。有效分钟桶不足 ⇒ None(不定罪,避免误杀)。
    """
    try:
        import psycopg
        with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
            cur.execute(
                "SELECT (event_ts_ms/60000) b,"
                " COALESCE(SUM(qty) FILTER (WHERE is_buyer_maker IS FALSE),0),"
                " COALESCE(SUM(qty) FILTER (WHERE is_buyer_maker IS TRUE),0)"
                " FROM asterdex_trades"
                " WHERE symbol = %s AND event_ts_ms > %s"
                " GROUP BY 1 ORDER BY 1",
                (f"{sym}USDT", int(time.time() * 1000) - minutes * 60_000))
            vals = []
            for _b, bv, sv in cur.fetchall():
                bv, sv = float(bv or 0.0), float(sv or 0.0)
                if bv + sv > 0:
                    vals.append(abs(bv - sv) / (bv + sv))
        if len(vals) < FLOW_OSI_MIN_BUCKETS:
            return None
        return sum(vals) / len(vals)
    except Exception:
        return None


# ── 纯函数(可单测) ─────────────────────────────────────────────

def spread_ok_share(rel_spreads: Sequence[float], thr: float = SPREAD_MIN_BP) -> float:
    """滚动窗口内 rel_spread ≥ thr 的占比;空窗口返回 0。"""
    n = len(rel_spreads)
    if n == 0:
        return 0.0
    return sum(1 for x in rel_spreads if float(x) >= thr) / n


def vr60_from_mids(mids_15s: Sequence[float]) -> Optional[float]:
    """60s 方差比近似:VR = Var(r60)/(4×Var(r15)),非重叠 60s 桶。

    < 8 个 60s 桶(≈2 分钟)返回 None(样本不足,回退种子榜)。"""
    mids = [float(x) for x in mids_15s if x and float(x) > 0]
    if len(mids) < 9:
        return None
    r15 = [(mids[i + 1] - mids[i]) / mids[i] for i in range(len(mids) - 1)]
    n60 = len(r15) // 4
    if n60 < 4:
        return None
    r60 = []
    for k in range(n60):
        base = mids[4 * k]
        end = mids[4 * k + 4]
        if base > 0:
            r60.append((end - base) / base)
    if len(r60) < 4:
        return None
    def _var(xs: List[float]) -> float:
        if len(xs) < 2:
            return 0.0
        m = sum(xs) / len(xs)
        return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    v1 = _var(r15)
    v60 = _var(r60)
    if v1 <= 0:
        return None
    return v60 / (4.0 * v1)


def family(vr60: Optional[float]) -> str:
    """'reversal' / 'momentum' / 'unknown'。h367:反转族限 vr60<1.3。"""
    if vr60 is None:
        return "unknown"
    return "reversal" if float(vr60) < VR60_REV_MAX else "momentum"


def eligible_patterns(fam: str, seed: Dict[str, Any], sym: str) -> List[str]:
    """该币当前可用的形态维度:p1(反转族资格)+ p5/p4(不限)。"""
    out = []
    if fam == "reversal" and sym in (seed.get("p1") or {}):
        out.append("p1")
    if sym in (seed.get("p5") or {}):
        out.append("p5")
    if sym in (seed.get("p4") or {}):
        out.append("p4")
    return out


def score_coin(seed: Dict[str, Any], sym: str, vr60: Optional[float]) -> Dict[str, Any]:
    """返回 {symbol, vr60, family, patterns, max_edge, neg_seed}。max_edge=None=不可评。"""
    fam = family(vr60)
    pats = eligible_patterns(fam, seed, sym)
    edges = []
    for p in pats:
        e = (seed.get(p) or {}).get(sym) or {}
        if isinstance(e, dict) and e.get("edge") is not None:
            edges.append((p, float(e["edge"]), e.get("t")))
    neg = sym in (seed.get("negative_seed") or [])
    return {
        "symbol": sym, "vr60": vr60, "family": fam, "patterns": [p for p, _, _ in edges],
        "max_edge": max((e for _, e, _ in edges), default=None),
        "best_pattern": max(edges, key=lambda x: x[1])[0] if edges else None,
        "neg_seed": neg,
    }


def choose_slots(scored: Sequence[Dict[str, Any]], *, slots: int, rev_slots: int,
                 max_new: int, current: Sequence[str]) -> Tuple[List[str], List[str]]:
    """按"反转槽 + 动量槽"分族选槽。

    scored 已通过 L1/L4 门槛。反转槽优先取 max_edge 最高的币(任一形态),
    动量槽取 p5/p4 edge 最高的币。返回 (新宇宙, 需微试跑名单)。
    保持 current 里仍合格的币(减少洗牌);每次最多换 max_new。
    """
    by_sym = {s["symbol"]: s for s in scored}
    cur_ok = [c for c in current if c in by_sym]
    # [h797 2026-10-04] 宇宙收敛:slots 只限制新增时,12 币宇宙永远不缩 ——
    # 在槽币全部保留(实测提案=现役 12 币)。现在:**在槽币也按流可预测性排序,
    # 超出 slots 的最弱者被裁出**(这是"选币机制是重头"的执行点)。
    def _key(s: Dict[str, Any]) -> float:
        # [h695b] 排序键 = 形态 edge − 单边度惩罚(仅影响**新增候选**的先后,
        # 不动在槽币)⇒ 同等 edge 时优先选流量双边的币,单边币仍可被选中。
        _e = float(s.get("max_edge") or -99.0)
        _osi = s.get("flow_osi")
        _pen = 0.30 * float(_osi) if _osi is not None else 0.15   # 惩罚上限 0.30bp
        # [h797 2026-10-04 用户"选币机制是重头"] 流可预测性优先:
        #   flow_tradeable(流边≥3bp 且顺流>0 且 n≥8)⇒ +100 分(必选);
        #   已测出但不可交易(流边<3bp 或顺流≤0)⇒ −50 分(方向不可预测,撤);
        #   样本不足(n<8)⇒ 0(中性,继续积累样本)。
        _ft = s.get("flow_tradeable")
        if _ft is True:
            _e += 100.0
        elif _ft is False:
            _e -= 50.0
        return _e - _pen

    if len(cur_ok) > slots:
        # 超出槽位 ⇒ 按同一把 key(含流可预测性/模型批注)裁掉最弱者;
        # 保持 cur_ok 为**符号列表**(下面的 uni/_cur_ok_syms 都按符号用)。
        cur_ok = [s["symbol"] for s in sorted(
            (by_sym[c] for c in cur_ok), key=_key, reverse=True)[:slots]]
    _cur_ok_syms = set(cur_ok)
    cand = [s for s in scored if s["symbol"] not in _cur_ok_syms]
    # 反转族槽:family=reversal 优先(但动量币也可以占反转槽,只要 edge 最高)
    rev_cand = [s for s in cand if s["family"] == "reversal"]
    mom_cand = [s for s in cand if s["family"] != "reversal"]

    rev_cand.sort(key=_key, reverse=True)
    mom_cand.sort(key=_key, reverse=True)
    mom_slots = slots - rev_slots
    uni = list(cur_ok)
    new_in: List[str] = []
    # 动量槽:优先动量族
    for s in mom_cand[:mom_slots]:
        if len(uni) >= slots:
            break
        if len(new_in) >= max_new:
            break
        uni.append(s["symbol"])
        new_in.append(s["symbol"])
    # 反转槽:优先反转族,不足再由动量族回填
    for s in rev_cand[:rev_slots]:
        if len(uni) >= slots:
            break
        if len(new_in) >= max_new:
            break
        uni.append(s["symbol"])
        new_in.append(s["symbol"])
    if len(uni) < slots:
        for s in [x for x in rev_cand + mom_cand if x["symbol"] not in set(uni)]:
            if len(uni) >= slots or len(new_in) >= max_new:
                break
            uni.append(s["symbol"])
            new_in.append(s["symbol"])
    dropped = [c for c in current if c not in uni]
    return uni, dropped


def build_pattern_matrix(uni: Sequence[str], scored: Sequence[Dict[str, Any]]) -> Dict[str, List[str]]:
    """[§9.2 选币即配闸] {币: [允许形态上下文]}。runner._detect_pattern 语境:
    'P1' / 'P45' / ''。反转族 → ['P1'],动量族 → ['P45'],双资格 → ['P1','P45']。"""
    by_sym = {s["symbol"]: s for s in scored}
    out: Dict[str, List[str]] = {}
    for sym in uni:
        s = by_sym.get(sym)
        if not s:
            continue
        ctx = []
        if "p1" in (s.get("patterns") or []):
            ctx.append("P1")
        if any(p in (s.get("patterns") or []) for p in ("p4", "p5")):
            ctx.append("P45")
        out[sym] = ctx
    return out


# ── 数据层 ─────────────────────────────────────────────────────

def _candidate_symbols() -> List[str]:
    """近 24h book ticker 出现过的币(结构性排除 USD1 后缀)。"""
    import psycopg
    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT symbol FROM asterdex_book_ticker"
            " WHERE event_ts_ms > (EXTRACT(EPOCH FROM now())-86400)*1000")
        rows = [r[0] for r in cur.fetchall()]
    out = []
    for r in rows:
        s = str(r or "")
        if not s.endswith("USDT"):
            continue
        bare = s[:-4]
        if not _excluded(bare):
            out.append(bare)
    return sorted(set(out))


def _snapshot_age_s(sym: str) -> Optional[float]:
    """数据管道覆盖判据:该币的行情数据有多旧(秒)。

    [h760 2026-10-03] **判据改用原生盘口**(asterdex 场馆):
    实测原判据查 `market_orderbook_snapshots`(DC 快照)只服务 13 个币 ——
    而 runner 对 asterdex 消费的是**原生 `asterdex_book_ticker`**(见 runner
    h376 注释:"asterdex 用原生 book_ticker(market_orderbook_snapshots 缺币)")。
    用错数据源 ⇒ SEI/PUMP/ADA/AAVE/LIT/ENA/XMR 等宽价差币**永远进不了池子**
    (合格池只有 5 个,广度被卡死)。
    现在:①先查原生盘口(真正被消费的源);②查不到再回退 DC 快照(旧逻辑)。
    """
    import psycopg
    # ① 原生盘口(asterdex 实际消费的源)
    try:
        with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
            cur.execute(
                "SELECT EXTRACT(EPOCH FROM (now() - to_timestamp(max(event_ts_ms)/1000.0)))::float8"
                " FROM asterdex_book_ticker WHERE symbol=%s"
                " AND event_ts_ms > (EXTRACT(EPOCH FROM now())-86400)*1000",
                (str(sym).upper() + "USDT",))
            row = cur.fetchone()
            if row and row[0] is not None:
                return float(row[0])
    except Exception:
        pass
    # ② 回退:DC 快照(旧判据,保留)
    try:
        with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
            cur.execute(
                "SELECT EXTRACT(EPOCH FROM (now() - to_timestamp(max(timestamp)/1000.0)))::float8"
                " FROM market_orderbook_snapshots"
                " WHERE exchange='asterdex' AND symbol=%s"
                " AND timestamp > (EXTRACT(EPOCH FROM now())-86400)*1000",
                (sym,))
            row = cur.fetchone()
            return float(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


SNAPSHOT_MAX_AGE_S = 600.0    # 快照年龄 > 10 分钟 ⇒ 数据管道不服务,本轮不入选


def _roll_l1(sym: str, hours: float) -> Optional[Dict[str, Any]]:
    """滚动窗口:rel_spread 序列 + 15s 中价序列(供 L1/L2 共用)。"""
    import psycopg
    vs = sym + "USDT"
    with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(
            "SELECT event_ts_ms, bid_px, ask_px FROM asterdex_book_ticker"
            " WHERE symbol=%s AND event_ts_ms >= %s"
            " ORDER BY event_ts_ms",
            (vs, int((time.time() - hours * 3600.0) * 1000)))
        rows = cur.fetchall()
    rels: List[float] = []
    mids: List[float] = []
    last_bucket = -1
    for ms, b, a in rows:
        if not b or not a or float(a) <= float(b) or float(b) <= 0:
            continue
        mid = (float(b) + float(a)) / 2.0
        bucket = int(float(ms) / 15000.0)
        if bucket == last_bucket and mids:
            rels[-1] = (float(a) - float(b)) / mid * 1e4
            mids[-1] = mid
        else:
            rels.append((float(a) - float(b)) / mid * 1e4)
            mids.append(mid)
            last_bucket = bucket
    if len(rels) < MIN_BUCKETS:
        return None
    return {"rels": rels, "mids": mids, "n_buckets": len(rels)}


# ── 主流程 ─────────────────────────────────────────────────────

def _read_meta(lane: str) -> Dict[str, Any]:
    import psycopg
    with psycopg.connect(_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (lane,))
        row = cur.fetchone()
        return dict(row[0] or {}) if row else {}


def evaluate(lane: str, *, slots: int, rev_slots: int, hours: float) -> Dict[str, Any]:
    """预览评估(不写库)。返回提案全文。"""
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    meta = _read_meta(lane)
    if meta.get("trial_active"):
        return {"frozen": True, "reason": "trial_active=true(治理互锁,§9.4)",
                "symbols": meta.get("symbols")}
    # [h657/h696] Q 速控衰减联动:**多信号快速判据**(连续停 3h 作兜底)
    q_decayed: List[str] = []
    try:
        from backend.services.market_maker.qspeed import (
            pnl_decayed_coins, q_decayed_coins,
        )
        q_decayed = q_decayed_coins(min_minutes=180.0)          # 含 60min 快判据
        # [h696] 亏损淘汰:近 60 分钟逐腿净 < −1bp 且 n≥20(真金白银证据)
        _pnl_dec = pnl_decayed_coins(lane)
        if _pnl_dec:
            q_decayed = sorted(set(q_decayed) | set(_pnl_dec))
    except Exception:
        q_decayed = []
    # [h670] 敞口健康(用户指令):持仓名义过大(>80% 单币上限)或未实现浮亏
    # 深于 30bp ⇒ 视为"被市场压制"衰减,本轮剔除候选(与 q_decayed 同族,非禁入)。
    exposure_decayed: List[str] = []
    try:
        from backend.services.market_maker.attribution import _market_dsn
        import psycopg
        _lm = dict(meta or {})
        _cap = float((_lm.get("live_caps") or {}).get("per_symbol_usd", 150.0) or 150.0)
        with psycopg.connect(_market_dsn(), autocommit=True) as _c, _c.cursor() as _cu:
            _cu.execute("SELECT symbol, state_json FROM lane_runtime_state WHERE lane_id=%s",
                        (lane,))
            _states = dict(_cu.fetchall())
            for _sym, _sj in _states.items():
                _st = dict(_sj or {})
                _qty = float(_st.get("qty") or 0.0)
                if abs(_qty) < 1e-12:
                    continue
                _cu.execute(
                    "SELECT bid_px, ask_px FROM asterdex_book_ticker"
                    " WHERE symbol=%s AND bid_px>0 AND ask_px>bid_px"
                    " ORDER BY event_ts_ms DESC LIMIT 1", (f"{_sym}USDT",))
                _row = _cu.fetchone()
                if not _row:
                    continue
                _mid = (float(_row[0]) + float(_row[1])) / 2.0
                _notional = abs(_qty) * _mid
                _avg_mid = float(_st.get("avg_mid") or 0.0)
                _upl_bp = ((_mid - _avg_mid) / _avg_mid * 1e4) if _avg_mid > 0 else 0.0
                if _notional > 0.8 * _cap or _upl_bp < -30.0:
                    exposure_decayed.append(str(_sym).upper())
    except Exception:
        exposure_decayed = []
    current = list(meta.get("symbols") or [])
    cands = _candidate_symbols()
    rows: List[Dict[str, Any]] = []
    for sym in cands:
        roll = _roll_l1(sym, hours)
        if not roll:
            continue
        ok_share = spread_ok_share(roll["rels"])
        vr = vr60_from_mids(roll["mids"])
        sc = score_coin(seed, sym, vr)
        sc["spread_ok_share"] = round(ok_share, 3)
        sc["n_buckets"] = roll["n_buckets"]
        sc["snapshot_age_s"] = _snapshot_age_s(sym)
        rows.append(sc)
    # [h653 修订] 硬门槛 = 实时盘口流覆盖(asterdex_book_ticker,_roll_l1 的
    # MIN_BUCKETS 已保证,runner 的 mid 源)。快照管道年龄只作**信息项**:
    # h653b 动态订阅会在换币后 1~2 分钟内自动补齐快照,不再据此阻断选币。
    feasible = [r for r in rows if r["spread_ok_share"] >= SPREAD_OK_SHARE_MIN]
    # [h695b] **流量单边度只作偏好,不做硬排除**(修正 h695):
    # 实测 UNI/ENA/ARB 的 flow_osi 0.84~0.88(被判"单边"),但它们的微试跑
    # 每腿净 **+0.99/+4.46/+1.44bp**,而"双边"的 BNB 是 −0.09bp ⇒ 单边度是
    # **瞬时行情状态**(当前抛售),不是币的属性 ⇒ 硬排除会踢掉已验证盈利币。
    # 改为:①保留可观测;②仍标记"若单边则本轮不作为**新增**候选优先",
    # 由 `choose_slots` 在**入场候选排序**里用(不影响在槽币)。
    for r in feasible:
        r["flow_osi"] = flow_osi(r["symbol"])
    _osi_hard = bool(os.getenv("MM_FLOW_OSI_HARD", "") == "1")   # 紧急可用,默认关
    if _osi_hard:
        _two_sided = [r for r in feasible
                      if r.get("flow_osi") is None
                      or float(r["flow_osi"]) < FLOW_OSI_MAX]
        if len(_two_sided) >= slots:
            _excluded_osi = [r["symbol"] for r in feasible if r not in _two_sided]
            feasible = _two_sided
        else:
            _excluded_osi = []
    else:
        _excluded_osi = [r["symbol"] for r in feasible
                         if r.get("flow_osi") is not None
                         and float(r["flow_osi"]) >= FLOW_OSI_MAX]
    trials: List[str] = []
    scored: List[Dict[str, Any]] = []
    # [h814b 2026-10-04] 模型批注必须**在入围判定之前**算好,否则
    # "历史差评"的模型批注币(BTW/LYN:旧范式下的亏损史)会被 neg_seed 直接剔除。
    _model_ok_pre = {}
    try:
        _rbp = json.loads((ROOT / "data" / "flow_edge_robust.json").read_text(encoding="utf-8"))
        for _rp in (_rbp.get("results") or []):
            if float(_rp.get("median_edge_bp") or 0) > 5.0 \
                    and int(_rp.get("positive_runs") or 0) * 2 > int(_rp.get("n_runs") or 1):
                _model_ok_pre[str(_rp["symbol"]).upper()] = float(_rp["median_edge_bp"])
    except Exception:
        pass
    for r in feasible:
        _sym_ma = str(r["symbol"]).upper()
        _is_model_ok = _sym_ma in _model_ok_pre
        if r["neg_seed"] and (r.get("max_edge") or -99.0) < NEG_SEED_BAR \
                and not _is_model_ok:
            continue    # 历史差评且 edge 不足:本轮不入围(回候选池,非禁入)
        #   ⚠️ 模型稳健批注(median>+5bp, 6/6)⇒ **豁免旧历史**(旧账来自被动做市范式)
        scored.append(r)
        if r["neg_seed"] or (r["symbol"] not in current):
            trials.append(r["symbol"])    # 回归币/新币:须过 L4 微试跑
    # [h797 2026-10-04 用户"选币机制是重头"] 流可预测性标注(读 h796 的打分):
    #   flow_tradeable=True/False/None 进 scored ⇒ choose_slots 用它排序
    #   (True +100 / False −50 / 样本不足 None 中性)。
    # [h814 2026-10-04 模型驱动选币] 另读 h812 的稳健性验证结果
    #   (data/flow_edge_robust.json):中位 edge > +5bp 的{币 × 时限}组合
    #   ⇒ 该币标 model_approved,选择器 +100 分(与流可交易同级)。
    _model_ok = {}
    try:
        _rb = json.loads((ROOT / "data" / "flow_edge_robust.json").read_text(encoding="utf-8"))
        for _r in (_rb.get("results") or []):
            if float(_r.get("median_edge_bp") or 0) > 5.0 \
                    and int(_r.get("positive_runs") or 0) * 2 > int(_r.get("n_runs") or 1):
                _model_ok[str(_r["symbol"]).upper()] = float(_r["median_edge_bp"])
    except Exception:
        pass
    try:
        _fe = json.loads((ROOT / "data" / "flow_edge_last.json").read_text(encoding="utf-8"))
        _fe_map = {str(x["symbol"]).upper(): bool(x.get("flow_tradeable"))
                   for x in (_fe.get("detail") or []) if x.get("n", 0) >= 8}
        for r in scored:
            _sym = str(r["symbol"]).upper()
            r["flow_tradeable"] = _fe_map.get(_sym)
            if _sym in _model_ok:
                r["flow_tradeable"] = True
                r["model_approved"] = _model_ok[_sym]
    except Exception:
        pass
    # [h663 审计#3 修复] q_decayed 闭环:停加仓连续 ≥3h 的币本轮从候选剔除
    # (非禁入:Q 恢复后它不在 q_decayed 列表,自动回归)。守卫:剔除后候选不足
    # slots 个时不剔除(避免宇宙被清空)。
    # [h670] 敞口衰减同族合并:名义过大/浮亏过深的币本轮也不入围。
    _decay_set = set(q_decayed) | set(exposure_decayed)
    # [h696b] **防抖保护**:仍在 12h 微试跑里的新币,若入槽未满 4h,不因"Q 快判据"
    # 被踢(只有**亏损证据** pnl_decayed 才允许提前踢)。否则会出现
    # "18:48 进、21:00 出"的抖动,而微试跑本来就该给足样本。
    try:
        _ut = dict(meta.get("universe_trial") or {})
        _trial_syms = {str(s).upper() for s in (_ut.get("symbols") or [])}
        _started = _ut.get("started_at")
        _age_h = 99.0
        if _started:
            _age_h = (datetime.now(timezone.utc)
                      - datetime.fromisoformat(str(_started).replace("Z", "+00:00"))
                      ).total_seconds() / 3600.0
        if _trial_syms and _age_h < 4.0:
            _pnl_dec_set = set(pnl_decayed_coins(lane))
            _protected = _trial_syms - _pnl_dec_set
            if _protected:
                _decay_set -= _protected
    except Exception:
        pass
    # [h708] **最短驻留 1h**:币龄 < 1h 的币,非亏损证据不换出(币龄曲线显示
    # 0-30min 是最差冷启动档、2-6h 是最好档 ⇒ 换太快把好档期全错过)。
    try:
        _now_ts = datetime.now(timezone.utc).timestamp()
        _entry: Dict[str, float] = {}
        for _o in sorted(meta.get("ops_changes") or [],
                         key=lambda x: x.get("ts") or ""):
            if _o.get("op") != "set_symbols" or not _o.get("after"):
                continue
            _t = datetime.fromisoformat(str(_o["ts"]).replace("Z", "+00:00")).timestamp()
            for _s in _o["after"]:
                _entry.setdefault(str(_s).upper(), _t)
        _pnl_dec2 = set(pnl_decayed_coins(lane))
        _young = {s for s in _decay_set
                  if (_now_ts - _entry.get(s, _now_ts)) < MIN_TENURE_SEC
                  and s not in _pnl_dec2}
        if _young:
            _decay_set -= _young
    except Exception:
        pass
    _decay_hit = [s for s in scored if s["symbol"] in _decay_set]
    # [h758 2026-10-03] 亏损淘汰不可被槽位数挡住。原口径 `剩余 ≥ slots` 才淘汰:
    # 当合格池只有 ~6 个而 slots=8 时**永不满足** ⇒ 淘汰机制实际停摆
    # (实测 WLD 4h −1.02U / VIRTUAL −0.44U,却因"槽位不够"继续挂着)。
    # 新口径:亏损证据优先,允许降到**广度地板 3**;槽位缺口由池内次优候选补。
    _remain = len(scored) - len(_decay_hit)
    if os.environ.get("V5_DEBUG"):
        import sys as _sys
        print(f"[V5_DEBUG] scored={len(scored)} decay_set={sorted(_decay_set)} "
              f"decay_hit={[s['symbol'] for s in _decay_hit]} remain={_remain} "
              f"slots={slots}", file=_sys.stderr)
    # [_remain] 广度地板设为 2:实测合格池只有 5 个(14 个宽价差币被 6h 窗口的
    # "价差达标占比"过滤器挡下),3 个在淘汰集 ⇒ 地板若为 3 则淘汰永不生效。
    # 取舍:宁跑 2 个盈利币,不跑 5 个混合币(亏损币会把盈利币的贡献吃掉)。
    if _decay_hit and _remain >= 2:
        scored = [s for s in scored if s["symbol"] not in _decay_set]
        decayed_out = sorted(s["symbol"] for s in _decay_hit)
    else:
        decayed_out = []
    uni, dropped = choose_slots(scored, slots=slots, rev_slots=rev_slots,
                                max_new=2, current=current)
    # [h670] L4 微试跑落地:新币/回归币在槽即为 12h 预注册试跑
    # (设计 §3 L4/§6.4:h356 口径,到期 d1_verdict 逐币仲裁,不过即摘出)。
    micro_trials = [t for t in trials if t in uni]
    return {
        "frozen": False, "as_of": datetime.now(timezone.utc).isoformat(),
        "lane": lane, "current": current, "proposed": uni, "dropped": dropped,
        "requires_micro_trial": micro_trials,
        "pattern_matrix": build_pattern_matrix(uni, scored),
        "scoreboard": sorted(scored, key=lambda x: -(x.get("max_edge") or -99.0))[:24],
        "dc_snapshot_stale": sorted(
            r["symbol"] for r in rows
            if r.get("snapshot_age_s") is None or float(r["snapshot_age_s"]) > SNAPSHOT_MAX_AGE_S),
        # [h657] Q 速控衰减联动:停加仓连续 ≥3h 的币,下轮轮换优先换出
        "q_decayed": sorted(q_decayed),
        # [h670] 敞口衰减(名义>80%上限 或 浮亏<-30bp)
        "exposure_decayed": sorted(exposure_decayed),
        "decayed_out": decayed_out,
        "feasible_n": len(feasible), "candidates_n": len(cands),
        # [h695] 流量单边度:分数榜与"因单边被排除"的币(可观测/可复盘)
        "flow_osi": {r["symbol"]: (round(float(r["flow_osi"]), 3)
                                   if r.get("flow_osi") is not None else None)
                     for r in rows},
        "osi_excluded": sorted(_excluded_osi),
        "params": {"spread_min_bp": SPREAD_MIN_BP, "spread_ok_share_min":
                   SPREAD_OK_SHARE_MIN, "roll_hours": hours, "vr60_rev_max":
                   VR60_REV_MAX, "slots": slots, "rev_slots": rev_slots},
    }


def apply_proposal(lane: str, proposal: Dict[str, Any],
                   write_matrix: bool = True) -> bool:
    """写库:meta.symbols + pattern_matrix + h329v5_rollback + ops_changes。

    write_matrix=False 用于**首次换币**(单变量纪律:宇宙与形态矩阵分开试跑,
    矩阵留待宇宙判决后的独立试跑)。
    """
    import psycopg
    meta = _read_meta(lane)
    if not meta or proposal.get("frozen"):
        return False
    uni = list(proposal.get("proposed") or [])
    before = list(meta.get("symbols") or [])
    meta["symbols"] = uni
    # [h663 审计#9 修复] 回滚必须包含被覆盖的旧 pattern_matrix,否则 write_matrix=True
    # 的换币无法完整回滚。
    meta["h329v5_rollback"] = {"symbols": before,
                               "pattern_matrix": meta.get("pattern_matrix"),
                               "by": "h329_selector_v5"}
    if write_matrix:
        meta["pattern_matrix"] = proposal.get("pattern_matrix") or {}
    ops = list(meta.get("ops_changes") or [])
    ops.append({"by": "h329_selector_v5", "op": "set_symbols",
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "before": before, "after": uni,
                "pattern_matrix": meta.get("pattern_matrix"),
                "reason": "v5 滚动质量雷达:价差可行性+形态×币匹配+事件研究分榜;"})
    # [h670] L4 微试跑落地(设计 §3/§6.4):新币/回归币在槽 = 12h 预注册试跑,
    # 到期由 d1_verdict 逐币仲裁,不过即摘出。写 meta.universe_trial 供判决读取。
    micro = list(proposal.get("requires_micro_trial") or [])
    if micro:
        now = datetime.now(timezone.utc)
        meta["universe_trial"] = {
            "symbols": micro,
            "started_at": now.isoformat(timespec="seconds"),
            "due_at": (now + timedelta(hours=12)).isoformat(timespec="seconds"),
            "verdict": "pending",
        }
        ops.append({"by": "h329_selector_v5", "op": "universe_trial",
                    "ts": now.isoformat(timespec="seconds"),
                    "symbols": micro,
                    "reason": "L4 微试跑:新币/回归币 12h 预注册仲裁(h356 口径)"})
    meta["ops_changes"] = ops[-40:]
    with psycopg.connect(_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute("UPDATE lane_registry SET meta_json=%s WHERE lane_id=%s",
                    (json.dumps(meta, ensure_ascii=False, default=str), lane))
        return cur.rowcount > 0


def _clearly_better(prop: Dict[str, Any], *, need_bp: float = None) -> bool:
    """[h696] 提案里的**新币**是否明显强于**现役最弱币**(edge 差 ≥ 阈值)。

    为什么:用户反馈"淘汰/替换过于滞后"。最短驻留(DWELL_SEC)本意是防抖动,
    但当新候选的形态 edge 比在槽最弱币高出 1.5bp 以上时,再等 2h 是纯损失
    ⇒ 允许提前替换(仍受 micro-trial 12h 仲裁约束)。
    """
    try:
        need = float(CLEAR_BETTER_BP if need_bp is None else need_bp)
        cur = set(prop.get("current") or [])
        prop_new = [s for s in (prop.get("proposed") or []) if s not in cur]
        if not prop_new:
            return False
        board = {str(r.get("symbol")): float(r.get("max_edge") or -99.0)
                 for r in (prop.get("scoreboard") or [])}
        cur_edges = [board.get(s) for s in cur if board.get(s) is not None]
        new_edges = [board.get(s) for s in prop_new if board.get(s) is not None]
        if not cur_edges or not new_edges:
            return False
        return (max(new_edges) - min(cur_edges)) >= need
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-pattern-matrix", action="store_true",
                    help="首次换币不写形态矩阵(单变量:矩阵留待宇宙判决后独立试跑)")
    ap.add_argument("--lane", default="mm_asterdex")
    ap.add_argument("--slots", type=int, default=4)
    ap.add_argument("--rev-slots", type=int, default=3)
    ap.add_argument("--hours", type=float, default=ROLL_HOURS)
    args = ap.parse_args()

    if args.apply:
        try:
            last = float(MARKER.read_text(encoding="utf-8").strip() or 0)
        except Exception:
            last = 0.0
        _age = time.time() - last
        # [h680 设计回归] 雷达是"持续运行、衰减即换",不是固定时段任务:
        #   · **衰减驱动的替换立刻执行**(不吃冷却)——现有槽币质量衰减就该马上换;
        #   · 非衰减的"择优替换"才受最短驻留(DWELL_SEC)约束,防抖动。
        # 冷却判断因此推迟到 evaluate 之后(需要知道是否衰减)。
        if _age < 0:
            last = 0.0
        _dwell_left = max(0.0, DWELL_SEC - _age)

    prop = evaluate(args.lane, slots=args.slots, rev_slots=args.rev_slots,
                    hours=args.hours)
    print(json.dumps(prop, ensure_ascii=False, indent=2, default=str))
    if not args.apply:
        print("\n（预览）加 --apply --lane %s 才写库" % args.lane)
        return 0
    if prop.get("frozen"):
        print("✗ 试跑中(trial_active),宇宙冻结,不写库。")
        return 4
    # [h680] 替换判定
    _decay_hit = list(prop.get("decayed_out") or [])
    _cur = set(prop.get("current") or [])
    _prop = list(prop.get("proposed") or [])
    _changed = set(_prop) != _cur
    if not _changed:
        print("✓ 无需换币(现役宇宙即最优)")
        return 0
    if _decay_hit:
        print(f"⚡ 衰减驱动替换(不吃冷却):衰减币 {_decay_hit} ⇒ 立即换")
    elif _dwell_left > 0 and not _clearly_better(prop):
        print(f"✗ 非衰减的择优替换被最短驻留挡住(还需 {_dwell_left/3600:.2f}h),"
              f"当前 {sorted(_cur)} vs 提案 {_prop}")
        return 3
    elif _dwell_left > 0:
        print(f"⚡ 明显更优的替换(edge 差距 ≥ {CLEAR_BETTER_BP}bp)⇒ 提前换"
              f"(常规驻留还剩 {_dwell_left/3600:.2f}h)")
    ok = apply_proposal(args.lane, prop, write_matrix=not args.no_pattern_matrix)
    if ok:
        MARKER.write_text(str(time.time()), encoding="utf-8")
        print(f"✓ 已写库:新宇宙 {prop.get('proposed')}")
    return 0 if ok else 1


# TODO(h652):L3 事件研究分榜滚动重算(批处理):用共享归因模块对每候选币重算
# P1/P2/P4/P5 事件研究 edge(168h 滚动),替换种子榜;L1 衰减检测对在槽币每日输出。
if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
