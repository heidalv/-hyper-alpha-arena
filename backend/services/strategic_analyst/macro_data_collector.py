"""
Strategic Analyst - 宏观数据采集器

6个免费数据源适配器：
1. FearGreedAdapter    - alternative.me API (恐贪指数)
2. BTCDominanceAdapter - CoinGecko API (BTC市值占比/加密总市值)
3. DXYAdapter          - Yahoo Finance / yfinance (美元指数)
4. SPXAdapter          - Yahoo Finance / yfinance (S&P500)
5. CSI300Adapter       - Yahoo Finance / yfinance (沪深300)
6. FedRateAdapter      - Yahoo Finance / yfinance (国债利率代理)

设计要点：
- 使用 httpx + yfinance 双通道
- 每个适配器失败时返回 None，不阻塞整体流水线
- 缓存 TTL=3600s，避免频繁请求
"""

import logging
import time
import threading
from typing import Dict, Optional
from datetime import datetime, timedelta

import httpx

from .models import MacroSnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
_cache: Dict[str, tuple] = {}       # key -> (data, timestamp)
_cache_lock = threading.Lock()
CACHE_TTL = 3600  # 1小时缓存


def _get_cached(key: str):
    with _cache_lock:
        if key in _cache:
            data, ts = _cache[key]
            if time.time() - ts < CACHE_TTL:
                return data
    return None


def _set_cached(key: str, data):
    with _cache_lock:
        _cache[key] = (data, time.time())
    return data


# ---------------------------------------------------------------------------
# 适配器基类
# ---------------------------------------------------------------------------
class _BaseAdapter:
    """适配器基类"""
    name: str = "base"

    def fetch(self) -> Optional[Dict]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 1. Fear & Greed Index (alternative.me, 免费)
# ---------------------------------------------------------------------------
class FearGreedAdapter(_BaseAdapter):
    """恐贪指数适配器 - alternative.me API"""
    name = "fear_greed"
    URL = "https://api.alternative.me/fng/?limit=1"

    def fetch(self) -> Optional[Dict]:
        cached = _get_cached(self.name)
        if cached is not None:
            return cached
        try:
            resp = httpx.get(self.URL, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("data"):
                    val = int(data["data"][0]["value"])
                    return _set_cached(self.name, {
                        "fear_greed_index": float(val),
                        "fear_greed_label": data["data"][0].get("value_classification", ""),
                    })
        except Exception as e:
            logger.warning(f"[FearGreedAdapter] 采集失败: {e}")
        return None


# ---------------------------------------------------------------------------
# 2. BTC Dominance + Crypto Market Cap (CoinGecko, 免费)
# ---------------------------------------------------------------------------
class BTCDominanceAdapter(_BaseAdapter):
    """BTC市值占比 + 加密总市值 - CoinGecko API"""
    name = "btc_dominance"
    URL = "https://api.coingecko.com/api/v3/global"

    def fetch(self) -> Optional[Dict]:
        cached = _get_cached(self.name)
        if cached is not None:
            return cached
        try:
            resp = httpx.get(self.URL, timeout=10, headers={"Accept": "application/json"})
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                return _set_cached(self.name, {
                    "btc_dominance": data.get("market_cap_percentage", {}).get("btc"),
                    "crypto_market_cap": data.get("total_market_cap", {}).get("usd"),
                })
        except Exception as e:
            logger.warning(f"[BTCDominanceAdapter] 采集失败: {e}")
        return None


# ---------------------------------------------------------------------------
# 3-5. Yahoo Finance 适配器 (DXY, SPX, CSI300)
# ---------------------------------------------------------------------------
def _fetch_yfinance(ticker: str, period: str = "5d") -> Optional[Dict]:
    """通用 yfinance 数据获取"""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period=period)
        if hist.empty or len(hist) < 2:
            return None
        latest = hist.iloc[-1]
        prev = hist.iloc[-2]
        close = float(latest["Close"])
        prev_close = float(prev["Close"])
        change_pct = (close - prev_close) / prev_close * 100 if prev_close else 0.0
        return {
            "close": close,
            "change_pct": change_pct,
        }
    except ImportError:
        logger.debug("[yfinance] 未安装，跳过 Yahoo Finance 数据源")
        return None
    except Exception as e:
        logger.warning(f"[yfinance] 获取 {ticker} 失败: {e}")
        return None


