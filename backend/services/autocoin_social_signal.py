"""社交热度信号:从 CoinGecko trending API 获取当前热门币种。

阶段D 新数据维度:作为评分 bonus(最高 +15%)注入 AutoCoin 选币流水线,
补齐 volume/momentum/funding/volatility/trend 之外的「社交热度」前瞻信号。

数据源
------
CoinGecko 免费 trending endpoint(无需 API key):
    GET https://api.coingecko.com/api/v3/search/trending
返回 CoinGecko 上当前搜索量最高的币种(零售兴趣/社交热度代理)。

设计要点
--------
* 5 分钟本地缓存(_CACHE_TTL),避免高频扫描打爆公共 API
* 失败时返回 stale 缓存(而非空 dict),保证流水线不被外部故障打断
* 只暴露 ``get_social_score(symbol)`` 给上层,内部懒加载 + 共享缓存
"""
from __future__ import annotations

import logging
import time
from typing import Dict, Tuple

import requests

_log = logging.getLogger(__name__)

# (写入时间, {SYMBOL: score}) —— 模块级共享缓存
_CACHE: Tuple[float, Dict[str, float]] = (0.0, {})
_CACHE_TTL = 300  # 5 min —— CoinGecko trending 更新慢,5min 足够


def fetch_trending_scores() -> Dict[str, float]:
    """返回 {symbol: score} —— 当前 CoinGecko 热门币,按排名递减打分。

    评分规则
    --------
    第 1 名 = 1.0, 第 2 名 = 0.95, ... 线性递减 0.05/名次;
    最低保底 0.3(进榜即有意义,但不喧宾夺主);
    不在榜单 = 0.0(由 get_social_score 兜底返回)。

    Returns
    -------
    Dict[str, float]
        大写 symbol → 0.3~1.0 热度分;缓存命中或拉取失败时返回上次结果。
    """
    global _CACHE
    now = time.time()
    if now - _CACHE[0] < _CACHE_TTL and _CACHE[1]:
        return _CACHE[1]
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/search/trending",
            timeout=10,
            headers={"accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        coins = data.get("coins", [])
        scores: Dict[str, float] = {}
        for i, item in enumerate(coins[:15]):
            coin_data = item.get("item", {})
            symbol = (coin_data.get("symbol") or "").upper()
            if symbol:
                # Rank 1 = 1.0, rank 15 = ~0.3, 线性递减
                scores[symbol] = max(0.3, 1.0 - i * 0.05)
        _CACHE = (now, scores)
        _log.info(
            f"[SocialSignal] CoinGecko trending: {len(scores)} coins, "
            f"top={list(scores.items())[:3]}"
        )
        return scores
    except Exception as e:
        _log.warning(f"[SocialSignal] CoinGecko trending fetch failed: {e}")
        return _CACHE[1]  # 返回 stale 缓存而非空,保证流水线稳定


def get_social_score(symbol: str) -> float:
    """获取单个币种的社交热度分。

    Parameters
    ----------
    symbol : str
        交易对 symbol(大小写不敏感,内部统一大写)。

    Returns
    -------
    float
        0.0 = 不在热门榜;0.3~1.0 = 在榜(排名越高分越高)。
    """
    scores = fetch_trending_scores()
    return scores.get(symbol.upper(), 0.0)


def reset_cache_for_test() -> None:
    """单测专用:清空模块缓存,让下次 fetch 必走真实/mock 的 requests。"""
    global _CACHE
    _CACHE = (0.0, {})
