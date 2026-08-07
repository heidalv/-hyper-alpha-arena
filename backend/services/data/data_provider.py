"""数据源抽象层（v6 阶段 2 · 2.3 数据源抽象层）。

目标：衍生品 / 链上数据的统一 DataProvider 接口，Coinglass 免费 → 付费
无缝切换（tier 由环境变量驱动，运行时也可 set_api_key 热切换）。

统一采集能力（落地顺序：funding/清算 → 链上净流入 → 稳定币）：
  - fetch_funding(symbol)            → 资金费率（8h 原始值）
  - fetch_liquidation(symbol)        → {long_usd, short_usd, total_usd}
  - fetch_netflow(asset)             → 交易所净流入（USD，流入为正）
  - fetch_stablecoin_mint(asset)     → 稳定币净铸造（USD）

设计原则：
  - 任何 fetch_* 失败一律返回 None / 空结构，绝不向调用方抛异常（采集不阻断）；
  - 每次调用记录成功/延迟/错误（内部统计），health() 输出统一健康视图，
    供 DataQualityMonitor 链路健康看板（前端三链路卡）消费；
  - ProviderChain 多 provider 按优先级降级：primary 失败自动切 fallback，
    "免费 → 付费无缝切换"通过给 CoinglassProvider.set_api_key() 热升级实现。

现状说明（2026-08-05）：
  - 生产衍生品路径 derivatives_analytics_service 已有 local→HL→Binance→Coinalyze
    四层免费源，本层作为第 5 层 Coinglass（funding/清算）接入；
  - 生产链上路径 onchain_data_collector 用本层补 exchange_net_flow /
    stablecoin_mint_burn 真实字段（此前缺失，因子层被过滤跳过）。
"""
from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ── 端点与 key 环境变量 ──────────────────────────────────────────────
# 付费 tier：open-api.coinglass.com（需 COINGLASS_API_KEY，Header: cg-api-key）
# 免费 tier：open-api-v4.coinglass.com（官方 V4 域名，需 COINGLASS_FREE_API_KEY，Header: CG-API-KEY）。
# [2026-08-06 2.2 实测] free-data.coinglass.com 无 TLS 服务（虚构域名，永远握手失败），
# 官方 V4 免费计划同样走 open-api-v4.coinglass.com（无有效 key 时端点统一返回 404 隐藏，
# 有免费 key 即正常返回数据）；COINGLASS_FREE_BASE_URL 仍可覆盖到第三方免费网关。
COINGLASS_PAID_BASE = "https://open-api.coinglass.com"
COINGLASS_FREE_BASE = "https://open-api-v4.coinglass.com"

# 免费 tier 也允许覆盖到第三方免费网关（部分地区免费端点不可达时）
ENV_PAID_KEY = "COINGLASS_API_KEY"
ENV_FREE_KEY = "COINGLASS_FREE_API_KEY"
ENV_PAID_BASE = "COINGLASS_BASE_URL"
ENV_FREE_BASE = "COINGLASS_FREE_BASE_URL"


@dataclass
class LiquidationData:
    """清算数据（近 N 小时）。"""
    long_usd: float = 0.0
    short_usd: float = 0.0
    total_usd: float = 0.0


@dataclass
class ProviderStats:
    """provider 调用统计（供 health()）。"""
    total_calls: int = 0
    success_calls: int = 0
    total_latency_ms: float = 0.0
    last_success: float = 0.0
    last_failure: float = 0.0
    last_error: str = ""
    last_error_ts: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.success_calls / max(self.total_calls, 1)

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / max(self.success_calls, 1)


