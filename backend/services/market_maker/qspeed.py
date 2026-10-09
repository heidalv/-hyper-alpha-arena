# -*- coding: utf-8 -*-
"""[h657 2026-09-30] Q 速控(腿速自适应)—— 设计:研究结论/腿速自适应Q速控设计_20260930.md。

Q 分数逐币滚动 15 分钟,动作表(滞后再武装):
  Q ≥ 0.7 → mult 1.0(全速)
  0.4 ≤ Q < 0.7 → mult 0.5(半速)
  Q < 0.4 → mult 0.0(停加仓;F91 减仓豁免自动成立),dwell 5 分钟,
            再武装条件 Q ≥ 0.5(滞后带 0.1)
Q 只作**加仓腿目标量的乘子**(与 per_symbol_size_mult 同点位),减仓腿精确平仓不变。
总开关 `limits.q_speed_gate`:0=影子(只算只记,不动交易);1=减速侧启用。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[3]
HISTORY = ROOT / "data" / "q_speed_history.jsonl"
HISTORY_MAX_LINES = 4000

# Q 权重(初版建议值,影子期用 Q 分桶收益标定后冻结)
W_SPREAD = 0.35
W_FILL = 0.25
W_CAPTURE = 0.25
W_ADV = 0.15

Q_FULL = 0.7      # ≥ 此值全速
Q_HALF = 0.4      # ≥ 此值半速,低于停
Q_REARM = 0.5     # 停加仓后再武装门槛(滞后带)
DWELL_MIN = 5     # 停加仓最小持续(分钟),期内不因一次变好恢复
# [h684] 试探性复入:暂停超时后**半速复入取证**,并指数退避(防死循环)
PROBATION_MIN = 10.0      # 首次试探复入等待(分钟)
PROBATION_MAX_MIN = 60.0  # 退避上限

# [h662 尾部风暴] 止损/强平是失血主源(单腿 −60~−120bp),15 分钟平均会把它抹平:
# 近 30 分钟止损类腿 ≥ STOP_STORM_N ⇒ Q 强制压到 Q_HALF 之下(触发停加仓)。
STOP_STORM_N = 2
STOP_STORM_Q_CAP = 0.39

# 归一化锚(影子期标定;先给保守初值)
CAPTURE_GOOD_BP = 0.3    # 捕获 ≥ 0.3bp 视为满分
ADV_GOOD_BP = 1.0        # |逆选择| ≤ 1.0bp 视为满分(≥1.5bp 归零)
FILL_BASE_PER_H = 10.0   # 该币每小时 ≥10 腿视为活跃满分


def compute_q(*, spread_ok_share: float, fill_rate_per_h: float,
              capture_bp: float, adv_bp: float,
              toxic: float = 0.0, stop_rate_30m: float = 0.0) -> float:
    """Q ∈ [0,1](纯函数)。各分量线性夹紧到 [0,1] 后按权重合成,毒性作罚分。

    [h662] 尾部风暴硬钳:近 30 分钟止损类腿 ≥ STOP_STORM_N ⇒ Q 强制 ≤
    STOP_STORM_Q_CAP(触发停加仓)——平均质量测不到尾部失血,这是尾部专用信号。
    """
    s = max(0.0, min(1.0, float(spread_ok_share or 0.0)))
    f = max(0.0, min(1.0, float(fill_rate_per_h or 0.0) / FILL_BASE_PER_H))
    c = max(0.0, min(1.0, float(capture_bp or 0.0) / CAPTURE_GOOD_BP))
    a = max(0.0, min(1.0, (ADV_GOOD_BP - abs(float(adv_bp or 0.0))) / ADV_GOOD_BP))
    q = W_SPREAD * s + W_FILL * f + W_CAPTURE * c + W_ADV * a
    q = max(0.0, min(1.0, q - max(0.0, float(toxic or 0.0))))
    if float(stop_rate_30m or 0.0) >= STOP_STORM_N:
        q = min(q, STOP_STORM_Q_CAP)
    return round(q, 4)


def speed_action(q: float, prev: Optional[Mapping[str, Any]] = None,
                 now_min: Optional[float] = None) -> Dict[str, Any]:
    """动作表 + 滞后再武装(纯函数)。

    返回 {action, mult, paused_since}(paused_since=None 表示未在停加仓态)。
    prev = 上一轮状态 {action, mult, paused_since}。
    """
    q = float(q)
    now_min = float(now_min if now_min is not None else time.time() / 60.0)
    prev = dict(prev or {})
    paused_since = prev.get("paused_since")
    if paused_since is not None:
        # 停加仓态:滞后再武装(Q ≥ REARM 且 dwell 已满),期间即使 Q 回升也保持停
        if q >= Q_REARM and (now_min - float(paused_since)) >= DWELL_MIN:
            paused_since = None
        else:
            # [h684] **试探性复入(打破死循环)**:暂停期没有新成交 ⇒ Q 用的还是
            # 暂停前的冻结窗口 ⇒ 永远够不到 Q_REARM=0.5(实测 BNB 卡 0.378 不动)。
            # 因此:暂停超过 PROBATION_MIN 后**半速复入取证**(mult=0.5):
            #   · 质量真的好 ⇒ 新成交把 Q 拉起来 ⇒ 正常恢复;
            #   · 质量还是差 ⇒ Q 迅速掉回 <0.4 ⇒ 再次暂停,且退避翻倍。
            _paused_min = now_min - float(paused_since)
            _prob = float(prev.get("probation_until") or 0.0)
            _backoff = float(prev.get("backoff_min") or PROBATION_MIN)
            if _paused_min >= _backoff:
                return {"action": "probation", "mult": 0.5,
                        "paused_since": paused_since,
                        "probation": True,
                        "backoff_min": min(_backoff * 2, PROBATION_MAX_MIN)}
            return {"action": "pause", "mult": 0.0, "paused_since": paused_since,
                    "backoff_min": _backoff}
    if q >= Q_FULL:
        return {"action": "full", "mult": 1.0, "paused_since": None}
    if q >= Q_HALF:
        return {"action": "half", "mult": 0.5, "paused_since": None}
    return {"action": "pause", "mult": 0.0, "paused_since": now_min}


def _pause_started_ts(sym: str) -> Optional[float]:
    """[h682] 该币当前"停加仓段"的起点(读历史文件;未暂停返回 None)。

    为什么要它:暂停期没有成交 ⇒ fill_rate/capture 归零 ⇒ Q 永远 <0.4 ⇒
    **自我锁死**(实测 BNB Q 卡在 0.30~0.35 爬不出来)。度量必须只看
    "我们真正在挂单的那段时间"。"""
    try:
        lines = HISTORY.read_text(encoding="utf-8").splitlines()
    except Exception:
        return None
    start: Optional[float] = None
    for line in lines[-400:]:
        try:
            e = json.loads(line)
        except Exception:
            continue
        if str(e.get("symbol") or "").upper() != str(sym).upper():
            continue
        _m = e.get("mult")
        _m = float(_m) if _m is not None else 1.0
        t = float(e.get("ts") or 0.0)
        if _m <= 0:
            if start is None:
                start = t
        else:
            start = None
    return start


def gather_coin_metrics(lane_id: str, sym: str, minutes: float = 15.0) -> Optional[Dict[str, float]]:
    """[h898 2026-10-07 重造为 scalp 口径] 单币滚动指标(只读)。

    策略已从「做市」转为「趋势概率快进快出」,Q 速控的指标随之从做市口径
    (价差份额/捕获/逆选择)改为 **scalp 口径**:
      fill_rate_per_h = 近 60 分钟该币**我们的成交腿数**(成交率——挂单有没有被穿越);
      edge_1h_bp      = 近 60 分钟该币**逐腿净 bp 均值**(真金白银的实时 edge);
      flow_30m        = 近 30 分钟该币**市场逐笔数**(实时活跃度——没流就没方向也没成交);
      stop_rate_30m   = 近 30 分钟强制 taker 出口腿数(尾部风暴,保留)。

    ⚠️ [h898 修复停更根因] 旧实现用 `h425_repair_trial._main_dsn()` 取主库 DSN,
    那个临时试跑脚本重构后删了该函数 ⇒ 每次取数抛 AttributeError 被
    `except: return None` 静默吞掉 ⇒ Q 速控 10-03 起停更。现改用权威
    `attribution._main_dsn()`(它内部读 `read_env_dsn`,稳定)。
    ⚠️ 失败返回 None(调用方保留上一状态),不静默清零。
    """
    try:
        import psycopg
        from backend.services.market_maker.attribution import (
            _main_dsn, _market_dsn, vs_symbol,
        )
        vs = vs_symbol(sym)
        # 1) 市场实时逐笔率(近 30 分钟)
        with psycopg.connect(_market_dsn(), autocommit=True) as c, c.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM asterdex_trades"
                " WHERE symbol=%s AND event_ts_ms > %s",
                (vs, int((time.time() - 30 * 60.0) * 1000)))
            flow_30m = float(cur.fetchone()[0] or 0)
        # 2) 我们的成交率 + 实时 edge(近 60 分钟,主库 lane_ledger)
        with psycopg.connect(_main_dsn(), autocommit=True) as c, c.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*), COALESCE(AVG(net_bp),0) FROM lane_ledger"
                " WHERE lane_id=%s AND symbol=%s AND event='fill'"
                " AND ts > now() - interval '60 minutes'",
                (lane_id, sym))
            n_fill, edge_1h = cur.fetchone()
            # 3) 尾部风暴(近 30 分钟强制 taker 出口)
            cur.execute(
                "SELECT count(*) FROM lane_ledger"
                " WHERE lane_id=%s AND symbol=%s AND event='fill'"
                " AND ts > now() - interval '30 minutes'"
                " AND (COALESCE(meta_json->>'exit_path','') LIKE '%%stop%%'"
                "   OR COALESCE(meta_json->>'exit_path','') LIKE '%%taker%%')",
                (lane_id, sym))
            stop_30m = float(cur.fetchone()[0] or 0)
        return {
            "fill_rate_per_h": float(n_fill or 0),
            "edge_1h_bp": float(edge_1h or 0.0),
            "flow_30m": flow_30m,
            "stop_rate_30m": stop_30m,
        }
    except Exception:
        return None


# ── [h898] scalp 口径的 Q 分(与做市口径 compute_q 并存,后者留作对照) ──
# 权重:成交率 0.40 / 实时 edge 0.35 / 市场流 0.25。
# 锚:10 腿/h = 成交满分;+5bp = edge 满分(−5bp = 0);30 笔/30min = 流满分。
W_FILL_S = 0.40
W_EDGE_S = 0.35
W_FLOW_S = 0.25
FILL_FULL_PER_H = 10.0
EDGE_GOOD_BP_S = 5.0
FLOW_FULL_30M = 30.0


def compute_q_scalp(*, fill_rate_per_h: float, edge_1h_bp: float,
                    flow_30m: float, stop_rate_30m: float = 0.0) -> float:
    """scalp 口径 Q ∈ [0,1](纯函数)。尾部风暴硬钳与做市版一致。"""
    f = max(0.0, min(1.0, float(fill_rate_per_h or 0.0) / FILL_FULL_PER_H))
    e = max(0.0, min(1.0, (float(edge_1h_bp or 0.0) + EDGE_GOOD_BP_S)
                     / (2.0 * EDGE_GOOD_BP_S)))
    fl = max(0.0, min(1.0, float(flow_30m or 0.0) / FLOW_FULL_30M))
    q = W_FILL_S * f + W_EDGE_S * e + W_FLOW_S * fl
    q = max(0.0, min(1.0, q))
    if float(stop_rate_30m or 0.0) >= STOP_STORM_N:
        q = min(q, STOP_STORM_Q_CAP)
    return round(q, 4)


def pnl_decayed_coins(lane_id: str, *, minutes: float = 240.0, min_legs: int = 30,
                      max_net_bp: float = -1.0,
                      max_net_usd: float = -0.30) -> List[str]:
    """[h696/h758] **亏损淘汰**:近 `minutes` 分钟内该币满足

        · 腿数 ≥ `min_legs`,且
        · **(逐腿净 bp 均值 < `max_net_bp`)或(累计净额 < `max_net_usd`)** 美元

    ⇒ 该换掉(真金白银的证据,不等 3 小时)。

    [h758 2026-10-03 用户点名 WLD 后修正] 原口径(60min / n≥20 / avg<−1.0bp)
    **漏掉了 WLD 这类"腿多、每腿接近零、但美元持续失血"的币**:实测 WLD 160 腿、
    每腿 −0.35bp(不触发)、当日 −0.99U。**bp 口径对"大名义 + 小 bp"的失血不敏感**
    —— 而美元口径才是账户真相。故:窗口 60→240 分钟(更稳)、腿数门槛 20→30、
    新增美元条件(OR):累计净额 < −0.30U 即淘汰。腿数门槛防小样本噪声。
    """
    try:
        import psycopg
        from backend.services.market_maker import attribution as _h
        with psycopg.connect(_h._main_dsn(), autocommit=True) as c, c.cursor() as cur:
            cur.execute(
                "SELECT symbol, count(*), AVG(net_bp), SUM(net_bp*notional)/10000.0"
                " FROM lane_ledger WHERE lane_id=%s AND event='fill'"
                "   AND ts > now() - make_interval(mins => %s)"
                " GROUP BY 1 HAVING count(*) >= %s"
                "   AND (AVG(net_bp) < %s OR SUM(net_bp*notional)/10000.0 < %s)",
                (lane_id, int(minutes), int(min_legs), float(max_net_bp),
                 float(max_net_usd)))
            return sorted({str(r[0]).upper() for r in cur.fetchall()})
    except Exception:
        return []


def append_history(entry: Mapping[str, Any]) -> bool:
    """Q 快照落盘(供宇宙 v5 衰减联动与影子验证)。失败不抛。"""
    try:
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY, "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(entry), ensure_ascii=False, default=str) + "\n")
        # 防膨胀:超长截断到最近 HISTORY_MAX_LINES 行
        lines = HISTORY.read_text(encoding="utf-8").splitlines()
        if len(lines) > HISTORY_MAX_LINES:
            HISTORY.write_text("\n".join(lines[-HISTORY_MAX_LINES:]) + "\n",
                               encoding="utf-8")
        return True
    except Exception:
        return False


def q_decayed_coins(min_minutes: float = 180.0, *, window_min: float = 60.0,
                    pause_share_min: float = 0.6,
                    q_avg_max: float = 0.5) -> List[str]:
    """读历史文件:判定"该换掉的币"(供宇宙 v5 衰减联动)。

    [h696 2026-10-01 **淘汰提速**] 用户反馈"淘汰过于滞后"。病根有两个:
      ① 旧判据要求 `mult=0` **连续** ≥180 分钟;
      ② 今晚新增的**试探性复入**(h684)每 10~20 分钟给一次 mult=0.5
         ⇒ "连续"永远被打断 ⇒ **淘汰判据永远不触发**(真 bug)。
    现改为**多信号快速判据**(任一命中即视为衰减,滞后从 3h 降到 ~1h):
      a) 近 `window_min` 分钟内 **mult<1 的时间占比** ≥ pause_share_min(含半速/停);
      b) 近 `window_min` 分钟内 **Q 均值** < q_avg_max;
      c) 旧规则(连续停 ≥ min_minutes)保留为兜底。
    """
    try:
        lines = HISTORY.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    now = time.time()
    cut_fast = now - window_min * 60.0
    run_start: Dict[str, float] = {}
    fast: Dict[str, Dict[str, float]] = {}
    for line in lines:
        try:
            e = json.loads(line)
        except Exception:
            continue
        sym = str(e.get("symbol") or "").upper()
        t = float(e.get("ts") or 0.0)
        # ⚠️ 不能写 `e.get("mult") or 1.0`:0.0 是 falsy 会被吞成 1.0(实测踩坑)
        mult = float(e.get("mult") if e.get("mult") is not None else 1.0)
        q = e.get("q")
        if not sym or t <= 0:
            continue
        # 旧规则:连续停
        if mult <= 0:
            run_start.setdefault(sym, t)
        else:
            run_start.pop(sym, None)
        # 快判据累计(历史每 60s 一条/币)
        if t >= cut_fast:
            f = fast.setdefault(sym, {"n": 0.0, "slow": 0.0, "q": 0.0, "qn": 0.0})
            f["n"] += 1.0
            if mult < 1.0:
                f["slow"] += 1.0
            if q is not None:
                f["q"] += float(q)
                f["qn"] += 1.0
    out: set = set()
    for sym, start in run_start.items():
        if (now - start) / 60.0 >= min_minutes:
            out.add(sym)                       # (c) 旧兜底
    for sym, f in fast.items():
        if f["n"] < 20:
            continue                           # 样本太少(至少 20 分钟历史)
        if f["slow"] / f["n"] >= pause_share_min:
            out.add(sym)                       # (a) 长时间非满速(含试探复入)
        if f["qn"] >= 20 and (f["q"] / f["qn"]) < q_avg_max:
            out.add(sym)                       # (b) Q 均值长期偏低
    return sorted(out)