class DXYAdapter(_BaseAdapter):
    """美元指数 - Yahoo Finance"""
    name = "dxy"

    def fetch(self) -> Optional[Dict]:
        cached = _get_cached(self.name)
        if cached is not None:
            return cached
        data = _fetch_yfinance("DX-Y.NYB", period="5d")
        if data:
            return _set_cached(self.name, {
                "dxy_value": data["close"],
                "dxy_change_pct": data["change_pct"],
            })
        return None


class SPXAdapter(_BaseAdapter):
    """S&P500 指数 - Yahoo Finance"""
    name = "spx"

    def fetch(self) -> Optional[Dict]:
        cached = _get_cached(self.name)
        if cached is not None:
            return cached
        data = _fetch_yfinance("^GSPC", period="5d")
        if data:
            return _set_cached(self.name, {
                "spx_close": data["close"],
                "spx_change_pct": data["change_pct"],
            })
        return None


class CSI300Adapter(_BaseAdapter):
    """沪深300指数 - Yahoo Finance"""
    name = "csi300"

    def fetch(self) -> Optional[Dict]:
        cached = _get_cached(self.name)
        if cached is not None:
            return cached
        data = _fetch_yfinance("000300.SS", period="5d")
        if data:
            return _set_cached(self.name, {
                "csi300_close": data["close"],
                "csi300_change_pct": data["change_pct"],
            })
        return None


# ---------------------------------------------------------------------------
# 6. Fed Funds Rate (用13周国债利率代理)
# ---------------------------------------------------------------------------
class FedRateAdapter(_BaseAdapter):
    """联邦基金利率代理 - 13周国债利率 (^IRX)"""
    name = "fed_rate"

    def fetch(self) -> Optional[Dict]:
        cached = _get_cached(self.name)
        if cached is not None:
            return cached
        data = _fetch_yfinance("^IRX", period="5d")
        if data:
            return _set_cached(self.name, {
                "fed_funds_rate": data["close"],  # 国债利率 % 作为代理
            })
        return None


# ---------------------------------------------------------------------------
# 宏观数据采集器主类
# ---------------------------------------------------------------------------
class MacroDataCollector:
    """
    宏观数据采集器
    并行调用所有适配器，聚合为 MacroSnapshot
    """

    def __init__(self):
        self.adapters = [
            FearGreedAdapter(),
            BTCDominanceAdapter(),
            DXYAdapter(),
            SPXAdapter(),
            CSI300Adapter(),
            FedRateAdapter(),
        ]

    def fetch_all(self) -> MacroSnapshot:
        """
        采集所有宏观数据，聚合为 MacroSnapshot
        失败的适配器不影响其他适配器
        """
        snapshot = MacroSnapshot(timestamp=datetime.utcnow())
        sources_status = {}

        for adapter in self.adapters:
            try:
                result = adapter.fetch()
                if result:
                    # 将结果映射到 snapshot 字段
                    for key, value in result.items():
                        if hasattr(snapshot, key):
                            setattr(snapshot, key, value)
                    sources_status[adapter.name] = "ok"
                else:
                    sources_status[adapter.name] = "no_data"
            except Exception as e:
                sources_status[adapter.name] = f"error: {e}"
                logger.warning(f"[MacroDataCollector] {adapter.name} 异常: {e}")

        snapshot.data_sources_status = sources_status

        # 计算数据质量评分
        ok_count = sum(1 for v in sources_status.values() if v == "ok")
        snapshot_data_quality = ok_count / max(len(self.adapters), 1)

        logger.info(
            f"[MacroDataCollector] 采集完成: {ok_count}/{len(self.adapters)} 成功, "
            f"DXY={snapshot.dxy_value}, SPX={snapshot.spx_change_pct}%, "
            f"FG={snapshot.fear_greed_index}"
        )

        return snapshot