class DataProvider(ABC):
    """统一数据源接口。tier ∈ {"free", "paid"}。"""

    name: str = "base"
    tier: str = "free"

    def __init__(self) -> None:
        self.stats = ProviderStats()

    # ── 统一采集能力（子类实现；失败返回 None，不抛异常） ──────────

    @abstractmethod
    def fetch_funding(self, symbol: str) -> Optional[float]:
        """资金费率。"""

    @abstractmethod
    def fetch_liquidation(self, symbol: str) -> Optional[LiquidationData]:
        """清算量。"""

    @abstractmethod
    def fetch_netflow(self, asset: str) -> Optional[float]:
        """交易所净流入（USD，流入为正）。"""

    @abstractmethod
    def fetch_stablecoin_mint(self, asset: str) -> Optional[float]:
        """稳定币净铸造（USD）。"""

    # ── 统一健康视图 ──────────────────────────────────────────────

    def health(self) -> Dict[str, Any]:
        s = self.stats
        return {
            "name": self.name,
            "tier": self.tier,
            "ok": s.total_calls == 0 or s.success_rate >= 0.7,
            "total_calls": s.total_calls,
            "success_rate": round(s.success_rate, 3),
            "avg_latency_ms": round(s.avg_latency_ms, 1),
            "last_success": s.last_success,
            "last_failure": s.last_failure,
            "last_error": s.last_error,
            "last_error_ts": s.last_error_ts,
        }

    # ── 内部：调用包装（统一计时 + 统计 + 容错） ──────────────────

    def _call(self, fn) -> Any:
        """执行一次带统计的调用；异常转 None，绝不外抛。"""
        t0 = time.time()
        try:
            val = fn()
            self.stats.total_calls += 1
            self.stats.success_calls += 1
            self.stats.total_latency_ms += (time.time() - t0) * 1000
            self.stats.last_success = time.time()
            return val
        except Exception as e:  # noqa: BLE001 — 数据采集容错边界
            self.stats.total_calls += 1
            self.stats.last_failure = time.time()
            self.stats.last_error = f"{type(e).__name__}: {e}"[:300]
            self.stats.last_error_ts = time.time()
            logger.debug(f"[DataProvider:{self.name}] {fn.__name__} 失败: {e}")
            return None


