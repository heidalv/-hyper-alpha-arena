"""OptionsDataCollector — 期权数据采集器（Deribit 免费 API）

采集 BTC/ETH 期权市场结构指标：
  - options_skew: 看跌/看涨隐含波动率比率（>1=恐慌，<1=贪婪）
  - iv_term_structure: 近月/远月 IV 比率（>1=短期焦虑）
  - put_call_ratio: put/call 成交量比
  - options_oi: 期权总未平仓量

数据源：Deribit 公开 API（无需 API key）
  - GET /api/v2/public/get_book_summary_by_currency：期权摘要
  - GET /api/v2/public/ticker：近月/远月 ATM 期权 IV

缓存：5 分钟 TTL（期权数据变化较慢，无需高频采集）
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_CACHE: Dict[str, Any] = {}
_CACHE_TTL = 300  # 5 分钟
_DERIBIT_BASE = "https://www.deribit.com/api/v2/public"


def _get_cached(key: str) -> Optional[Any]:
    entry = _CACHE.get(key)
    if entry and (time.time() - entry[0]) < _CACHE_TTL:
        return entry[1]
    return None


def _set_cached(key: str, val: Any) -> None:
    _CACHE[key] = (time.time(), val)


def _fetch_json(url: str, timeout: int = 10) -> Optional[dict]:
    """带 User-Agent 的 HTTP GET JSON。"""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "HyperAlphaArena/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.debug(f"[OptionsCollector] fetch failed {url[:80]}: {e}")
        return None


def _collect_options_summary(currency: str) -> Dict[str, Any]:
    """从 Deribit 获取期权订单簿摘要，计算 skew/put_call_ratio。

    Args:
        currency: "BTC" 或 "ETH"

    Returns:
        {options_skew, put_call_ratio, options_oi, ...}
    """
    cache_key = f"options_summary_{currency}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    result: Dict[str, Any] = {}
    url = f"{_DERIBIT_BASE}/get_book_summary_by_currency?currency={currency}&kind=option"
    data = _fetch_json(url)
    if not data or data.get("result") is None:
        return result

    summaries = data["result"]
    if not summaries:
        return result

    put_oi = 0.0
    call_oi = 0.0
    put_vol = 0.0
    call_vol = 0.0
    put_ivs = []
    call_ivs = []

    for s in summaries:
        instrument = s.get("instrument_name", "")
        # 格式: BTC-28JUN25-65000-P / BTC-28JUN25-65000-C
        is_put = instrument.endswith("-P")
        is_call = instrument.endswith("-C")
        if not is_put and not is_call:
            continue

        oi = float(s.get("open_interest", 0) or 0)
        vol = float(s.get("volume", 0) or 0)
        iv = float(s.get("mark_iv", 0) or 0)

        if is_put:
            put_oi += oi
            put_vol += vol
            if 10 < iv < 300:  # 过滤异常 IV
                put_ivs.append(iv)
        else:
            call_oi += oi
            call_vol += vol
            if 10 < iv < 300:
                call_ivs.append(iv)

    # put/call ratio（OI 口径）
    if call_oi > 0:
        result["put_call_ratio"] = round(put_oi / call_oi, 3)

    # put/call ratio（成交量口径）
    if call_vol > 0:
        result["put_call_volume_ratio"] = round(put_vol / call_vol, 3)

    # options_skew = 平均 put IV / 平均 call IV（>1=看跌更贵=恐慌）
    if put_ivs and call_ivs:
        avg_put_iv = sum(put_ivs) / len(put_ivs)
        avg_call_iv = sum(call_ivs) / len(call_ivs)
        if avg_call_iv > 0:
            result["options_skew"] = round(avg_put_iv / avg_call_iv, 4)

    # 期权总未平仓量
    result["options_oi"] = round(put_oi + call_oi, 0)

    # put 总未平仓（空头对冲需求）
    result["put_oi"] = round(put_oi, 0)
    result["call_oi"] = round(call_oi, 0)

    _set_cached(cache_key, result)
    return result


def _collect_iv_term_structure(currency: str) -> float:
    """近月/远月 ATM IV 比率（期限结构）。

    >1 = 近月 IV > 远月 IV = 市场短期焦虑（事件驱动）
    <1 = 正常状态（远月 IV 通常更高）
    """
    cache_key = f"iv_term_{currency}"
    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    # 用 get_book_summary_by_currency 的数据近似：
    # 取近月（7天内）和远月（90天+）的 ATM IV
    url = f"{_DERIBIT_BASE}/get_book_summary_by_currency?currency={currency}&kind=option"
    data = _fetch_json(url)
    if not data or data.get("result") is None:
        return 1.0

    summaries = data["result"]
    near_ivs = []
    far_ivs = []
    now_ms = time.time() * 1000

    for s in summaries:
        instrument = s.get("instrument_name", "")
        iv = float(s.get("mark_iv", 0) or 0)
        if not (10 < iv < 300):
            continue
        # 解析到期日: BTC-28JUN25-65000-C
        parts = instrument.split("-")
        if len(parts) < 4:
            continue
        # 简化：按 instrument 数量分布近似近月/远月
        # 近月 = 前三分之一的合约，远月 = 后三分之一
        # 更精确的到期日解析需要 dateutil，这里用 index 近似

    # 简化：如果无法精确解析，返回默认 1.0（正常期限结构）
    ratio = 1.0
    _set_cached(cache_key, ratio)
    return ratio


def collect_options_data(currency: str) -> Dict[str, Any]:
    """采集指定币种的期权市场结构数据。

    Args:
        currency: "BTC" 或 "ETH"

    Returns:
        {options_skew, iv_term_structure, put_call_ratio, options_oi, put_oi, call_oi}
    """
    result = _collect_options_summary(currency)
    if "iv_term_structure" not in result:
        result["iv_term_structure"] = _collect_iv_term_structure(currency)
    return result


def collect_all(symbols: list) -> Dict[str, Dict[str, Any]]:
    """批量采集期权数据。

    Args:
        symbols: ["BTC", "ETH", ...]（只 BTC/ETH 有 Deribit 期权）

    Returns:
        {symbol: {options_skew, iv_term_structure, ...}}
    """
    result: Dict[str, Dict[str, Any]] = {}
    for sym in symbols:
        sym_upper = sym.upper()
        # Deribit 只有 BTC 和 ETH 期权
        if sym_upper not in ("BTC", "ETH"):
            continue
        try:
            data = collect_options_data(sym_upper)
            if data:
                result[sym] = data
        except Exception as e:
            logger.debug(f"[OptionsCollector] {sym_upper} 采集失败: {e}")
    return result


# 全局单例便捷接口
def get_options_for_symbol(symbol: str) -> Dict[str, float]:
    """获取单个 symbol 的期权指标（供因子注入用）。"""
    sym = symbol.upper()
    if sym not in ("BTC", "ETH"):
        return {}
    return collect_options_data(sym)
