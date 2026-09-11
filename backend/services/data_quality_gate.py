# -*- coding: utf-8 -*-
"""[F55] 数据质量闸 —— 交易决策前的最后一道过滤。

背景（2026-09-09 实测）：
  - `crypto_klines` 存在单根 **25025%** 的跳空（脏数据），会污染因子计算、
    三屏障结算与任何回测；
  - `perp_funding.timestamp` 是**毫秒**（非秒），任何按秒解释的代码会得到
    year 58212 这类荒谬日期；
  - 8.71% 的 5m bar 跳空 >40bp —— 这对以「止损距离」为单位的策略是致命的
    （止损被跳空穿透，模型里的精确成交是乐观的）。

本模块提供纯函数闸门，不依赖 DB/网络，便于单测与在任意链路复用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

# 单根 K 线跳空阈值：超过视为脏数据（正常 5m 波动远小于此）
DEFAULT_MAX_GAP_PCT = 0.10
# 时间戳合理上界（秒）：2100-01-01。下界取 0——闸门的目的是拦截**单位错误**
# （如把毫秒当秒 → year 58212）与负值/零，而不是强制业务时间范围，
# 否则合成测试数据与历史回放会被误杀。
_TS_MAX_SEC = 4_102_444_800


@dataclass(frozen=True)
class KlineIssue:
    """单根 K 线的问题描述。"""

    index: int
    timestamp: Optional[int]
    kind: str          # gap / high_lt_low / non_positive / bad_timestamp
    detail: str


def normalize_epoch_ms(ts: Any) -> Optional[int]:
    """把可能是秒或毫秒的时间戳统一成**毫秒**；无法判定时返回 None。

    判定规则：>= 1e11 视为毫秒（2000 年之后的毫秒值都 >= 9.46e11），
    否则视为秒并乘以 1000。0/负数/None → None。
    """
    try:
        v = int(float(ts))
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v if v >= 100_000_000_000 else v * 1000


def is_plausible_ts(ts_ms: Optional[int]) -> bool:
    """时间戳是否可解释（0 < 秒值 <= 2100-01-01）。

    只拦截单位错误/负值/零，不强制业务时间窗。
    """
    if ts_ms is None or ts_ms <= 0:
        return False
    return (ts_ms / 1000.0) <= _TS_MAX_SEC


def is_plausible_kline(
    o: float, h: float, l: float, c: float,
    prev_close: Optional[float] = None,
    max_gap_pct: float = DEFAULT_MAX_GAP_PCT,
) -> Tuple[bool, str]:
    """单根 K 线是否可用。返回 (ok, 原因)。"""
    try:
        o, h, l, c = float(o), float(h), float(l), float(c)
    except (TypeError, ValueError):
        return False, "non_numeric"
    if not all(x > 0 for x in (o, h, l, c)):
        return False, "non_positive"
    if h < l:
        return False, "high_lt_low"
    if not (l <= o <= h and l <= c <= h):
        return False, "close_out_of_range"
    if prev_close and prev_close > 0:
        gap = abs(o / float(prev_close) - 1.0)
        if gap > max_gap_pct:
            return False, f"gap_{gap*1e4:.0f}bp"
    return True, ""


def scan_kline_issues(
    rows: Iterable[Dict[str, Any]],
    max_gap_pct: float = DEFAULT_MAX_GAP_PCT,
) -> List[KlineIssue]:
    """扫描 K 线序列，返回问题列表。

    rows 每项需含 timestamp/open_price/high_price/low_price/close_price
    （兼容 o/h/l/c 短键）。
    """
    issues: List[KlineIssue] = []
    prev_close: Optional[float] = None
    for i, r in enumerate(rows):
        ts = normalize_epoch_ms(r.get("timestamp"))
        o = r.get("open_price", r.get("o"))
        h = r.get("high_price", r.get("h"))
        l = r.get("low_price", r.get("l"))
        c = r.get("close_price", r.get("c"))
        if not is_plausible_ts(ts):
            issues.append(KlineIssue(i, ts, "bad_timestamp", f"ts={r.get('timestamp')}"))
        ok, why = is_plausible_kline(o, h, l, c, prev_close, max_gap_pct)
        if not ok:
            issues.append(KlineIssue(i, ts, why.split("_")[0], why))
        try:
            prev_close = float(c)
        except (TypeError, ValueError):
            prev_close = None
    return issues


def filter_clean_klines(
    rows: Iterable[Dict[str, Any]],
    max_gap_pct: float = DEFAULT_MAX_GAP_PCT,
) -> List[Dict[str, Any]]:
    """返回剔除脏 bar 后的序列（保持原顺序）。"""
    out: List[Dict[str, Any]] = []
    prev_close: Optional[float] = None
    for r in rows:
        o = r.get("open_price", r.get("o"))
        h = r.get("high_price", r.get("h"))
        l = r.get("low_price", r.get("l"))
        c = r.get("close_price", r.get("c"))
        ok, _ = is_plausible_kline(o, h, l, c, prev_close, max_gap_pct)
        if ok:
            out.append(r)
            try:
                prev_close = float(c)
            except (TypeError, ValueError):
                prev_close = None
    return out


def gap_stats(rows: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    """跳空分布（bp），用于监控与告警阈值校准。"""
    gaps: List[float] = []
    prev_close: Optional[float] = None
    for r in rows:
        o = r.get("open_price", r.get("o"))
        c = r.get("close_price", r.get("c"))
        try:
            o, c = float(o), float(c)
        except (TypeError, ValueError):
            continue
        if prev_close and prev_close > 0:
            gaps.append(abs(o / prev_close - 1.0) * 1e4)
        prev_close = c
    if not gaps:
        return {"n": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    gaps.sort()
    n = len(gaps)
    pick = lambda q: gaps[min(n - 1, int(q * n))]  # noqa: E731
    return {
        "n": float(n),
        "p50": pick(0.50),
        "p95": pick(0.95),
        "p99": pick(0.99),
        "max": gaps[-1],
    }
