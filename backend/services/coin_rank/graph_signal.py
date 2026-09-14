# -*- coding: utf-8 -*-
"""CoinRank 图信号试点 —— RTGNN 迁移第一阶（§4.5 零训练快速试点）。

仅从历史 K 线计算两类「币间关系」信号，纯 numpy、无训练、fail-open：

1. lead_score —— 领先-滞后强度分（RTGNN 非对称图的非学习版）：
   对市场锚（默认 BTC/ETH）计算滞后互相关
       lead_{i,anchor} = mean_{ℓ∈[1..K]} ρ(r_{i,t}, r_{anchor,t−ℓ}) − ρ(r_{i,t}, r_{anchor,t})
   取截面 min-max 归一化到 [0,1]。方向性：锚的过去收益解释了币的当期收益
   → 锚领先币（BTC/ETH 领涨，alt 跟随）。用滞后均值而非 max，避免多重比较噪声。
2. dm_score —— 动量加速度分（RTGNN 动量变化 Δm 的非学习版）：
   M_i(t) = 多周期加权收益；Δm_i = M_i(t) − M_i(t−1)；截面百分位排名 → [0,1]。

默认关闭（COIN_RANK_GRAPH_SIGNAL_ENABLED=false）；开启后由 coin_rank.engine 的
rank_universe / rank_symbols 计算并注入 score_rows（权重 COIN_RANK_GRAPH_WEIGHT）。
任何数据缺失/异常 → 返回空 dict，绝不阻塞主排序链（fail-open）。

背景：docs/_rtgnn/RTGNN 论文迁移方案 §4.5；通过后为 P1 深度学习版立项提供数据支撑。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# 模块级 TTL 缓存：key → (expire_ts, signals)
_CACHE: Dict[tuple, Tuple[float, Dict[str, Dict[str, float]]]] = {}


# ─────────────────────────────────────────────────────────────
# 配置读取（懒加载，settings 不可用时用缺省，绝不抛错）
# ─────────────────────────────────────────────────────────────
def graph_signal_enabled() -> bool:
    try:
        from backend.config.settings import COIN_RANK_GRAPH_SIGNAL_ENABLED

        return bool(COIN_RANK_GRAPH_SIGNAL_ENABLED)
    except Exception:
        import os

        return os.getenv("COIN_RANK_GRAPH_SIGNAL_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def _cfg() -> Dict[str, Any]:
    """settings 优先，env/缺省兜底。"""
    import os

    defaults: Dict[str, Any] = {
        "lead_period": os.getenv("COIN_RANK_GRAPH_LEAD_PERIOD", "15m"),
        "mom_period": os.getenv("COIN_RANK_GRAPH_MOM_PERIOD", "4h"),
        "lead_count": int(os.getenv("COIN_RANK_GRAPH_LEAD_COUNT", "288") or "288"),
        "mom_count": int(os.getenv("COIN_RANK_GRAPH_MOM_COUNT", "48") or "48"),
        "max_lag": int(os.getenv("COIN_RANK_GRAPH_MAX_LAG", "6") or "6"),
        "anchors": tuple(
            s.strip().upper()
            for s in os.getenv("COIN_RANK_GRAPH_ANCHORS", "BTC,ETH").split(",")
            if s.strip()
        ),
        "ttl_sec": int(os.getenv("COIN_RANK_GRAPH_TTL_SEC", "600") or "600"),
        "min_bars": int(os.getenv("COIN_RANK_GRAPH_MIN_BARS", "60") or "60"),
    }
    try:
        from backend.config import settings as s

        for key in list(defaults):
            attr = "COIN_RANK_GRAPH_" + key.upper()
            if hasattr(s, attr):
                defaults[key] = getattr(s, attr)
        if hasattr(s, "COIN_RANK_GRAPH_ANCHORS"):
            _a = getattr(s, "COIN_RANK_GRAPH_ANCHORS")
            if isinstance(_a, str):
                defaults["anchors"] = tuple(x.strip().upper() for x in _a.split(",") if x.strip())
            else:
                defaults["anchors"] = tuple(str(x).upper() for x in (_a or ()))
    except Exception:
        pass
    return defaults


# ─────────────────────────────────────────────────────────────
# 基础工具
# ─────────────────────────────────────────────────────────────
def _zscore(x: np.ndarray) -> np.ndarray:
    mu = float(np.nanmean(x))
    sd = float(np.nanstd(x))
    if not np.isfinite(sd) or sd < 1e-12:
        return x * 0.0
    return (x - mu) / sd


def _returns(rows: List[Dict[str, Any]]) -> np.ndarray:
    """close 序列 → pct_change 收益（剔除 NaN/Inf）。"""
    closes = np.asarray(
        [float(r.get("close") or r.get("close_price") or 0.0) for r in (rows or [])],
        dtype=float,
    )
    if len(closes) < 3:
        return np.asarray([], dtype=float)
    mask = np.isfinite(closes) & (closes > 0)
    closes = closes[mask]
    if len(closes) < 3:
        return np.asarray([], dtype=float)
    ret = np.diff(closes) / closes[:-1]
    ret = ret[np.isfinite(ret)]
    return ret


def _fetch_klines(symbols: List[str], period: str, count: int) -> Dict[str, np.ndarray]:
    """批量取 K 线并转收益序列 {SYM: returns[]}；research 用途（只读深历史，不做新鲜度硬拒）。"""
    out: Dict[str, np.ndarray] = {}
    if not symbols:
        return out
    try:
        from backend.services.data_center import data_center

        batch = data_center.get_klines_batch(
            [str(s).upper() for s in symbols],
            period,
            count=count,
            purpose="research",
        )
        for su, result in (batch or {}).items():
            try:
                rows = getattr(result, "rows", None)
                if rows is None:
                    df = getattr(result, "to_dataframe", lambda: None)()
                    rows = df.reset_index().to_dict("records") if df is not None and len(df) else []
                ret = _returns(rows)
                if len(ret) >= 12:
                    out[str(su).upper()] = ret
            except Exception as e:
                logger.debug("[GraphSignal] %s/%s 取数失败: %s", su, period, e)
    except Exception as e:
        logger.warning("[GraphSignal] 批量取 K 线失败（fail-open）: %s", e)
    return out


# ─────────────────────────────────────────────────────────────
# 信号 1：领先-滞后强度分
# ─────────────────────────────────────────────────────────────
def _lead_vs_anchor(anchor_ret: np.ndarray, sym_ret: np.ndarray, max_lag: int, min_bars: int) -> float:
    """anchor 领先 sym 的强度 = mean_{ℓ∈[1..max_lag]} ρ(sym_t, anchor_{t−ℓ}) − ρ(sym_t, anchor_t)。

    用滞后均值而非 max：max 跨 6 个滞后做多重比较会把独立噪声抬高约
    0.1~0.2（max 偏差），均值形式对无结构序列近似无偏（差的标准误 ≈
    1/√n·√(1+1/K)），且对真实领先（多个滞后持续相关）同样敏感。
    """
    if len(anchor_ret) < min_bars or len(sym_ret) < min_bars:
        return 0.0
    n = min(len(anchor_ret), len(sym_ret))
    if n < min_bars:
        return 0.0
    a = _zscore(anchor_ret[-n:])
    s = _zscore(sym_ret[-n:])
    tail = n - max_lag
    c0 = float(np.dot(s[max_lag:], a[max_lag:])) / max(1, tail)
    acc = 0.0
    for lag in range(1, max_lag + 1):
        c = float(np.dot(s[lag:], a[:-lag])) / max(1, n - lag)
        if np.isfinite(c):
            acc += c
    best = acc / max_lag
    return max(0.0, best - c0)


def lead_lag_scores(
    symbols: List[str],
    period: Optional[str] = None,
    count: Optional[int] = None,
    anchors: Optional[Tuple[str, ...]] = None,
    max_lag: Optional[int] = None,
    min_bars: Optional[int] = None,
) -> Dict[str, float]:
    """{SYM: lead_score∈[0,1]}；数据不足返回 {}。"""
    cfg = _cfg()
    period = period or str(cfg["lead_period"])
    count = int(count or cfg["lead_count"])
    anchors = tuple(str(a).upper() for a in (anchors or cfg["anchors"]))
    max_lag = int(max_lag or cfg["max_lag"])
    min_bars = int(min_bars or cfg["min_bars"])

    want = [str(s).upper() for s in symbols if s]
    if not want or not anchors:
        return {}

    fetched = _fetch_klines(want, period, count)
    if not fetched:
        return {}
    anchor_ret = {a: fetched[a] for a in anchors if a in fetched}
    if not anchor_ret:
        return {}

    raw: Dict[str, float] = {}
    for sym, sret in fetched.items():
        if sym in anchors:
            continue
        vals = [
            _lead_vs_anchor(aret, sret, max_lag, min_bars)
            for aret in anchor_ret.values()
        ]
        raw[sym] = max(vals) if vals else 0.0

    if not raw:
        return {}
    # 截面 min-max 归一化（对锚的领先强度做相对排序，与 score.py 百分位哲学一致）
    vmin = min(raw.values())
    vmax = max(raw.values())
    if vmax - vmin < 1e-12:
        return {s: 0.0 for s in raw}
    return {s: (v - vmin) / (vmax - vmin) for s, v in raw.items()}


# ─────────────────────────────────────────────────────────────
# 信号 2：动量加速度分
# ─────────────────────────────────────────────────────────────
def momentum_delta_ranks(
    symbols: List[str],
    period: Optional[str] = None,
    count: Optional[int] = None,
) -> Dict[str, float]:
    """{SYM: dm_score∈[0,1]}（Δm 截面百分位排名）；数据不足返回 {}。"""
    cfg = _cfg()
    period = period or str(cfg["mom_period"])
    count = int(count or cfg["mom_count"])

    want = [str(s).upper() for s in symbols if s]
    if not want:
        return {}

    fetched = _fetch_klines(want, period, count)
    if not fetched:
        return {}

    raw: Dict[str, float] = {}
    for sym, ret in fetched.items():
        # M(t)=最近 W 根动量；Δm = M(t) − M(t−1)（前 W 根），非重叠窗口的干净差分。
        # W=min(12, max(3, len/4))：4h 档 count=48 → W=12 =「近 2 天 vs 前 2 天」。
        w = min(12, max(3, len(ret) // 4))
        if len(ret) < 2 * w:
            if len(ret) < 6:
                continue
            w = 3
            if len(ret) < 2 * w:
                continue
        m_now = float(np.mean(ret[-w:]))
        m_prev = float(np.mean(ret[-2 * w:-w]))
        raw[sym] = m_now - m_prev

    if not raw:
        return {}
    vals = sorted(raw.values())
    n = len(vals)
    if n <= 1:
        return {s: 0.5 for s in raw}
    order = sorted(raw, key=lambda k: raw[k])
    ranks = {s: i / (n - 1) for i, s in enumerate(order)}
    return ranks


# ─────────────────────────────────────────────────────────────
# 统一入口（带 TTL 缓存）
# ─────────────────────────────────────────────────────────────
def compute_graph_signals(
    symbols: List[str],
    *,
    force: bool = False,
) -> Dict[str, Dict[str, float]]:
    """{SYM: {"lead": float, "dm": float}}；未启用/数据不足/异常 → {}。

    结果缓存 TTL=COIN_RANK_GRAPH_TTL_SEC（默认 600s），避免每次选币循环重复取数。
    """
    if not graph_signal_enabled():
        return {}

    cfg = _cfg()
    want = [str(s).upper() for s in symbols if s]
    if not want:
        return {}
    key = tuple(sorted(set(want)))
    now = time.time()
    if not force:
        hit = _CACHE.get(key)
        if hit and hit[0] > now:
            return dict(hit[1])

    try:
        lead = lead_lag_scores(want)
        dm = momentum_delta_ranks(want)
    except Exception as e:
        logger.warning("[GraphSignal] 图信号计算失败（fail-open）: %s", e)
        return {}

    merged: Dict[str, Dict[str, float]] = {}
    # 仅对有 lead 信号的币发条目（锚与数据不足者不调整——锚不参与相对排序）；
    # dm 缺失时用中性 0.5（未知加速度不惩罚）。
    for sym in lead:
        merged[sym] = {
            "lead": float(lead.get(sym, 0.0)),
            "dm": float(dm.get(sym, 0.5)),
        }
    _CACHE[key] = (now + float(cfg["ttl_sec"]), merged)
    if merged:
        logger.info("[GraphSignal] 图信号就绪: %d 币（lead/dd）", len(merged))
    return merged


def clear_cache() -> None:
    """测试/手工重算用。"""
    _CACHE.clear()


# ─────────────────────────────────────────────────────────────
# 成对领先-滞后冗余矩阵（P3 universe 方向感知去重用）
# ─────────────────────────────────────────────────────────────
def pairwise_lead_matrix(
    returns_by_symbol: Dict[str, Any],
    max_lag: int = 6,
    min_bars: int = 60,
) -> "Any":  # pd.DataFrame
    """对称冗余矩阵 M[i,j] = max(lead(i→j), lead(j→i)) ∈ [0,∞)。

    输入 {SYM: returns[]}；输出 pandas DataFrame（行列 = symbols，缺失数据为 NaN）。
    方向由 _lead_vs_anchor 的均值滞后相关给出：A 强领先 B → M[A,B] 大。
    供 universe_manager._step4 做"领先-滞后冗余"冲突判据（P3）。
    """
    import pandas as pd

    syms = [str(s).upper() for s in returns_by_symbol if s]
    n = len(syms)
    mat = pd.DataFrame(index=syms, columns=syms, dtype=float)
    for i in range(n):
        si = np.asarray(returns_by_symbol[syms[i]], dtype=float)
        for j in range(i + 1, n):
            sj = np.asarray(returns_by_symbol[syms[j]], dtype=float)
            v = max(
                _lead_vs_anchor(sj, si, max_lag, min_bars),
                _lead_vs_anchor(si, sj, max_lag, min_bars),
            )
            mat.loc[syms[i], syms[j]] = v
            mat.loc[syms[j], syms[i]] = v
    return mat
