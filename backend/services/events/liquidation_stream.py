# -*- coding: utf-8 -*-
"""多交易所全市场逐笔清算流 → liquidation_ticks + liquidation_events + market_events。

数据源（LIQUIDATION_STREAM_SOURCES，默认 binance,asterdex,okx,bybit，全部免费公共流）：
  binance   wss://fstream.binance.com/ws/!forceOrder@arr      Binance USDⓈ-M 全市场（每秒每币最多 1 条代表性成交）
  asterdex  wss://fstream.asterdex.com/ws/!forceOrder@arr     Aster 与 Binance 同构（实盘所在场所）
  okx       wss://ws.okx.com:8443/ws/v5/public                liquidation-orders / instType=SWAP（一次订阅全市场）
  bybit     wss://stream.bybit.com/v5/public/linear           allLiquidation.{symbol}（按 24h 成交额 Top-N 订阅）

  注：部分网络环境下 Binance 期货行情 WS 握手成功但静默不推帧（REST 与用户流正常），
  因此保留 binance 源但把它当"锦上添花"，其余三源保证清算数据不缺位。
  各源状态见 stream_status()['sources']，静默 ≥ LIQ_SILENT_WARN_SEC 会标记 silent 并告警一次。

Binance/Aster 消息格式：
  {"e":"forceOrder","E":..., "o":{"s":"BTCUSDT","S":"SELL","q":"0.014","p":"9910","ap":"9910","X":"FILLED","T":...}}
  S=SELL → 多头被清算（强平卖出）；S=BUY → 空头被清算。
OKX：details[].side（buy/sell 为强平订单方向，与 Binance S 同义）、sz 为张数 → 名义 = sz × ctVal × bkPx。
Bybit：data[].S 为「被清算的持仓方向」（Buy=多头被清算）→ 转换成订单方向 SELL。

产物：
  liquidation_ticks     逐笔（exchange 区分，保留 LIQ_TICKS_RETENTION_DAYS=30 天）
  liquidation_events    小时聚合 rollup（每个 exchange 一行/币/小时，source='ws'，与 coinalyze 行并存）
  market_events         liquidation.large（单笔 ≥ LIQ_LARGE_TICK_USD）
                        liquidation.cascade（单币 5 分钟跨所合计 ≥ 阈值；每 5 分钟桶最多 1 条）
                        liquidation.market_cascade（全市场 5 分钟合计 ≥ 阈值）

运行形态：守护线程 + 自建 asyncio loop，每个源一个协程各自指数退避重连；共用缓冲 2s 落一次库；
每 60s 向 job_registry 心跳（任务名 liquidation_stream）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from backend.services.events import market_events_store as mes
from backend.services.events._http import get_json, ws_proxy_for

logger = logging.getLogger(__name__)

WS_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"
ASTER_WS_URL = "wss://fstream.asterdex.com/ws/!forceOrder@arr"
OKX_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
OKX_INSTRUMENTS_URL = "https://www.okx.com/api/v5/public/instruments"
BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"
BYBIT_TICKERS_URL = "https://api.bybit.com/v5/market/tickers"

DEFAULT_SOURCES = "binance,asterdex,okx,bybit"
_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "USD")
_MAJORS = {"BTC", "ETH"}

_schema_ready = False
_schema_lock = threading.Lock()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def base_of(pair: str) -> str:
    p = (pair or "").upper()
    for suf in _QUOTE_SUFFIXES:
        if p.endswith(suf) and len(p) > len(suf):
            return p[: -len(suf)]
    return p


def enabled_sources() -> List[str]:
    raw = os.getenv("LIQUIDATION_STREAM_SOURCES", DEFAULT_SOURCES) or DEFAULT_SOURCES
    out: List[str] = []
    for s in raw.split(","):
        s = s.strip().lower()
        if s and s in _SOURCE_FACTORIES and s not in out:
            out.append(s)
    return out


@dataclass
class LiqTick:
    pair: str
    symbol: str
    side: str            # BUY / SELL（强平订单方向；SELL = 多头被清算）
    price: float
    qty: float
    notional_usd: float
    ts_ms: int
    order_status: str = ""
    exchange: str = "binance"

    @property
    def long_liquidated(self) -> bool:
        return self.side == "SELL"


# ─────────────────────────────────────────────────────────────────────────────
# 解析器（纯函数，可单测）
# ─────────────────────────────────────────────────────────────────────────────
def _loads(msg: Any) -> Any:
    if isinstance(msg, (str, bytes)):
        try:
            return json.loads(msg)
        except Exception:
            return None
    return msg


def parse_force_order(msg: Any, exchange: str = "binance") -> Optional[LiqTick]:
    """Binance/Aster `forceOrder` 消息（dict 或 JSON 串）→ LiqTick；格式不符返回 None。"""
    msg = _loads(msg)
    if not isinstance(msg, dict):
        return None
    o = msg.get("o")
    if not isinstance(o, dict) or msg.get("e") not in (None, "forceOrder"):
        return None
    try:
        pair = str(o.get("s") or "").upper()
        side = str(o.get("S") or "").upper()
        price = float(o.get("ap") or 0) or float(o.get("p") or 0)
        qty = float(o.get("z") or 0) or float(o.get("q") or 0)
        ts_ms = int(o.get("T") or msg.get("E") or 0)
    except (TypeError, ValueError):
        return None
    if not pair or side not in ("BUY", "SELL") or price <= 0 or qty <= 0 or ts_ms <= 0:
        return None
    return LiqTick(pair=pair, symbol=base_of(pair), side=side, price=price, qty=qty,
                   notional_usd=round(price * qty, 2), ts_ms=ts_ms,
                   order_status=str(o.get("X") or ""), exchange=exchange)


def okx_inst_to_pair(inst_id: str) -> Optional[str]:
    """'BTC-USDT-SWAP' → 'BTCUSDT'；币本位（'BTC-USD-SWAP'）与非 SWAP 返回 None。"""
    parts = (inst_id or "").upper().split("-")
    if len(parts) != 3 or parts[2] != "SWAP" or parts[1] not in ("USDT", "USDC"):
        return None
    return parts[0] + parts[1]


def parse_okx_liquidations(msg: Any, ct_val: Dict[str, float]) -> List[LiqTick]:
    """OKX `liquidation-orders` 推送 → LiqTick 列表。ct_val: instId → 合约面值（基础币数量）。
    缺少面值时跳过该 instId（宁缺毋滥，避免名义额错一个量级）。"""
    msg = _loads(msg)
    if not isinstance(msg, dict) or not isinstance(msg.get("data"), list):
        return []
    out: List[LiqTick] = []
    for item in msg["data"]:
        if not isinstance(item, dict):
            continue
        inst_id = str(item.get("instId") or "")
        pair = okx_inst_to_pair(inst_id)
        if not pair:
            continue
        cv = ct_val.get(inst_id)
        if not cv or cv <= 0:
            continue
        for d in item.get("details") or []:
            if not isinstance(d, dict):
                continue
            try:
                side = str(d.get("side") or "").upper()
                price = float(d.get("bkPx") or 0)
                sz = float(d.get("sz") or 0)
                ts_ms = int(d.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            if side not in ("BUY", "SELL") or price <= 0 or sz <= 0 or ts_ms <= 0:
                continue
            qty = sz * cv
            out.append(LiqTick(pair=pair, symbol=base_of(pair), side=side, price=price, qty=qty,
                               notional_usd=round(price * qty, 2), ts_ms=ts_ms,
                               order_status=str(d.get("posSide") or ""), exchange="okx"))
    return out


def parse_bybit_liquidations(msg: Any) -> List[LiqTick]:
    """Bybit `allLiquidation.*` 推送 → LiqTick 列表。S 是被清算持仓方向：Buy=多头被清算 → 订单方向 SELL。"""
    msg = _loads(msg)
    if not isinstance(msg, dict) or not str(msg.get("topic") or "").startswith("allLiquidation."):
        return []
    data = msg.get("data")
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    out: List[LiqTick] = []
    for d in data:
        if not isinstance(d, dict):
            continue
        try:
            pair = str(d.get("s") or "").upper()
            pos_side = str(d.get("S") or "").upper()
            price = float(d.get("p") or 0)
            qty = float(d.get("v") or 0)
            ts_ms = int(d.get("T") or msg.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if not pair or pos_side not in ("BUY", "SELL") or price <= 0 or qty <= 0 or ts_ms <= 0:
            continue
        side = "SELL" if pos_side == "BUY" else "BUY"
        out.append(LiqTick(pair=pair, symbol=base_of(pair), side=side, price=price, qty=qty,
                           notional_usd=round(price * qty, 2), ts_ms=ts_ms,
                           order_status=("long" if side == "SELL" else "short"), exchange="bybit"))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 级联检测（跨所按币合计）
# ─────────────────────────────────────────────────────────────────────────────
class CascadeDetector:
    """纯内存级联检测：滚动 5 分钟窗口按币/全市场求和，超阈值发事件（每桶一次）。"""

    def __init__(self, *, window_sec: int = 300, large_tick_usd: Optional[float] = None,
                 major_usd: Optional[float] = None, alt_usd: Optional[float] = None,
                 market_usd: Optional[float] = None):
        self.window_ms = int(window_sec * 1000)
        self.large_tick_usd = large_tick_usd if large_tick_usd is not None else _env_float("LIQ_LARGE_TICK_USD", 250_000.0)
        self.major_usd = major_usd if major_usd is not None else _env_float("LIQ_CASCADE_5M_MAJOR_USD", 10_000_000.0)
        self.alt_usd = alt_usd if alt_usd is not None else _env_float("LIQ_CASCADE_5M_USD", 2_000_000.0)
        self.market_usd = market_usd if market_usd is not None else _env_float("LIQ_CASCADE_5M_MARKET_USD", 50_000_000.0)
        self._by_symbol: Dict[str, Deque[Tuple[int, float, bool]]] = {}
        self._market: Deque[Tuple[int, float, bool]] = deque()
        self._emitted: Set[str] = set()
        self._emitted_order: Deque[str] = deque(maxlen=5000)

    def _mark(self, key: str) -> bool:
        if key in self._emitted:
            return False
        self._emitted.add(key)
        self._emitted_order.append(key)
        if len(self._emitted) > 5000:
            while len(self._emitted) > 4000 and self._emitted_order:
                old = self._emitted_order.popleft()
                self._emitted.discard(old)
        return True

    @staticmethod
    def _sum(dq: Deque[Tuple[int, float, bool]]) -> Tuple[float, float]:
        long_usd = sum(n for _, n, is_long in dq if is_long)
        short_usd = sum(n for _, n, is_long in dq if not is_long)
        return long_usd, short_usd

    def add(self, t: LiqTick) -> List[mes.MarketEvent]:
        events: List[mes.MarketEvent] = []
        src = f"{t.exchange}_ws"
        cutoff = t.ts_ms - self.window_ms
        dq = self._by_symbol.setdefault(t.symbol, deque())
        dq.append((t.ts_ms, t.notional_usd, t.long_liquidated))
        while dq and dq[0][0] < cutoff:
            dq.popleft()
        self._market.append((t.ts_ms, t.notional_usd, t.long_liquidated))
        while self._market and self._market[0][0] < cutoff:
            self._market.popleft()

        # 单笔大额
        if t.notional_usd >= self.large_tick_usd:
            sev = 2 if t.notional_usd < 1_000_000 else (3 if t.notional_usd < 5_000_000 else 4)
            key = mes.make_dedupe_hash("liq_large", t.exchange, t.pair, t.ts_ms, t.side, t.qty)
            if self._mark(key):
                events.append(mes.MarketEvent(
                    event_type=mes.LIQUIDATION_LARGE, ts_ms=t.ts_ms, source=src, symbol=t.symbol,
                    severity=sev, direction=(-0.5 if t.long_liquidated else 0.5),
                    title=f"{t.exchange} {t.pair} 单笔清算 ${t.notional_usd:,.0f}（{'多头' if t.long_liquidated else '空头'}）",
                    payload={"exchange": t.exchange, "pair": t.pair, "side": t.side, "price": t.price, "qty": t.qty,
                             "notional_usd": t.notional_usd, "long_liquidated": t.long_liquidated},
                    dedupe_hash=key,
                ))

        bucket = t.ts_ms // self.window_ms
        # 单币级联（跨所合计）
        thr = self.major_usd if t.symbol in _MAJORS else self.alt_usd
        long_usd, short_usd = self._sum(dq)
        total = long_usd + short_usd
        if total >= thr:
            key = mes.make_dedupe_hash("liq_cascade", t.symbol, bucket)
            if self._mark(key):
                dominant_long = long_usd >= short_usd
                sev = 3 if total < thr * 3 else 4
                events.append(mes.MarketEvent(
                    event_type=mes.LIQUIDATION_CASCADE, ts_ms=t.ts_ms, source="liq_ws", symbol=t.symbol,
                    severity=sev, direction=(-1.0 if dominant_long else 1.0),
                    title=f"{t.symbol} 5 分钟清算级联 ${total:,.0f}（多 ${long_usd:,.0f} / 空 ${short_usd:,.0f}）",
                    payload={"pair": t.pair, "window_sec": self.window_ms // 1000, "total_usd": round(total, 2),
                             "long_usd": round(long_usd, 2), "short_usd": round(short_usd, 2),
                             "threshold_usd": thr, "ticks": len(dq), "window_hours": 2.0},
                    dedupe_hash=key,
                ))
        # 全市场级联
        m_long, m_short = self._sum(self._market)
        m_total = m_long + m_short
        if m_total >= self.market_usd:
            key = mes.make_dedupe_hash("liq_market_cascade", bucket)
            if self._mark(key):
                dominant_long = m_long >= m_short
                events.append(mes.MarketEvent(
                    event_type=mes.LIQUIDATION_MARKET_CASCADE, ts_ms=t.ts_ms, source="liq_ws", symbol=None,
                    severity=4 if m_total < self.market_usd * 3 else 5, direction=(-1.0 if dominant_long else 1.0),
                    title=f"全市场 5 分钟清算 ${m_total:,.0f}（多 ${m_long:,.0f} / 空 ${m_short:,.0f}）",
                    payload={"window_sec": self.window_ms // 1000, "total_usd": round(m_total, 2),
                             "long_usd": round(m_long, 2), "short_usd": round(m_short, 2),
                             "threshold_usd": self.market_usd, "ticks": len(self._market), "window_hours": 4.0},
                    dedupe_hash=key,
                ))
        return events


# ─────────────────────────────────────────────────────────────────────────────
# 落库
# ─────────────────────────────────────────────────────────────────────────────
def _db():
    from backend.database.connection import MarketSessionLocal
    return MarketSessionLocal()


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        try:
            from sqlalchemy import text
            db = _db()
            try:
                db.execute(text(
                    """
                    CREATE TABLE IF NOT EXISTS liquidation_ticks (
                        id BIGSERIAL PRIMARY KEY,
                        exchange VARCHAR(20) NOT NULL DEFAULT 'binance',
                        symbol VARCHAR(32) NOT NULL,
                        pair VARCHAR(40) NOT NULL,
                        side VARCHAR(4) NOT NULL,
                        price NUMERIC(20, 8) NOT NULL,
                        qty NUMERIC(24, 8) NOT NULL,
                        notional_usd NUMERIC(20, 2) NOT NULL,
                        ts_ms BIGINT NOT NULL,
                        order_status VARCHAR(16),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        CONSTRAINT uq_liq_tick UNIQUE (exchange, pair, ts_ms, side, price, qty)
                    )
                    """
                ))
                db.execute(text("CREATE INDEX IF NOT EXISTS ix_liq_ticks_symbol_ts ON liquidation_ticks (symbol, ts_ms DESC)"))
                db.execute(text("CREATE INDEX IF NOT EXISTS ix_liq_ticks_ts ON liquidation_ticks (ts_ms DESC)"))
                db.commit()
                _schema_ready = True
            except Exception as exc:
                db.rollback()
                logger.warning("[liq_stream] 建表失败: %s", exc)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("[liq_stream] 建表跳过: %s", exc)


def persist_ticks(ticks: List[LiqTick]) -> int:
    if not ticks:
        return 0
    ensure_schema()
    try:
        from sqlalchemy import text
        db = _db()
        try:
            res = db.execute(text(
                "INSERT INTO liquidation_ticks (exchange, symbol, pair, side, price, qty, notional_usd, ts_ms, order_status) "
                "VALUES (:exchange, :symbol, :pair, :side, :price, :qty, :notional, :ts, :status) "
                "ON CONFLICT ON CONSTRAINT uq_liq_tick DO NOTHING"
            ), [{"exchange": (t.exchange or "binance")[:20], "symbol": t.symbol[:32], "pair": t.pair[:40],
                 "side": t.side, "price": t.price, "qty": t.qty, "notional": t.notional_usd, "ts": int(t.ts_ms),
                 "status": (t.order_status or "")[:16] or None}
                for t in ticks])
            db.commit()
            n = res.rowcount if res.rowcount is not None and res.rowcount >= 0 else len(ticks)
            return int(n)
        except Exception as exc:
            db.rollback()
            logger.warning("[liq_stream] 写入 %d 笔失败: %s", len(ticks), exc)
            return 0
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[liq_stream] 写入跳过: %s", exc)
        return 0


def rollup_hourly(hours_back: int = 3) -> Dict[str, Any]:
    """把 liquidation_ticks 聚合到 liquidation_events（小时，按 exchange 分行，source='ws'）
    并清理过期逐笔。作为定时任务 liquidation_rollup 每小时运行。"""
    ensure_schema()
    from backend.core.tenant import set_system_identity
    set_system_identity()
    retention_days = _env_int("LIQ_TICKS_RETENTION_DAYS", 30)
    now_ms = int(time.time() * 1000)
    since = (now_ms // 3600_000 - int(hours_back)) * 3600_000
    out: Dict[str, Any] = {"since_ms": since, "rows_upserted": 0, "ticks_deleted": 0}
    try:
        from sqlalchemy import text
        db = _db()
        try:
            res = db.execute(text(
                """
                INSERT INTO liquidation_events (exchange, symbol, ts_ms, long_usd, short_usd, source)
                SELECT exchange, symbol, (ts_ms / 3600000) * 3600000 AS h,
                       COALESCE(SUM(CASE WHEN side = 'SELL' THEN notional_usd END), 0),
                       COALESCE(SUM(CASE WHEN side = 'BUY' THEN notional_usd END), 0),
                       'ws'
                FROM liquidation_ticks
                WHERE ts_ms >= :since
                GROUP BY exchange, symbol, (ts_ms / 3600000) * 3600000
                ON CONFLICT (exchange, symbol, ts_ms) DO UPDATE
                    SET long_usd = EXCLUDED.long_usd, short_usd = EXCLUDED.short_usd
                """
            ), {"since": since})
            out["rows_upserted"] = int(res.rowcount or 0)
            cutoff = now_ms - int(retention_days) * 86400_000
            res2 = db.execute(text("DELETE FROM liquidation_ticks WHERE ts_ms < :cutoff"), {"cutoff": cutoff})
            out["ticks_deleted"] = int(res2.rowcount or 0)
            db.commit()
        except Exception as exc:
            db.rollback()
            out["error"] = str(exc)[:300]
            logger.warning("[liq_stream] rollup 失败: %s", exc)
        finally:
            db.close()
    except Exception as exc:
        out["error"] = str(exc)[:300]
    out.update(stream_status())
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 数据源适配器
# ─────────────────────────────────────────────────────────────────────────────
class _Source:
    """一个 WS 数据源：连接地址、订阅报文、应用层 ping、解析。子类只需覆写 parse/prepare。"""
    name: str = ""
    url: str = ""
    ping_text: Optional[str] = None      # 应用层心跳文本（OKX 'ping' / Bybit {"op":"ping"}）
    ping_interval: float = 20.0
    refresh_interval: float = 6 * 3600   # 元数据（合约面值/币池）刷新周期

    def __init__(self) -> None:
        self._meta_ts: float = 0.0

    def prepare(self) -> None:
        """连接前刷新元数据（同步、可失败）。"""
        return None

    def subscribe_msgs(self) -> List[str]:
        return []

    def parse(self, data: str) -> List[LiqTick]:
        raise NotImplementedError

    def meta(self) -> Dict[str, Any]:
        return {}


class BinanceForceOrderSource(_Source):
    name = "binance"
    url = WS_URL

    def parse(self, data: str) -> List[LiqTick]:
        t = parse_force_order(data, exchange=self.name)
        return [t] if t else []


class AsterForceOrderSource(BinanceForceOrderSource):
    name = "asterdex"
    url = ASTER_WS_URL


class OkxLiquidationSource(_Source):
    name = "okx"
    url = OKX_WS_URL
    ping_text = "ping"
    ping_interval = 20.0

    def __init__(self) -> None:
        super().__init__()
        self.ct_val: Dict[str, float] = {}

    def prepare(self) -> None:
        if self.ct_val and time.time() - self._meta_ts < self.refresh_interval:
            return
        payload = get_json(OKX_INSTRUMENTS_URL, params={"instType": "SWAP"}, timeout=20.0)
        rows = (payload or {}).get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            if not self.ct_val:
                logger.warning("[liq_stream][okx] instruments 拉取失败，暂不解析（缺合约面值）")
            return
        cv: Dict[str, float] = {}
        for r in rows:
            try:
                inst = str(r.get("instId") or "")
                val = float(r.get("ctVal") or 0)
                if inst and val > 0 and okx_inst_to_pair(inst):
                    cv[inst] = val
            except (TypeError, ValueError, AttributeError):
                continue
        if cv:
            self.ct_val = cv
            self._meta_ts = time.time()

    def subscribe_msgs(self) -> List[str]:
        return [json.dumps({"op": "subscribe", "args": [{"channel": "liquidation-orders", "instType": "SWAP"}]})]

    def parse(self, data: str) -> List[LiqTick]:
        if data == "pong":
            return []
        return parse_okx_liquidations(data, self.ct_val)

    def meta(self) -> Dict[str, Any]:
        return {"instruments": len(self.ct_val)}


class BybitLiquidationSource(_Source):
    name = "bybit"
    url = BYBIT_WS_URL
    ping_text = json.dumps({"op": "ping"})
    ping_interval = 20.0

    def __init__(self) -> None:
        super().__init__()
        self.symbols: List[str] = []

    def prepare(self) -> None:
        if self.symbols and time.time() - self._meta_ts < self.refresh_interval:
            return
        top_n = max(10, _env_int("BYBIT_LIQ_TOP_N", 60))
        payload = get_json(BYBIT_TICKERS_URL, params={"category": "linear"}, timeout=20.0)
        rows = ((payload or {}).get("result") or {}).get("list") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            if not self.symbols:
                self.symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT", "ADAUSDT",
                                "LINKUSDT", "AVAXUSDT", "SUIUSDT"]
                logger.warning("[liq_stream][bybit] tickers 拉取失败，退回 10 个主流币")
            return
        scored: List[Tuple[float, str]] = []
        for r in rows:
            try:
                sym = str(r.get("symbol") or "").upper()
                if not sym.endswith("USDT") or "-" in sym:
                    continue
                scored.append((float(r.get("turnover24h") or 0), sym))
            except (TypeError, ValueError, AttributeError):
                continue
        scored.sort(reverse=True)
        if scored:
            self.symbols = [s for _, s in scored[:top_n]]
            self._meta_ts = time.time()

    def subscribe_msgs(self) -> List[str]:
        msgs: List[str] = []
        for i in range(0, len(self.symbols), 10):
            chunk = self.symbols[i:i + 10]
            msgs.append(json.dumps({"op": "subscribe", "args": [f"allLiquidation.{s}" for s in chunk]}))
        return msgs

    def parse(self, data: str) -> List[LiqTick]:
        return parse_bybit_liquidations(data)

    def meta(self) -> Dict[str, Any]:
        return {"symbols": len(self.symbols)}


_SOURCE_FACTORIES = {
    "binance": BinanceForceOrderSource,
    "asterdex": AsterForceOrderSource,
    "okx": OkxLiquidationSource,
    "bybit": BybitLiquidationSource,
}


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket 消费线程（多源）
# ─────────────────────────────────────────────────────────────────────────────
class LiquidationStream:
    JOB_NAME = "liquidation_stream"

    def __init__(self, sources: Optional[List[str]] = None):
        names = sources if sources is not None else enabled_sources()
        self.sources: List[_Source] = [_SOURCE_FACTORIES[n]() for n in names if n in _SOURCE_FACTORIES]
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._buf: List[LiqTick] = []
        self._buf_lock = threading.Lock()
        self._detector = CascadeDetector()
        self._silent_warned: Set[str] = set()
        self.status: Dict[str, Any] = {
            "enabled": False, "sources": {}, "ticks_total": 0, "ticks_written": 0, "events_total": 0,
            "last_msg_ts": None, "started_ts": None, "last_flush_ts": None,
        }
        for s in self.sources:
            self.status["sources"][s.name] = {
                "url": s.url, "proxy": ws_proxy_for(s.url), "connected": False, "connected_ts": None,
                "ticks_total": 0, "reconnects": 0, "last_msg_ts": None, "last_error": None, "silent": False,
            }

    # ── 生命周期 ──
    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        if not self.sources:
            logger.warning("[liq_stream] 没有可用数据源（LIQUIDATION_STREAM_SOURCES 为空）")
            return False
        self._stop.clear()
        self.status["enabled"] = True
        self.status["started_ts"] = time.time()
        self._thread = threading.Thread(target=self._thread_main, name="liquidation-stream", daemon=True)
        self._thread.start()
        logger.info("[liq_stream] 已启动 sources=%s", [s.name for s in self.sources])
        return True

    def stop(self) -> None:
        self._stop.set()
        self.status["enabled"] = False

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ── 主循环 ──
    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_forever())
        except Exception as exc:  # pragma: no cover
            logger.warning("[liq_stream] 线程异常退出: %s", exc)
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _run_forever(self) -> None:
        tasks = [asyncio.ensure_future(self._run_source(s)) for s in self.sources]
        flusher = asyncio.ensure_future(self._flush_loop())
        try:
            while not self._stop.is_set():
                await asyncio.sleep(1.0)
        finally:
            for t in tasks:
                t.cancel()
            flusher.cancel()
            for t in tasks + [flusher]:
                try:
                    await t
                except (Exception, asyncio.CancelledError):
                    pass
            try:
                self._flush_sync()
            except Exception as exc:  # pragma: no cover
                logger.debug("[liq_stream] 收尾 flush 失败: %s", exc)

    async def _run_source(self, src: _Source) -> None:
        import aiohttp
        st = self.status["sources"][src.name]
        backoff = 2.0
        loop = asyncio.get_event_loop()
        while not self._stop.is_set():
            try:
                try:
                    await loop.run_in_executor(None, src.prepare)
                except Exception as exc:
                    logger.debug("[liq_stream][%s] prepare 失败: %s", src.name, exc)
                timeout = aiohttp.ClientTimeout(total=None, connect=20)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.ws_connect(src.url, proxy=st["proxy"], heartbeat=30,
                                                  receive_timeout=None) as ws:
                        st["connected"] = True
                        st["connected_ts"] = time.time()
                        st["last_error"] = None
                        backoff = 2.0
                        for m in src.subscribe_msgs():
                            await ws.send_str(m)
                        logger.info("[liq_stream][%s] WS 已连接 %s", src.name, src.meta() or "")
                        while not self._stop.is_set():
                            try:
                                msg = await asyncio.wait_for(ws.receive(), timeout=src.ping_interval)
                            except asyncio.TimeoutError:
                                if src.ping_text:
                                    await ws.send_str(src.ping_text)
                                self._check_silent(src, st)
                                continue
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                self._on_text(src, st, msg.data)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING,
                                              aiohttp.WSMsgType.ERROR):
                                raise ConnectionError(f"ws closed: {msg.type}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                st["connected"] = False
                st["last_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
                st["reconnects"] = int(st["reconnects"]) + 1
                if self._stop.is_set():
                    break
                logger.warning("[liq_stream][%s] 断线 %s → %.0fs 后重连", src.name, st["last_error"], backoff)
                await asyncio.sleep(backoff)
                backoff = min(120.0, backoff * 2)
        st["connected"] = False

    def _check_silent(self, src: _Source, st: Dict[str, Any]) -> None:
        """已连接但长时间零消息 → 标记 silent（Binance 期货流在部分网络下即如此）。"""
        warn_sec = _env_float("LIQ_SILENT_WARN_SEC", 900.0)
        ref = st.get("last_msg_ts") or st.get("connected_ts") or time.time()
        silent = (time.time() - ref) >= warn_sec
        st["silent"] = silent
        if silent and src.name not in self._silent_warned:
            self._silent_warned.add(src.name)
            logger.warning("[liq_stream][%s] 连接正常但 %.0f 分钟无任何清算帧（网络对该流静默或该所清算稀少）",
                           src.name, warn_sec / 60)

    def _on_text(self, src: _Source, st: Dict[str, Any], data: str) -> None:
        try:
            ticks = src.parse(data)
        except Exception as exc:
            logger.debug("[liq_stream][%s] 解析失败: %s", src.name, exc)
            return
        if not ticks:
            return
        now = time.time()
        st["ticks_total"] = int(st["ticks_total"]) + len(ticks)
        st["last_msg_ts"] = now
        st["silent"] = False
        self.status["ticks_total"] = int(self.status["ticks_total"]) + len(ticks)
        self.status["last_msg_ts"] = now
        with self._buf_lock:
            self._buf.extend(ticks)

    async def _flush_loop(self) -> None:
        last_hb = 0.0
        loop = asyncio.get_event_loop()
        while not self._stop.is_set():
            await asyncio.sleep(2.0)
            try:
                await loop.run_in_executor(None, self._flush_sync)
            except Exception as exc:  # pragma: no cover
                logger.debug("[liq_stream] flush 异常: %s", exc)
            now = time.time()
            if now - last_hb >= 60:
                last_hb = now
                try:
                    await loop.run_in_executor(None, self._heartbeat)
                except Exception:
                    pass

    def _heartbeat(self) -> None:
        try:
            from backend.services.ops.job_registry import heartbeat
            heartbeat(self.JOB_NAME)
        except Exception as exc:
            logger.debug("[liq_stream] heartbeat fail: %s", exc)

    def _flush_sync(self) -> None:
        with self._buf_lock:
            batch, self._buf = self._buf, []
        if not batch:
            return
        events: List[mes.MarketEvent] = []
        for t in sorted(batch, key=lambda x: x.ts_ms):
            events.extend(self._detector.add(t))
        written = persist_ticks(batch)
        self.status["ticks_written"] = int(self.status["ticks_written"]) + written
        self.status["last_flush_ts"] = time.time()
        if events:
            n = mes.publish(events)
            self.status["events_total"] = int(self.status["events_total"]) + n


_STREAM: Optional[LiquidationStream] = None
_STREAM_LOCK = threading.Lock()


def get_stream() -> LiquidationStream:
    global _STREAM
    with _STREAM_LOCK:
        if _STREAM is None:
            _STREAM = LiquidationStream()
        return _STREAM


def start_liquidation_stream() -> bool:
    """启动（幂等）。LIQUIDATION_STREAM_ENABLED=false 时不启动。"""
    if str(os.getenv("LIQUIDATION_STREAM_ENABLED", "true")).strip().lower() in ("0", "false", "no", "off"):
        logger.info("[liq_stream] LIQUIDATION_STREAM_ENABLED=false，未启动")
        return False
    return get_stream().start()


def stream_status() -> Dict[str, Any]:
    s = get_stream()
    d = dict(s.status)
    d["sources"] = {k: dict(v) for k, v in s.status.get("sources", {}).items()}
    d["alive"] = s.is_alive()
    lm = d.get("last_msg_ts")
    d["last_msg_age_sec"] = round(time.time() - lm, 1) if lm else None
    d["connected"] = any(v.get("connected") for v in d["sources"].values())
    d["receiving"] = [k for k, v in d["sources"].items() if v.get("connected") and not v.get("silent")
                      and v.get("last_msg_ts")]
    return d
