"""真实资金费率数据源（从 perp_funding 读最新各场所费率）。

2026-07-06 完善：
    SDN / funding_rate_matrix 需要 {exchange: {symbol: rate}} 形状的**每场所**资金费。
    但 tick_context.fetch_funding_rates 在本环境（无交易所客户端）要么返回空，要么走
    opportunity_scanner 兜底返回**扁平** {symbol: rate}（跨所聚合，无法拆分场所）。

    本模块从 `perp_funding` 表读**真实历史资金费的最新快照**，按 (exchange, symbol) 归组，
    产出 {exchange: {normalized_symbol: hourly_rate}}——这是 delta-neutral 资金费矩阵真正
    需要的输入。数据真实、离线可用、随新场所数据入库自动扩展覆盖。

    诚实说明：当前 perp_funding 仅有 hyperliquid 单场所数据，故矩阵通常凑不齐两条腿；
    这正确地让 SDN 判 not viable（而非臆造机会）。一旦第二个场所资金费入库即自动生效。

symbol 归一：perp_funding 存 "BTC" 这类基础符号，统一成 "BTC/USDT" 便于跨场所配对。
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 只用"足够新"的资金费快照（默认 12h 内），避免拿陈旧费率当当前值。
DEFAULT_MAX_AGE_HOURS = 12.0

# 轻量缓存，避免每次扫描都打 DB（默认 60s）。
_cache: Dict[str, object] = {"ts": 0.0, "data": {}}
_CACHE_TTL_SECONDS = 60.0


def _normalize_symbol(symbol: str) -> str:
    """把 perp_funding 的符号（如 'BTC' / 'BTC-USDT'）统一为 'BTC/USDT'。"""
    raw = (symbol or "").strip().upper()
    if not raw:
        return ""
    if "/" in raw:
        return raw
    base = raw.split("-")[0].replace("USDT", "").strip() or raw
    return f"{base}/USDT"


def latest_funding_by_venue(
    symbols: Optional[List[str]] = None,
    *,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    use_cache: bool = True,
) -> Dict[str, Dict[str, float]]:
    """读取各场所每 symbol 的最新资金费率（小时费率）。

    Returns: {exchange: {normalized_symbol: funding_rate}}，无数据时返回 {}。
    """
    now = time.time()
    if use_cache and (now - float(_cache["ts"])) < _CACHE_TTL_SECONDS:
        cached = _cache["data"]
        if isinstance(cached, dict):
            return _filter_symbols(cached, symbols)  # type: ignore[arg-type]

    result: Dict[str, Dict[str, float]] = {}
    try:
        from sqlalchemy import text

        from backend.database.connection import MarketSessionLocal

        cutoff_ms = int((now - max_age_hours * 3600) * 1000)
        db = MarketSessionLocal()
        try:
            # 每个 (exchange, symbol) 取 timestamp 最大的一行（最新快照）
            rows = db.execute(
                text(
                    """
                    SELECT pf.exchange, pf.symbol, pf.funding_rate
                    FROM perp_funding pf
                    JOIN (
                        SELECT exchange, symbol, MAX(timestamp) AS mx
                        FROM perp_funding
                        WHERE timestamp >= :cutoff
                        GROUP BY exchange, symbol
                    ) latest
                      ON pf.exchange = latest.exchange
                     AND pf.symbol = latest.symbol
                     AND pf.timestamp = latest.mx
                    """
                ),
                {"cutoff": cutoff_ms},
            ).fetchall()
            for r in rows:
                exchange = str(r[0]).lower()
                sym = _normalize_symbol(str(r[1]))
                if not sym:
                    continue
                result.setdefault(exchange, {})[sym] = float(r[2])
        finally:
            db.close()
    except Exception as exc:
        logger.debug("[FundingProvider] 读取 perp_funding 失败: %s", exc)
        return {}

    _cache["ts"] = now
    _cache["data"] = result
    return _filter_symbols(result, symbols)


def _filter_symbols(
    data: Dict[str, Dict[str, float]], symbols: Optional[List[str]]
) -> Dict[str, Dict[str, float]]:
    if not symbols:
        return {ex: dict(m) for ex, m in data.items()}
    wanted = {_normalize_symbol(s) for s in symbols}
    out: Dict[str, Dict[str, float]] = {}
    for ex, m in data.items():
        filtered = {s: r for s, r in m.items() if s in wanted}
        if filtered:
            out[ex] = filtered
    return out


def hold_funding_pnl(
    net_funding_per_day: float,
    notional_usd: float,
    elapsed_seconds: float,
) -> float:
    """持仓期资金费盈亏（delta-neutral：空腿收 - 长腿付，正=净收益）。

    net_funding_per_day 来自 funding_rate_matrix（占单腿名义的每日比例）。
    这是 delta-neutral 刷分的经济核心——两腿价格波动相互抵消，真正的收益来自
    持有期内积累的资金费价差。纯函数、无副作用，便于单测与在平仓时叠加进 PnL。
    """
    if notional_usd <= 0 or elapsed_seconds <= 0:
        return 0.0
    days = elapsed_seconds / 86400.0
    return net_funding_per_day * notional_usd * days


def has_multi_venue_coverage(data: Dict[str, Dict[str, float]]) -> bool:
    """是否至少有一个 symbol 在 >=2 个场所都有费率（能凑出 delta-neutral 双腿）。"""
    symbol_venues: Dict[str, int] = {}
    for _ex, m in data.items():
        for sym in m:
            symbol_venues[sym] = symbol_venues.get(sym, 0) + 1
    return any(c >= 2 for c in symbol_venues.values())