class CoinglassProvider(DataProvider):
    """Coinglass 数据源（免费 tier → 付费 tier 无缝切换）。

    tier 判定（构造时）：有 COINGLASS_API_KEY → paid；否则 → free
    （free tier 也允许无 key，主端点会失败降级返回 None，接口保持可用）。
    set_api_key(key, tier="paid") 可运行时热升级（如用户在配置页填入付费 key），
    之后所有请求自动走付费端点，无需重启——即"免费 → 付费无缝切换"。
    """

    name = "coinglass"

    def __init__(self) -> None:
        super().__init__()
        self._paid_key: str = os.getenv(ENV_PAID_KEY, "").strip()
        self._free_key: str = os.getenv(ENV_FREE_KEY, "").strip()
        self._paid_base: str = os.getenv(ENV_PAID_BASE, COINGLASS_PAID_BASE).rstrip("/")
        self._free_base: str = os.getenv(ENV_FREE_BASE, COINGLASS_FREE_BASE).rstrip("/")
        self.tier = "paid" if self._paid_key else "free"
        logger.info(
            "[DataProvider] CoinglassProvider tier=%s (paid_key=%s, free_key=%s, "
            "paid_base=%s, free_base=%s)",
            self.tier, bool(self._paid_key), bool(self._free_key),
            self._paid_base, self._free_base,
        )

    # ── tier / key 管理 ───────────────────────────────────────────

    @property
    def has_key(self) -> bool:
        return bool(self._paid_key or self._free_key)

    def set_api_key(self, key: str, tier: str = "paid") -> None:
        """运行时热切换 API key（免费 → 付费无缝升级）。"""
        key = (key or "").strip()
        if tier == "paid" and key:
            self._paid_key = key
            self.tier = "paid"
        elif tier == "free" and key:
            self._free_key = key
            if not self._paid_key:
                self.tier = "free"
        logger.info("[DataProvider] CoinglassProvider key 已更新 → tier=%s", self.tier)

    # ── 内部 HTTP ─────────────────────────────────────────────────

    def _client(self) -> httpx.Client:
        # [2026-08-06 2.2] 复用 Coinalyze 同款代理（BINANCE_HTTPS_PROXY，.env 已配
        # 127.0.0.1:1080）：免费端点 free-data.coinglass.com 在无代理网络下
        # DNS 解析失败（getaddrinfo failed），与 Coinalyze 相同的网络环境处理。
        proxy = (
            os.getenv("BINANCE_HTTPS_PROXY")
            or os.getenv("HTTPS_PROXY")
            or os.getenv("https_proxy")
            or None
        )
        return httpx.Client(timeout=10.0, proxy=proxy)

    def _headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {}
        if self.tier == "paid" and self._paid_key:
            h["cg-api-key"] = self._paid_key
        elif self._free_key:
            # [2026-08-06 2.2] 默认免费端点为官方 V4 → 用 CG-API-KEY；
            # 若 COINGLASS_FREE_BASE_URL 覆盖到第三方网关（非 V4 域名）→ 兼容 api-key。
            h["CG-API-KEY" if "open-api-v4" in self._base() else "api-key"] = self._free_key
        return h

    def _base(self) -> str:
        return self._paid_base if self.tier == "paid" else self._free_base

    def _get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """GET 并解析 JSON；非 200 / 结构异常抛异常（由 _call 兜底）。"""
        with self._client() as client:
            r = client.get(f"{self._base()}{path}", params=params, headers=self._headers())
            r.raise_for_status()
            return r.json()

    @staticmethod
    def _num(v: Any, default: float = 0.0) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    # ── 统一采集能力 ──────────────────────────────────────────────

    def fetch_funding(self, symbol: str) -> Optional[float]:
        def _fn() -> float:
            data = self._get_json("/api/futures/funding_rate/ohlc-history", {
                "symbol": symbol.upper(), "interval": "1h", "limit": 1,
            })
            rows = data.get("data") if isinstance(data, dict) else None
            if not rows:
                raise ValueError(f"empty funding rows for {symbol}")
            return self._num(rows[-1].get("fundingRate") if isinstance(rows[-1], dict) else None)
        val = self._call(_fn)
        return val

    def fetch_liquidation(self, symbol: str) -> Optional[LiquidationData]:
        def _fn() -> LiquidationData:
            data = self._get_json("/api/futures/liquidation/v2/market-chart", {
                "exchange": "BINANCE", "symbol": symbol.upper(),
                "interval": "1h", "limit": 24,
            })
            rows = data.get("data") if isinstance(data, dict) else None
            if not rows:
                raise ValueError(f"empty liquidation rows for {symbol}")
            long_u = sum(self._num(r.get("longVolUsd") if isinstance(r, dict) else 0) for r in rows)
            short_u = sum(self._num(r.get("shortVolUsd") if isinstance(r, dict) else 0) for r in rows)
            return LiquidationData(long_usd=long_u, short_usd=short_u, total_usd=long_u + short_u)
        return self._call(_fn)

    def fetch_netflow(self, asset: str) -> Optional[float]:
        def _fn() -> float:
            data = self._get_json("/api/futures/exchange-flow/balance-history", {
                "exchange": "BINANCE", "symbol": asset.upper(),
                "interval": "1h", "limit": 24,
            })
            rows = data.get("data") if isinstance(data, dict) else None
            if not rows:
                raise ValueError(f"empty netflow rows for {asset}")
            # 净流入 = 最近余额 − 窗口起点余额（余额上升 = 资金流入交易所 = 净流入）
            head = self._num(rows[0].get("balance") if isinstance(rows[0], dict) else 0)
            tail = self._num(rows[-1].get("balance") if isinstance(rows[-1], dict) else 0)
            return tail - head
        return self._call(_fn)

    def fetch_stablecoin_mint(self, asset: str) -> Optional[float]:
        """稳定币净铸造（USD）。Coinglass 稳定币端点尚未开放免费通道，
        预留接口与解析逻辑：付费 tier 端点路径由 COINGLASS_STABLE_URI 覆盖，
        默认暂返回 None（不阻断调用方，落地顺序稳定币在最后）。"""
        uri = os.getenv("COINGLASS_STABLE_URI", "").strip()
        if not uri:
            return None

        def _fn() -> float:
            data = self._get_json(uri, {"coin": asset.upper()})
            rows = data.get("data") if isinstance(data, dict) else None
            if not rows:
                raise ValueError(f"empty stablecoin rows for {asset}")
            r = rows[-1]
            if not isinstance(r, dict):
                raise ValueError("bad stablecoin row")
            mint = self._num(r.get("mint", r.get("issued", 0)))
            burn = self._num(r.get("burn", 0))
            return mint - burn
        return self._call(_fn)


class ProviderChain:
    """多 provider 降级链：按优先级依次尝试，第一个成功即返回。

    每个 provider 的失败自动记录到其 stats（health() 可见），切换是
    无感的——调用方拿到的是"最佳可用值"或 None。
    """

    def __init__(self, providers: Optional[List[DataProvider]] = None) -> None:
        self.providers: List[DataProvider] = providers or [CoinglassProvider()]

    def fetch(self, method: str, *args: Any, **kwargs: Any) -> Any:
        for p in self.providers:
            fn = getattr(p, method, None)
            if fn is None:
                continue
            try:
                val = fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 — 降级链边界
                logger.debug(f"[ProviderChain] {p.name}.{method} 异常: {e}")
                val = None
            if val is not None:
                return val
        return None

    def health_report(self) -> Dict[str, Any]:
        return {
            p.name: p.health()
            for p in self.providers
        }


# ── 模块级单例 ──────────────────────────────────────────────────────
coinglass_provider = CoinglassProvider()
provider_chain = ProviderChain([coinglass_provider])


def get_coinglass_provider() -> CoinglassProvider:
    """获取 CoinglassProvider 单例（配置页热更新 key 用）。"""
    return coinglass_provider
