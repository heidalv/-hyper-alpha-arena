"""多场所资金费率采集器（为 delta-neutral 补第二条腿的数据管道）。

2026-07-06 新增（Phase 5 数据接入·选 A：搭管道、留接口、有网即生效、绝不造数）：
    delta-neutral 刷分(SDN)/资金费矩阵需要**同一 symbol 在 ≥2 个场所**的资金费才能凑出
    多空双腿。此前 `perp_funding` 只有 hyperliquid 单场所（由 market_flow_collector 的 WS 写入），
    所以 SDN 永远凑不齐双腿、诚实判 not viable。

    本采集器用**公共只读**行情客户端（ccxt fetch_funding_rates，无需 API key）轮询
    Binance/Bybit/OKX/Gate.io/Asterdex 的资金费，归一为与 hyperliquid 一致的**基础符号**
    （如 "BTC"）后写入 `perp_funding`，与 hyperliquid 数据天然可配对。

诚实原则（关键）：
    - 本环境无外网/被墙时，ccxt 拉取会超时/失败 → 各场所返回 {} → 采集器**优雅空转**并记
      摘要日志，**绝不写入任何虚构/占位费率**。数据只来自真实交易所公共端点。
    - 默认**关闭**（MULTI_VENUE_FUNDING_COLLECTOR_ENABLED=false），需运维在有网环境显式开启，
      避免在无网部署里空跑网络请求。

写入形状：exchange=小写场所名, symbol=基础符号(如 "BTC"), timestamp=采集时刻(ms),
    funding_rate=该场所当前(小时)资金费率, mark_price=None（本管道只采费率）。
    funding_rate_provider.latest_funding_by_venue 会把 "BTC" 归一为 "BTC/USDT" 供矩阵配对。

命令行手动采集：
    python -m backend.services.multi_venue_funding_collector --once
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 采集场所（hyperliquid 已由 market_flow_collector 采集，这里补其余深流动性场所）
DEFAULT_VENUES: List[str] = ["binance", "bybit", "okx", "gateio", "asterdex"]

# 连通性失败状态（用于连续失败告警计数）；"empty"=连通但无匹配 symbol，不计为故障。
FAILURE_STATUSES = {"error", "cancelled", "timeout", "thread_timeout", "unknown"}

# 最近一次采集的健康快照（供状态接口/前端展示）；仅内存，进程内共享。
_LAST_REPORT: Dict[str, object] = {}
# 每个场所连续失败轮数 & 已告警状态（避免每轮重复刷告警，恢复后复位）。
_CONSEC_FAIL: Dict[str, int] = {}
_ALERTED_VENUES: set = set()


def get_last_report() -> Dict[str, object]:
    """返回最近一次采集的健康快照（浅拷贝）；未采集过则空 dict。"""
    return dict(_LAST_REPORT)

# 默认关注的核心 symbol（基础符号）；只写这些，避免灌入上千冷门币。
DEFAULT_SYMBOLS: List[str] = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "ARB", "OP", "SUI",
    "LINK", "LTC", "TON", "APT", "NEAR",
]


def _to_base_symbol(ccxt_symbol: str) -> str:
    """ccxt 永续符号（'BTC/USDT:USDT' / 'BTC/USDT'）→ 基础符号 'BTC'。

    非 USDT 计价（币本位/USDC 等）返回空串以便过滤——只保留 USDT 本位永续，
    与 hyperliquid 数据口径一致，才能跨场所配对。
    """
    s = (ccxt_symbol or "").strip().upper()
    if not s or "/" not in s:
        return ""
    base, _, rest = s.partition("/")
    # rest 形如 "USDT:USDT" 或 "USDT" 或 "USD:USD"
    quote = rest.split(":")[0]
    if quote != "USDT":
        return ""
    return base.strip()


def _filter_and_normalize(
    raw_rates: Dict[str, float], symbols_upper: Optional[set]
) -> Dict[str, float]:
    """把某场所 ccxt 返回的 {ccxt_symbol: rate} 归一为 {base_symbol: rate} 并按白名单过滤。"""
    out: Dict[str, float] = {}
    for sym, rate in (raw_rates or {}).items():
        base = _to_base_symbol(sym)
        if not base:
            continue
        if symbols_upper is not None and base not in symbols_upper:
            continue
        try:
            out[base] = float(rate)
        except (TypeError, ValueError):
            continue
    return out


def _run_fetch_selector_loop(
    venues: List[str],
    symbols_upper: Optional[set],
    timeout: float = 45.0,
    diagnostics: Optional[Dict[str, Dict[str, object]]] = None,
) -> Dict[str, Dict[str, float]]:
    """在独立线程 + SelectorEventLoop 里跑资金费抓取。

    ccxt/aiohttp 依赖 aiodns，在 Windows 默认 ProactorEventLoop 下会抛
    'aiodns needs a SelectorEventLoop'。用独立线程建 SelectorEventLoop 运行，
    既规避该问题、又不干扰主线程可能存在的事件循环。协程在线程内构建/执行，
    超时/异常时返回 {}（不造数）。

    diagnostics（可选，可变 dict）：抓取过程把每个场所的
    {status/count/elapsed_ms/via/error} 写入，供上层输出结构化摘要。
    """
    import asyncio
    import sys
    import threading

    box: Dict[str, Dict[str, Dict[str, float]]] = {"value": {}}

    def _runner() -> None:
        try:
            loop = (
                asyncio.SelectorEventLoop()
                if sys.platform.startswith("win")
                else asyncio.new_event_loop()
            )
            try:
                asyncio.set_event_loop(loop)
                box["value"] = loop.run_until_complete(
                    _fetch_venue_funding(venues, symbols_upper, diagnostics)
                )
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("[MultiVenueFunding] 抓取协程异常: %s", exc)

    t = threading.Thread(target=_runner, name="multi-venue-funding-fetch", daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        logger.warning("[MultiVenueFunding] 抓取超时(%.0fs)，本轮放弃（不写入）", timeout)
        if diagnostics is not None:
            for v in venues:
                diagnostics.setdefault(
                    v, {"status": "thread_timeout", "count": 0, "elapsed_ms": int(timeout * 1000), "via": None}
                )
        return {}
    return box["value"] or {}


# 单场所抓取的时间预算（秒）——防止某个被墙/慢的场所拖垮其余场所。
# 预算按 BULK + MAX_PER_SYMBOL×PER_SYMBOL < VENUE 设计，使正常情况下场所协程
# 自然跑完（finally 能干净 close 客户端），而非被外层 wait_for 取消（会残留会话告警）。
BULK_TIMEOUT_SECONDS = 10.0
PER_SYMBOL_TIMEOUT_SECONDS = 4.0
# 逐 symbol 兜底最多尝试的 symbol 数（受 VENUE_TIMEOUT 约束，避免慢场所耗尽预算）
MAX_PER_SYMBOL_FALLBACK = 4
VENUE_TIMEOUT_SECONDS = 30.0  # >= BULK(10) + MAX_PER_SYMBOL(4)×PER_SYMBOL(4)=26，留裕量


async def _create_async_public(venue: str):
    """????? ccxt async ?????????? ExchangeClientFactory ???/asterdex ?????
    asterdex ? Binance ???????? fapi ???OKX ? swap?"""
    import ccxt.async_support as ccxt_async
    from backend.services.market_aggregation.aggregate_collector_base import _get_proxy
    proxy = _get_proxy()
    cfg: Dict[str, object] = {"enableRateLimit": True, "timeout": 10000}
    if proxy:
        cfg["proxies"] = {"http": proxy, "https": proxy}
    if venue == "asterdex":
        ex = ccxt_async.binance({**cfg, "options": {"defaultType": "future"}})
        # [2026-08-04 修复] 此前 public/private 指向 api.asterdex.com（DNS 不可解析）。
        # 全部覆盖为 fapi.asterdex.com，并限制只加载 linear 合约市场（避免 load_markets
        # 因 spot/dapi 404 整体失败），与 kline_collectors / asterdex_collector 保持一致。
        ex.urls["api"] = {
            "fapiPublic": "https://fapi.asterdex.com/fapi/v1",
            "fapiPrivate": "https://fapi.asterdex.com/fapi/v1",
            "fapiPublicV2": "https://fapi.asterdex.com/fapi/v2",
            "fapiPrivateV2": "https://fapi.asterdex.com/fapi/v2",
            "fapiPublicV3": "https://fapi.asterdex.com/fapi/v3",
            "fapiPrivateV3": "https://fapi.asterdex.com/fapi/v3",
            "fapiData": "https://fapi.asterdex.com/futures/data",
            "public": "https://fapi.asterdex.com/api/v3",
            "private": "https://fapi.asterdex.com/api/v3",
            "dapiPublic": "https://fapi.asterdex.com/dapi/v1",
            "dapiPrivate": "https://fapi.asterdex.com/dapi/v1",
            "eapiPublic": "https://fapi.asterdex.com/eapi/v1",
            "eapiPrivate": "https://fapi.asterdex.com/eapi/v1",
            "sapi": "https://fapi.asterdex.com/sapi/v1",
            "papi": "https://fapi.asterdex.com/papi/v1",
        }
        ex.options = {
            **(ex.options or {}),
            "defaultType": "future",
            "fetchMarkets": {"types": ["linear"]},
        }
        return ex
    if venue == "binance":
        return ccxt_async.binanceusdm(cfg)
    if venue == "okx":
        cfg = {**cfg, "options": {"defaultType": "swap"}}
    cls = getattr(ccxt_async, venue, None)
    if cls is None:
        return None
    return cls(cfg)


def _rest_get_json(url: str, timeout: float = 10.0):
    """?????? REST GET??????????????"""
    import json as _json
    import urllib.request
    proxy = None
    try:
        from backend.services.market_aggregation.aggregate_collector_base import _get_proxy
        proxy = _get_proxy()
    except Exception:
        pass
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        if proxy else urllib.request.ProxyHandler({})
    )
    last_exc: Optional[Exception] = None
    for _attempt in range(2):
        try:
            with opener.open(url, timeout=timeout) as r:
                return _json.loads(r.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            last_exc = exc
            import time as _t
            _t.sleep(1.5)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("rest_get_json failed")


def _fetch_binance_style_funding(venue: str, symbols_upper: Optional[set]):
    """binance/asterdex?/fapi/v1/premiumIndex ??????????"""
    import time as _t
    t0 = _t.time()
    try:
        base_url = "https://fapi.asterdex.com" if venue == "asterdex" else "https://fapi.binance.com"
        data = _rest_get_json(f"{base_url}/fapi/v1/premiumIndex")
        out: Dict[str, float] = {}
        for item in data or []:
            sym = str(item.get("symbol") or "").upper()
            if not sym.endswith("USDT"):
                continue
            base = sym[:-4]
            try:
                rate = float(item.get("lastFundingRate") or 0)
            except (TypeError, ValueError):
                continue
            if symbols_upper is None or base in symbols_upper:
                out[base] = rate
        status = "ok" if out else "empty"
        return out, {"status": status, "count": len(out), "elapsed_ms": int((_t.time() - t0) * 1000), "via": "rest"}
    except Exception as exc:
        return {}, {"status": "error", "count": 0, "elapsed_ms": int((_t.time() - t0) * 1000), "via": None, "error": f"rest:{exc}"}


def _fetch_bybit_funding(symbols_upper: Optional[set]):
    """bybit?/v5/market/tickers?category=linear ??????????????"""
    import time as _t
    t0 = _t.time()
    try:
        data = _rest_get_json("https://api.bybit.com/v5/market/tickers?category=linear")
        items = (data.get("result") or {}).get("list") or []
        out: Dict[str, float] = {}
        for item in items:
            sym = str(item.get("symbol") or "").upper()
            if not sym.endswith("USDT"):
                continue
            base = sym[:-4]
            try:
                rate = float(item.get("fundingRate") or 0)
            except (TypeError, ValueError):
                continue
            if symbols_upper is None or base in symbols_upper:
                out[base] = rate
        status = "ok" if out else "empty"
        return out, {"status": status, "count": len(out), "elapsed_ms": int((_t.time() - t0) * 1000), "via": "rest"}
    except Exception as exc:
        return {}, {"status": "error", "count": 0, "elapsed_ms": int((_t.time() - t0) * 1000), "via": None, "error": f"rest:{exc}"}


def _fetch_okx_funding_ccxt(symbols_upper: Optional[set]):
    """okx?sync ccxt ?? fetch_funding_rate?REST ????? 403??"""
    import time as _t
    t0 = _t.time()
    try:
        from backend.services.market_aggregation.aggregate_collector_base import _create_ccxt_public
        ex = _create_ccxt_public("okx", timeout=20000)
        if ex is None:
            return {}, {"status": "error", "count": 0, "elapsed_ms": int((_t.time() - t0) * 1000), "via": None, "error": "create:okx"}
        ex.options["defaultType"] = "swap"
        out: Dict[str, float] = {}
        targets = sorted(symbols_upper) if symbols_upper else []
        for base in targets:
            try:
                fr = ex.fetch_funding_rate(f"{base}/USDT:USDT")
                rate = float(fr.get("fundingRate")) if isinstance(fr, dict) and fr.get("fundingRate") is not None else None
                if rate is not None:
                    out[base] = rate
            except Exception:
                continue
        try:
            ex.close()
        except Exception:
            pass
        status = "ok" if out else "empty"
        return out, {"status": status, "count": len(out), "elapsed_ms": int((_t.time() - t0) * 1000), "via": "ccxt"}
    except Exception as exc:
        return {}, {"status": "error", "count": 0, "elapsed_ms": int((_t.time() - t0) * 1000), "via": None, "error": f"ccxt:{exc}"}


def _fetch_venue_funding_sync(venue: str, symbols_upper: Optional[set]):
    """sync ????????????async ccxt ???????binance/asterdex/bybit ? REST??"""
    import time as _t
    t0 = _t.time()

    def _elapsed():
        return int((_t.time() - t0) * 1000)

    if venue in ("binance", "asterdex"):
        return _fetch_binance_style_funding(venue, symbols_upper)
    if venue == "bybit":
        return _fetch_bybit_funding(symbols_upper)
    if venue == "okx":
        return _fetch_okx_funding_ccxt(symbols_upper)

    # gateio ??sync ccxt???????
    from backend.services.market_aggregation.aggregate_collector_base import _create_ccxt_public
    try:
        ex = _create_ccxt_public(venue, timeout=20000)
        if ex is None:
            return {}, {"status": "error", "count": 0, "elapsed_ms": _elapsed(), "via": None, "error": f"create:{venue}"}
        if venue in ("bybit", "gateio"):
            try:
                ex.options["defaultType"] = "swap"
            except Exception:
                pass
    except Exception as exc:
        return {}, {"status": "error", "count": 0, "elapsed_ms": _elapsed(), "via": None, "error": f"create:{exc}"}

    filtered: Dict[str, float] = {}
    last_error: Optional[str] = None
    try:
        raw = ex.fetch_funding_rates()
        flat: Dict[str, float] = {}
        for _sym, _tick in (raw or {}).items():
            if isinstance(_tick, dict) and _tick.get("fundingRate") is not None:
                try:
                    flat[_sym] = float(_tick["fundingRate"])
                except (TypeError, ValueError):
                    continue
        filtered = _filter_and_normalize(flat, symbols_upper)
    except Exception as exc:
        last_error = f"bulk:{exc}"

    if symbols_upper and hasattr(ex, "fetch_funding_rate"):
        missing = sorted(symbols_upper - set(filtered.keys()))[:MAX_PER_SYMBOL_FALLBACK]
        for base in missing:
            try:
                fr = ex.fetch_funding_rate(f"{base}/USDT:USDT")
                rate = float(fr.get("fundingRate")) if isinstance(fr, dict) and fr.get("fundingRate") is not None else None
            except Exception as exc:
                last_error = f"one:{exc}"
                rate = None
            if rate is not None:
                filtered[base] = float(rate)

    try:
        ex.close()
    except Exception:
        pass
    if filtered:
        status = "ok"
    elif last_error is not None:
        status = "error"
    else:
        status = "empty"
    diag: Dict[str, object] = {"status": status, "count": len(filtered), "elapsed_ms": _elapsed(), "via": None}
    if status == "error" and last_error:
        diag["error"] = last_error
    return filtered, diag


async def _fetch_one_venue(venue: str, symbols_upper: Optional[set]):
    """??????? to_thread ?? sync ccxt???????"""
    import asyncio
    filtered, diag = await asyncio.to_thread(_fetch_venue_funding_sync, venue, symbols_upper)
    return filtered, diag


async def _fetch_venue_funding(
    venues: List[str],
    symbols_upper: Optional[set],
    diagnostics: Optional[Dict[str, Dict[str, object]]] = None,
) -> Dict[str, Dict[str, float]]:
    """并发抓取各场所资金费，每场所独立超时，慢/被墙场所不拖垮其余。

    返回 {exchange: {base_symbol: rate}}；任一场所失败/超时仅跳过该场所、保留其余成功结果。
    diagnostics（可选）：逐场所写入 {status/count/elapsed_ms/via/error}，供上层结构化摘要。
    """
    import asyncio

    async def _guarded(venue: str):
        try:
            rates, diag = await asyncio.wait_for(
                _fetch_one_venue(venue, symbols_upper), timeout=VENUE_TIMEOUT_SECONDS
            )
            return venue, rates, diag
        except Exception as exc:
            logger.debug("[MultiVenueFunding] %s 超时/失败: %s", venue, exc)
            return venue, {}, {
                "status": "timeout", "count": 0,
                "elapsed_ms": int(VENUE_TIMEOUT_SECONDS * 1000), "via": None, "error": str(exc),
            }

    pairs = await asyncio.gather(*[_guarded(v) for v in venues], return_exceptions=True)

    result: Dict[str, Dict[str, float]] = {}
    # gather 保序：用 zip(venues, pairs) 确保每个场所都留一条诊断，
    # 即便某项返回的是异常（如 CancelledError 不被 _guarded 的 except Exception 捕获）。
    for venue, item in zip(venues, pairs):
        if isinstance(item, tuple) and len(item) == 3:
            _, rates, diag = item
            if rates:
                result[venue] = rates
        else:
            exc_name = type(item).__name__
            # CancelledError（网络/DNS 受限时任务被取消）单独标注，便于运维区分"被墙/超时"与真实报错
            status = "cancelled" if "Cancel" in exc_name else "error"
            rates, diag = {}, {
                "status": status, "count": 0, "elapsed_ms": 0, "via": None,
                "error": f"{exc_name}:{item}".rstrip(":"),
            }
        if diagnostics is not None:
            diagnostics[venue] = diag
    return result


def _persist(venue_rates: Dict[str, Dict[str, float]], ts_ms: int) -> int:
    """把 {exchange:{base_symbol:rate}} 写入 perp_funding，返回写入行数。幂等（唯一约束）。"""
    if not venue_rates:
        return 0
    written = 0
    try:
        from backend.database.connection import MarketSessionLocal, sqlite_write_commit
        from backend.database.models import PerpFunding
    except Exception as exc:
        logger.warning("[MultiVenueFunding] 无法加载 DB 模型: %s", exc)
        return 0

    db = MarketSessionLocal()
    try:
        for exchange, rates in venue_rates.items():
            for base_symbol, rate in rates.items():
                exists = (
                    db.query(PerpFunding.id)
                    .filter(
                        PerpFunding.exchange == exchange,
                        PerpFunding.symbol == base_symbol,
                        PerpFunding.timestamp == ts_ms,
                    )
                    .first()
                )
                if exists:
                    continue
                db.add(
                    PerpFunding(
                        exchange=exchange,
                        symbol=base_symbol,
                        timestamp=ts_ms,
                        funding_rate=rate,
                        mark_price=None,
                    )
                )
                written += 1
        if written:
            sqlite_write_commit(db, label="multi_venue_funding_write")
    except Exception as exc:
        logger.warning("[MultiVenueFunding] 写入 perp_funding 失败: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    finally:
        db.close()
    return written


def _maybe_alert(venue_report: Dict[str, Dict[str, object]]) -> List[str]:
    """连续失败告警：某场所连续 N 轮采集失败即飞书告警一次，恢复后自动复位。

    - status=="ok"        → 计数清零；若之前处于告警态，发一条"恢复"通知并解除告警态。
    - status in FAILURE_* → 计数 +1；跨过阈值且未告警过 → 本轮加入待告警列表。
    - status=="empty"     → 连通但无匹配 symbol，视为非故障，不改计数（避免误报）。

    阈值来自 settings.MULTI_VENUE_FUNDING_ALERT_THRESHOLD（0=关闭）。
    实际发送走 FeishuNotifier.send_sync，未配置通知渠道时静默降级、绝不阻塞采集。
    返回本轮新触发告警的场所列表（供测试断言）。
    """
    try:
        from backend.config import settings as _settings

        threshold = int(getattr(_settings, "MULTI_VENUE_FUNDING_ALERT_THRESHOLD", 3) or 0)
    except Exception:
        threshold = 3
    if threshold <= 0:
        return []

    newly_failed: List[str] = []
    recovered: List[str] = []
    for venue, diag in venue_report.items():
        status = str(diag.get("status") or "unknown")
        if status == "ok":
            _CONSEC_FAIL[venue] = 0
            if venue in _ALERTED_VENUES:
                _ALERTED_VENUES.discard(venue)
                recovered.append(venue)
        elif status in FAILURE_STATUSES:
            _CONSEC_FAIL[venue] = _CONSEC_FAIL.get(venue, 0) + 1
            if _CONSEC_FAIL[venue] >= threshold and venue not in _ALERTED_VENUES:
                _ALERTED_VENUES.add(venue)
                newly_failed.append(venue)
        # status=="empty" 或其他：不改计数

    if not newly_failed and not recovered:
        return newly_failed

    try:
        from backend.services.openclaw_notify import get_notifier

        notifier = get_notifier()
        if newly_failed:
            lines = [f"多场所资金费采集连续失败（≥{threshold}轮）："]
            for v in newly_failed:
                d = venue_report.get(v, {})
                lines.append(
                    f"• {v}：{d.get('status')}（连续{_CONSEC_FAIL.get(v)}轮，"
                    f"{d.get('elapsed_ms')}ms{('，'+str(d.get('error'))) if d.get('error') else ''}）"
                )
            lines.append("→ 该场所资金费暂不可用，delta-neutral 配对将缺腿；请检查外网/风控限频。")
            notifier.send_sync(
                text="\n".join(lines),
                title="⚠️ 资金费采集场所异常",
                level="warning",
                event_type="system",
            )
        if recovered:
            notifier.send_sync(
                text="以下场所资金费采集已恢复：" + "、".join(recovered),
                title="✅ 资金费采集恢复",
                level="info",
                event_type="system",
            )
    except Exception as exc:
        logger.debug("[MultiVenueFunding] 发送告警失败（忽略）：%s", exc)

    return newly_failed


def collect_once(
    symbols: Optional[List[str]] = None,
    venues: Optional[List[str]] = None,
) -> Dict[str, object]:
    """采集一次多场所资金费并写入 perp_funding。

    Returns 摘要 dict：venues_with_data / rows_written / symbols_covered / offline。
    无任何场所返回数据时（离线/被墙）→ offline=True、rows_written=0，绝不造数。
    """
    venues = venues or DEFAULT_VENUES
    symbols = symbols if symbols is not None else DEFAULT_SYMBOLS
    symbols_upper = {s.strip().upper() for s in symbols} if symbols else None

    t0 = time.time()
    diagnostics: Dict[str, Dict[str, object]] = {}
    venue_rates = _run_fetch_selector_loop(venues, symbols_upper, diagnostics=diagnostics)

    ts_ms = int(time.time() * 1000)
    rows = _persist(venue_rates, ts_ms)

    covered = sorted({sym for m in venue_rates.values() for sym in m})
    # 逐场所结构化摘要：即便某场所无数据也留一条（含 status/耗时/失败原因），
    # 便于运维在有网环境一眼看清哪些场所通了、哪些超时/被墙。
    venue_report = {
        v: diagnostics.get(v, {"status": "unknown", "count": 0, "elapsed_ms": 0, "via": None})
        for v in venues
    }
    summary = {
        "venues_with_data": sorted(venue_rates.keys()),
        "rows_written": rows,
        "symbols_covered": covered,
        "offline": len(venue_rates) == 0,
        "elapsed_ms": int((time.time() - t0) * 1000),
        "venue_report": venue_report,
        "as_of": ts_ms,
        "as_of_iso": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(),
    }

    # 更新内存健康快照（供状态接口/前端展示）+ 连续失败告警。
    _LAST_REPORT.clear()
    _LAST_REPORT.update(summary)
    try:
        _maybe_alert(venue_report)
    except Exception as exc:  # 告警绝不可影响采集主流程
        logger.debug("[MultiVenueFunding] 告警评估异常（忽略）：%s", exc)

    # 单行结构化摘要（每个场所：status/count/耗时ms/来源）
    report_str = " ".join(
        f"{v}={d.get('status')}({d.get('count')},{d.get('elapsed_ms')}ms,{d.get('via') or '-'})"
        for v, d in venue_report.items()
    )
    if summary["offline"]:
        logger.info(
            "[MultiVenueFunding] 本轮无场所返回数据（离线/无外网）→ 未写入，等有网自动生效 | %s",
            report_str,
        )
    else:
        logger.info(
            "[MultiVenueFunding] 采集完成：场所=%s 写入=%d 行 覆盖%d个symbol | %s",
            summary["venues_with_data"], rows, len(covered), report_str,
        )
    return summary


# [2026-08-15 D1] 资金费率历史回填：支持历史接口的场所（binance/bybit/okx/gateio）；
# asterdex premiumIndex 无历史接口，诚实跳过（只前向积累）。
BACKFILL_VENUES: List[str] = ["binance", "bybit", "okx", "gateio"]


def _fetch_funding_history_for_symbol(ex, base: str, since_ms: int) -> List[Dict]:
    """ccxt fetch_funding_rate_history 分页拉取，返回 [{ts_ms, rate}]（升序）。"""
    import ccxt  # noqa: F401  # 仅用于异常类型判断

    out: List[Dict] = []
    until_ms: Optional[int] = None
    guard = 0
    while guard < 60:  # 每符号最多 60 页
        guard += 1
        params: Dict[str, object] = {"limit": 500}
        if until_ms is not None:
            params["until"] = until_ms - 1
        try:
            rows = ex.fetch_funding_rate_history(f"{base}/USDT:USDT", since=since_ms, params=params)
        except Exception as exc:
            logger.debug("[MultiVenueFunding] 回填 %s/%s 页失败: %s", ex.id, base, exc)
            break
        if not rows:
            break
        parsed = []
        for r in rows:
            ts = r.get("timestamp") or r.get("fundingTimestamp")
            rate = r.get("fundingRate")
            if ts is None or rate is None:
                continue
            try:
                parsed.append({"ts_ms": int(ts), "rate": float(rate)})
            except (TypeError, ValueError):
                continue
        if not parsed:
            break
        out.extend(parsed)
        oldest = min(p["ts_ms"] for p in parsed)
        if oldest <= since_ms:
            break
        until_ms = oldest
        if len(parsed) < 500:
            break
    # 去重（同一结算时刻可能跨页出现）并按时间升序
    dedup: Dict[int, float] = {}
    for p in out:
        dedup[p["ts_ms"]] = p["rate"]
    return [{"ts_ms": ts, "rate": r} for ts, r in sorted(dedup.items())]


def backfill_funding_history(
    symbols: Optional[List[str]] = None,
    days: int = 90,
    venues: Optional[List[str]] = None,
    max_symbols: int = 40,
) -> Dict[str, Any]:
    """回填多场所资金费率历史 → perp_funding（幂等：已存在的不重写）。

    - 场所：BACKFILL_VENUES（asterdex 无历史接口，自动跳过）；
    - 符号：默认 DEFAULT_SYMBOLS + 成交额热币（经 get_research_priority_symbols 扩展，
      上限 max_symbols），只用 ccxt 公共接口，无 API key；
    - 写语义：只补缺失（timestamp 毫秒 = 结算时刻），与实时采集同表同口径。
    """
    import threading

    from backend.services.market_aggregation.aggregate_collector_base import _create_ccxt_public

    venues = venues or BACKFILL_VENUES
    symbols = symbols if symbols is not None else list(DEFAULT_SYMBOLS)
    if not symbols:
        return {"ok": False, "reason": "no symbols"}
    syms = list(dict.fromkeys(s.upper() for s in symbols))[:max_symbols]

    since_ms = int((time.time() - days * 86400) * 1000)
    summary: Dict[str, Any] = {"venues": {}, "total_written": 0, "ok": True}

    try:
        from backend.database.connection import MarketSessionLocal, sqlite_write_commit
        from backend.database.models import PerpFunding
    except Exception as exc:
        return {"ok": False, "reason": f"db unavailable: {exc}"}

    for venue in venues:
        v_summary: Dict[str, Any] = {"status": "skip", "written": 0, "error": None}
        try:
            ex = _create_ccxt_public(venue, timeout=30000)
            if ex is None:
                v_summary["status"] = "error"
                v_summary["error"] = "create client failed"
                summary["venues"][venue] = v_summary
                continue
            if venue in ("bybit", "gateio", "okx"):
                try:
                    ex.options["defaultType"] = "swap"
                except Exception:
                    pass
            db = MarketSessionLocal()
            try:
                for base in syms:
                    try:
                        hist = _fetch_funding_history_for_symbol(ex, base, since_ms)
                    except Exception as exc:
                        logger.debug("[MultiVenueFunding] 回填 %s/%s 失败: %s", venue, base, exc)
                        continue
                    if not hist:
                        continue
                    # 幂等：查已有结算时刻
                    ts_list = [h["ts_ms"] for h in hist]
                    existing = {
                        r[0]
                        for r in db.query(PerpFunding.timestamp)
                        .filter(
                            PerpFunding.exchange == venue,
                            PerpFunding.symbol == base,
                            PerpFunding.timestamp.in_(ts_list),
                        )
                        .all()
                    }
                    added = 0
                    for h in hist:
                        if h["ts_ms"] in existing:
                            continue
                        db.add(
                            PerpFunding(
                                exchange=venue,
                                symbol=base,
                                timestamp=h["ts_ms"],
                                funding_rate=h["rate"],
                                mark_price=None,
                            )
                        )
                        added += 1
                    if added:
                        sqlite_write_commit(db, label="multi_venue_funding_backfill")
                        v_summary["written"] += added
                    if added > 0:
                        logger.info(
                            "[MultiVenueFunding] 回填 %s/%s +%d 行（%d 天）",
                            venue, base, added, days,
                        )
            finally:
                db.close()
            try:
                ex.close()
            except Exception:
                pass
            v_summary["status"] = "ok"
        except Exception as exc:
            v_summary["status"] = "error"
            v_summary["error"] = str(exc)[:200]
            logger.warning("[MultiVenueFunding] 回填场所 %s 失败: %s", venue, exc)
        summary["venues"][venue] = v_summary
        summary["total_written"] += v_summary["written"]

    summary["ok"] = summary["total_written"] > 0
    logger.info(
        "[MultiVenueFunding] 历史回填完成: %s",
        {v: s["written"] for v, s in summary["venues"].items()},
    )
    return summary


def start_funding_backfill_thread(days: int = 90) -> None:
    """数据中心进程启动时后台回填一次资金费历史（每周重跑一次补齐缺口）。"""
    import threading

    def _run() -> None:
        while True:
            try:
                backfill_funding_history(days=days)
            except Exception as exc:
                logger.warning("[MultiVenueFunding] 回填线程异常: %s", exc)
            time.sleep(7 * 86400)

    t = threading.Thread(target=_run, name="multi-venue-funding-backfill", daemon=True)
    t.start()


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="多场所资金费采集器")
    parser.add_argument("--once", action="store_true", help="采集一次后退出")
    parser.add_argument("--symbols", type=str, default="", help="逗号分隔基础符号，如 BTC,ETH")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    syms = [s for s in args.symbols.split(",") if s.strip()] or None
    summary = collect_once(symbols=syms)
    print(summary)


if __name__ == "__main__":
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    _main()
