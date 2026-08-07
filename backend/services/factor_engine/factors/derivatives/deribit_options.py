"""
Deribit 期权数据源（整改#12，免费公开 API，无需 key）。

补齐大多数永续系统忽略的"期权数据缺口"。产出 3 类点位信号，喂给已存在的
`OptionsStructureFactor`（消费 K线 DF 中的 options_skew / iv_term_structure 列）：

  - options_skew      : OTM 看跌 IV − OTM 看涨 IV（vol 点）。>0 = 看跌保护需求强 = 恐惧。
  - iv_term_structure : 近月 ATM IV / 远月 ATM IV。>1 = 期限倒挂（预期短期剧烈波动）。
  - gamma_magnet      : 最大 OI 行权价对现价的"磁吸"方向信号 [-1,1]。

架构对齐现网 `derivatives_analytics_service`：TTL 缓存 + stale-while-revalidate 后台刷新，
热路径永不阻塞网络；任何异常/无数据 → 优雅降级为中性（None / 0.0）。Deribit 主要覆盖
BTC/ETH 期权，其它币种直接返回 None。

零风险：本模块不自动接入实盘数据池；由 DERIBIT_OPTIONS_ENABLED 显式开启后，调用方可用
inject_into_klines() 把列注入 K线 DF。
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DERIBIT_BASE = "https://www.deribit.com/api/v2/public"

# 交易对 → Deribit currency（仅 BTC/ETH 有活跃期权链）
_CURRENCY_MAP = {
    "BTC": "BTC", "BTCUSDT": "BTC", "BTC-PERP": "BTC", "BTC/USDT": "BTC", "XBT": "BTC",
    "ETH": "ETH", "ETHUSDT": "ETH", "ETH-PERP": "ETH", "ETH/USDT": "ETH",
}

_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}

# 形如 BTC-27JUN25-70000-C
_INSTRUMENT_RE = re.compile(r"^[A-Z]+-(\d{1,2})([A-Z]{3})(\d{2})-(\d+(?:d\d+)?)-([CP])$")


def symbol_to_currency(symbol: str) -> Optional[str]:
    if not symbol:
        return None
    s = symbol.upper().replace(":", "").strip()
    if s in _CURRENCY_MAP:
        return _CURRENCY_MAP[s]
    # 前缀匹配（BTCUSD、BTCUSDT_...）
    for pfx in ("BTC", "XBT", "ETH"):
        if s.startswith(pfx):
            return "BTC" if pfx in ("BTC", "XBT") else "ETH"
    return None


def parse_instrument_name(name: str) -> Optional[Tuple[datetime, float, bool]]:
    """解析 Deribit 期权名 → (到期时间UTC, 行权价, 是否看涨)。无法解析返回 None。"""
    if not name:
        return None
    m = _INSTRUMENT_RE.match(name.strip().upper())
    if not m:
        return None
    day, mon, yy, strike_raw, cp = m.groups()
    mon_num = _MONTHS.get(mon)
    if not mon_num:
        return None
    try:
        year = 2000 + int(yy)
        expiry = datetime(year, mon_num, int(day), 8, 0, 0, tzinfo=timezone.utc)  # Deribit 08:00 UTC 结算
        strike = float(strike_raw.replace("d", "."))
        return expiry, strike, (cp == "C")
    except (ValueError, TypeError):
        return None


@dataclass
class DeribitOptionsSnapshot:
    currency: str
    spot: float
    options_skew: float           # vol 点，>0=恐惧
    iv_term_structure: float      # 近/远 ATM IV 比
    gamma_magnet_strike: float
    gamma_magnet_signal: float    # [-1,1]
    n_instruments: int
    ts: float

    def as_kline_columns(self) -> Dict[str, float]:
        return {
            "options_skew": self.options_skew,
            "iv_term_structure": self.iv_term_structure,
            "gamma_magnet_signal": self.gamma_magnet_signal,
        }


# ============================ 纯计算（可离线测试）============================
def _enrich(instruments: List[dict]) -> List[dict]:
    """给每条 book_summary 附上解析后的 (expiry, strike, is_call)。"""
    out = []
    for it in instruments:
        parsed = parse_instrument_name(it.get("instrument_name", ""))
        if parsed is None:
            continue
        expiry, strike, is_call = parsed
        iv = it.get("mark_iv")
        if iv is None:
            iv = it.get("volatility")  # 兜底字段
        if iv is None:
            continue
        out.append({
            "expiry": expiry, "strike": strike, "is_call": is_call,
            "iv": float(iv), "oi": float(it.get("open_interest", 0.0) or 0.0),
        })
    return out


def _nearest_expiry(rows: List[dict], now: datetime) -> Optional[datetime]:
    futures = sorted({r["expiry"] for r in rows if r["expiry"] > now})
    return futures[0] if futures else None


def compute_skew(rows: List[dict], spot: float, now: Optional[datetime] = None,
                 band: float = 0.15) -> float:
    """近月：band 内 OTM 看跌 IV 均值 − OTM 看涨 IV 均值（vol 点）。"""
    if not rows or spot <= 0:
        return 0.0
    now = now or datetime.now(timezone.utc)
    near = _nearest_expiry(rows, now)
    if near is None:
        return 0.0
    lo, hi = spot * (1 - band), spot * (1 + band)
    put_ivs = [r["iv"] for r in rows if r["expiry"] == near and not r["is_call"]
               and lo <= r["strike"] < spot]
    call_ivs = [r["iv"] for r in rows if r["expiry"] == near and r["is_call"]
                and spot < r["strike"] <= hi]
    if not put_ivs or not call_ivs:
        return 0.0
    return round(sum(put_ivs) / len(put_ivs) - sum(call_ivs) / len(call_ivs), 4)


def _atm_iv(rows: List[dict], expiry: datetime, spot: float) -> Optional[float]:
    same = [r for r in rows if r["expiry"] == expiry]
    if not same:
        return None
    atm = min(same, key=lambda r: abs(r["strike"] - spot))
    # 取该行权价 call/put IV 均值
    ivs = [r["iv"] for r in same if abs(r["strike"] - atm["strike"]) < 1e-9]
    return sum(ivs) / len(ivs) if ivs else None


def compute_term_structure(rows: List[dict], spot: float, now: Optional[datetime] = None) -> float:
    """近月 ATM IV / 远月 ATM IV。缺数据返回 1.0（中性）。"""
    if not rows or spot <= 0:
        return 1.0
    now = now or datetime.now(timezone.utc)
    expiries = sorted({r["expiry"] for r in rows if r["expiry"] > now})
    if len(expiries) < 2:
        return 1.0
    near_iv = _atm_iv(rows, expiries[0], spot)
    far_iv = _atm_iv(rows, expiries[-1], spot)
    if not near_iv or not far_iv or far_iv <= 0:
        return 1.0
    return round(near_iv / far_iv, 4)


def compute_gamma_magnet(rows: List[dict], spot: float, now: Optional[datetime] = None,
                         band: float = 0.1) -> Tuple[float, float]:
    """近月 band 内按 OI 聚合各行权价，取最大 OI 行权价作磁吸位。

    返回 (magnet_strike, signal[-1,1])；signal 指向磁吸位相对现价的方向。
    """
    if not rows or spot <= 0:
        return (0.0, 0.0)
    now = now or datetime.now(timezone.utc)
    near = _nearest_expiry(rows, now)
    if near is None:
        return (0.0, 0.0)
    lo, hi = spot * (1 - band), spot * (1 + band)
    oi_by_strike: Dict[float, float] = {}
    for r in rows:
        if r["expiry"] == near and lo <= r["strike"] <= hi:
            oi_by_strike[r["strike"]] = oi_by_strike.get(r["strike"], 0.0) + r["oi"]
    if not oi_by_strike:
        return (0.0, 0.0)
    magnet = max(oi_by_strike.items(), key=lambda kv: kv[1])[0]
    signal = max(-1.0, min(1.0, (magnet - spot) / spot / 0.05))
    return (magnet, round(signal, 4))


def build_snapshot(currency: str, spot: float, instruments: List[dict],
                   now: Optional[datetime] = None) -> Optional[DeribitOptionsSnapshot]:
    rows = _enrich(instruments)
    if not rows or spot <= 0:
        return None
    skew = compute_skew(rows, spot, now)
    ts_struct = compute_term_structure(rows, spot, now)
    magnet, magnet_sig = compute_gamma_magnet(rows, spot, now)
    return DeribitOptionsSnapshot(
        currency=currency, spot=float(spot), options_skew=skew,
        iv_term_structure=ts_struct, gamma_magnet_strike=magnet,
        gamma_magnet_signal=magnet_sig, n_instruments=len(rows), ts=time.time(),
    )


# ============================ 服务（缓存 + 后台刷新 + 降级）============================
class DeribitOptionsService:
    def __init__(self, ttl: float = 300.0, timeout: float = 4.0):
        self.enabled = os.environ.get("DERIBIT_OPTIONS_ENABLED", "false").strip().lower() in (
            "1", "true", "yes", "on")
        self.ttl = ttl
        self.timeout = timeout
        self._cache: Dict[str, DeribitOptionsSnapshot] = {}
        self._bg_refreshing: Dict[str, bool] = {}
        self._lock = threading.Lock()

    # ---- 网络（仅在 get_snapshot 主动刷新时调用）----
    def _http_get(self, path: str, params: dict) -> Optional[dict]:
        try:
            import httpx

            # Deribit 在中国可能需要代理（与 ccxt 共用同一代理配置）
            _proxy = os.environ.get("BINANCE_HTTPS_PROXY") or os.environ.get("HTTPS_PROXY")
            _client_kwargs = {"timeout": self.timeout, "trust_env": False}
            if _proxy:
                _client_kwargs["proxies"] = {"http://": _proxy, "https://": _proxy}

            with httpx.Client(**_client_kwargs) as client:
                resp = client.get(f"{_DERIBIT_BASE}/{path}", params=params)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.debug("[Deribit] GET %s 失败: %s", path, e)
            return None

    def _fetch(self, currency: str) -> Optional[DeribitOptionsSnapshot]:
        data = self._http_get("get_book_summary_by_currency",
                              {"currency": currency, "kind": "option"})
        if not data or "result" not in data:
            return None
        instruments = data["result"] or []
        spot = 0.0
        for it in instruments:
            up = it.get("underlying_price")
            if up:
                spot = float(up)
                break
        if spot <= 0:
            idx = self._http_get("get_index_price", {"index_name": f"{currency.lower()}_usd"})
            if idx and idx.get("result"):
                spot = float(idx["result"].get("index_price", 0.0) or 0.0)
        return build_snapshot(currency, spot, instruments)

    def get_snapshot(self, symbol: str) -> Optional[DeribitOptionsSnapshot]:
        """主动（可能阻塞网络）获取快照并刷新缓存。"""
        currency = symbol_to_currency(symbol)
        if not currency or not self.enabled:
            return None
        snap = self._fetch(currency)
        if snap is not None:
            with self._lock:
                self._cache[currency] = snap
        return snap

    def get_cached_snapshot(self, symbol: str, max_stale: float = 900.0
                            ) -> Optional[DeribitOptionsSnapshot]:
        """热路径专用：返回缓存（可能陈旧），并在后台刷新；无缓存返回 None，绝不阻塞。"""
        currency = symbol_to_currency(symbol)
        if not currency or not self.enabled:
            return None
        with self._lock:
            snap = self._cache.get(currency)
        fresh = snap is not None and (time.time() - snap.ts) < self.ttl
        if not fresh:
            self._trigger_bg_refresh(currency)
        if snap is not None and (time.time() - snap.ts) < max_stale:
            return snap
        return None

    def _trigger_bg_refresh(self, currency: str) -> None:
        with self._lock:
            if self._bg_refreshing.get(currency):
                return
            self._bg_refreshing[currency] = True

        def _worker():
            try:
                snap = self._fetch(currency)
                if snap is not None:
                    with self._lock:
                        self._cache[currency] = snap
            finally:
                with self._lock:
                    self._bg_refreshing[currency] = False

        threading.Thread(target=_worker, daemon=True).start()

    def inject_into_klines(self, df, symbol: str):
        """opt-in：把期权列注入 K线 DataFrame（最后一行/广播）。返回是否注入成功。"""
        snap = self.get_cached_snapshot(symbol)
        if snap is None or df is None or len(df) == 0:
            return False
        for col, val in snap.as_kline_columns().items():
            df[col] = val
        return True


_service_singleton: Optional[DeribitOptionsService] = None


def get_deribit_options_service() -> DeribitOptionsService:
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = DeribitOptionsService()
    return _service_singleton
