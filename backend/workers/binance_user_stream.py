# -*- coding: utf-8 -*-
"""
binance_user_stream — 币安用户数据流（listenKey + ACCOUNT_UPDATE 实时推送）

架构（《币安实盘接口设计_V1》长期项）:
  POST /fapi/v1/listenKey → wss://fstream.binance.com/ws/<listenKey>
  事件: ACCOUNT_UPDATE(余额B/持仓P) / ACCOUNT_CONFIG_UPDATE / listenKeyExpired
  + 每 12s REST V2 positionRisk 合并 leverage/liquidationPrice/initialMargin
  → 原子写 data/live_user_stream_snapshot.json（主进程 API 直读，REST 降级为兜底）

运行: 独立进程（schtasks onlogon），60 分钟 listenKey 由 50 分钟续期覆盖。
"""
import asyncio
import json
import logging
import os
import sys
import time
import traceback

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(levelname)s %(name)s %(message)s")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SNAPSHOT = os.path.join(REPO, "data", "live_user_stream_snapshot.json")
SNAPSHOT_TMP = SNAPSHOT + ".tmp"
WS_URL = "wss://fstream.binance.com/ws/{}"
WS_PROXY = os.environ.get("BINANCE_HTTP_PROXY") or "http://127.0.0.1:1080"
RENEW_EVERY = 50 * 60          # 50 分钟续期（listenKey 60 分钟有效）
MERGE_EVERY = 12.0             # REST V2 合并周期（秒）
SNAPSHOT_MAX_AGE = 20.0        # 快照有效窗口（主进程以此判断新鲜度）
ACCOUNT_ID = int(os.environ.get("USER_STREAM_ACCOUNT_ID", "188") or "188")

sys.path.insert(0, REPO)
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(REPO, ".env"), override=True)  # 强制覆盖：任务环境的残留密钥变量会导致凭证解密失败


def log(msg: str) -> None:
    line = "%s [user-stream] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(os.path.join(REPO, "logs", "user_stream.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


_state = {
    "listen_key": None,
    "listen_key_ts": 0.0,
    "balances": {},          # asset -> {wb, cw, bc}
    "positions": {},         # symbol -> {amt, ep, bep, up, mt, iw, ps, ma, cr}
    "leverage_map": {},      # symbol -> {leverage, liquidation_price, initial_margin, break_even}
    "last_event_ts": 0.0,
}


def _build_client():
    """直连 DB 构建客户端（异常入日志，避免 manager 静默吞错）。"""
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import ExchangeCredential
        from backend.utils.encryption import decrypt_private_key
        from backend.services.exchange.exchange_factory import ExchangeClientFactory

        with SessionLocal() as db:
            cred = db.query(ExchangeCredential).filter(
                ExchangeCredential.user_id == 326,
                ExchangeCredential.exchange == "binance",
                ExchangeCredential.enabled == True,  # noqa: E712
                ExchangeCredential.account_id == ACCOUNT_ID,
            ).first()
            if not cred:
                cred = db.query(ExchangeCredential).filter(
                    ExchangeCredential.user_id == 326,
                    ExchangeCredential.exchange == "binance",
                    ExchangeCredential.enabled == True,  # noqa: E712
                ).first()
            if not cred:
                log("no binance credential found (account=%s)" % ACCOUNT_ID)
                return None
            api_key = decrypt_private_key(cred.api_key_encrypted) if cred.api_key_encrypted else ""
            api_secret = decrypt_private_key(cred.api_secret_encrypted) if cred.api_secret_encrypted else ""
            return ExchangeClientFactory.create(
                "binance",
                api_key=api_key,
                secret=api_secret,
                password="",
                testnet=cred.testnet,
                proxy_url=getattr(cred, "proxy_url", None) or None,
                market_type="usdt_m",
            )
    except Exception as e:
        log("_build_client failed: %s" % traceback.format_exc(limit=3))
        return None


def _write_snapshot() -> None:
    payload = {
        "ts": time.time(),
        "account_id": ACCOUNT_ID,
        "listen_key_ok": bool(_state["listen_key"]),
        "balances": _state["balances"],
        "positions": _state["positions"],
        "leverage_map": _state["leverage_map"],
        "last_event_ts": _state["last_event_ts"],
    }
    with open(SNAPSHOT_TMP, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    try:
        os.replace(SNAPSHOT_TMP, SNAPSHOT)
    except OSError:
        # Windows 下读进程瞬时占用目标文件时 rename 失败——降级直接覆盖写
        try:
            with open(SNAPSHOT, "w", encoding="utf-8") as f2:
                json.dump(payload, f2, ensure_ascii=False)
        except Exception:
            pass


async def _merge_leverage_rest(ex) -> None:
    """每 12s REST V2 positionRisk 合并杠杆/强平价/初始保证金/保本价。"""
    try:
        # [2026-08-28 实盘数据卡死修复] ExchangeClientFactory 返回的是
        # BinanceAdapter（包装 ccxt），ccxt 隐式方法只存在于底层 raw client
        # (self._exchange)。此前直接调 ex.fapiPrivateV2GetPositionRisk →
        # AttributeError → position/leverage 合并 19:23 起从未成功，实盘
        # 持仓/杠杆数据冻结（WS 只推增量事件）。
        _raw = getattr(ex, "_exchange", None) or ex
        rows = await _raw.fapiPrivateV2GetPositionRisk()
        _map = {}
        for r in rows or []:
            sym = str(r.get("symbol") or "").upper()
            base = sym.replace("USDT", "").replace("USD", "")
            if not base:
                continue
            _amt = abs(float(r.get("positionAmt") or 0))
            if _amt <= 0:
                continue
            _lev = float(r.get("leverage") or 0) or 1.0
            _notional = float(r.get("notional") or 0)
            _im = _notional / _lev if _lev > 0 else 0.0
            _map[base] = {
                "leverage": _lev,
                "liquidation_price": (float(r.get("liquidationPrice") or 0) if float(r.get("liquidationPrice") or 0) > 0 else None),
                "initial_margin": round(_im, 8),
                "break_even": float(r.get("breakEvenPrice") or 0),
                "margin_type": str(r.get("marginType") or "") or None,
                "isolated": bool(r.get("isolated")) if r.get("isolated") is not None else None,
            }
            # 初始/定期同步完整持仓（WS 只推增量）
            _state["positions"][base] = {
                "amt": float(r.get("positionAmt") or 0),
                "ep": float(r.get("entryPrice") or 0),
                "bep": float(r.get("breakEvenPrice") or 0),
                "up": float(r.get("unRealizedProfit") or 0),
                "mp": float(r.get("markPrice") or 0),
                "mt": str(r.get("marginType") or "") or None,
                "iw": float(r.get("isolatedMargin") or 0),
                "ps": str(r.get("positionSide") or "BOTH"),
                "ma": "USDT",
            }
        _state["leverage_map"] = _map
        # 余额同步（fapi/v2/account，官方字段 walletBalance/crossWalletBalance）
        try:
            _acc = await _raw.fapiPrivateV2GetAccount()
            for _a in (_acc.get("assets") or []):
                _asset = str(_a.get("asset") or "")
                if _asset:
                    _state["balances"][_asset] = {
                        "wb": float(_a.get("walletBalance") or 0),
                        "cw": float(_a.get("crossWalletBalance") or 0),
                        "bc": float(_a.get("balanceChange") or 0),
                    }
        except Exception:
            pass
        _write_snapshot()
    except Exception as e:
        log("merge leverage REST failed: %s" % str(e)[:120])


async def _handle_account_update(a: dict) -> None:
    for b in (a.get("B") or []):
        asset = str(b.get("a") or "")
        if not asset:
            continue
        _state["balances"][asset] = {
            "wb": float(b.get("wb") or 0),
            "cw": float(b.get("cw") or 0),
            "bc": float(b.get("bc") or 0),
        }
    for p in (a.get("P") or []):
        sym = str(p.get("s") or "").upper()
        base = sym.replace("USDT", "").replace("USD", "")
        if not base:
            continue
        _state["positions"][base] = {
            "amt": float(p.get("pa") or 0),
            "ep": float(p.get("ep") or 0),
            "bep": float(p.get("bep") or 0),
            "cr": float(p.get("cr") or 0),
            "up": float(p.get("up") or 0),
            "mt": str(p.get("mt") or "") or None,
            "iw": float(p.get("iw") or 0),
            "ps": str(p.get("ps") or "BOTH"),
            "ma": str(p.get("ma") or "USDT"),
        }
    _state["last_event_ts"] = time.time()
    _write_snapshot()


async def _ws_loop(ex) -> None:
    import aiohttp

    async with aiohttp.ClientSession() as session:
        url = WS_URL.format(_state["listen_key"])
        async with session.ws_connect(url, proxy=WS_PROXY, heartbeat=30, receive_timeout=None) as ws:
            log("ws connected listenKey=%s..." % str(_state["listen_key"])[:12])
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        raise ConnectionError("ws closed: %s" % msg.type)
                    continue
                try:
                    ev = json.loads(msg.data)
                except Exception:
                    continue
                e = str(ev.get("e") or "")
                if e == "ACCOUNT_UPDATE":
                    await _handle_account_update(ev.get("a") or {})
                elif e == "ACCOUNT_CONFIG_UPDATE":
                    _state["last_event_ts"] = time.time()
                    _write_snapshot()
                elif e == "listenKeyExpired":
                    log("listenKeyExpired received, restart ws")
                    raise ConnectionError("listenKeyExpired")


async def _ensure_listen_key(ex) -> bool:
    if _state["listen_key"] and time.time() - _state["listen_key_ts"] < RENEW_EVERY:
        return True
    r = await ex.fapiPrivatePostListenKey()
    _state["listen_key"] = str(r.get("listenKey") or "")
    _state["listen_key_ts"] = time.time()
    log("listenKey created")
    return bool(_state["listen_key"])


async def _renew_listen_key(ex) -> None:
    try:
        await ex.fapiPrivatePutListenKey()
        _state["listen_key_ts"] = time.time()
        log("listenKey renewed")
    except Exception as e:
        log("listenKey renew failed: %s" % str(e)[:120])


async def main() -> None:
    log("binance user stream starting account=%s proxy=%s" % (ACCOUNT_ID, WS_PROXY))
    ex = None
    while True:
        try:
            if ex is None:
                client = _build_client()
                if client is None or client._exchange is None:
                    log("client unavailable (凭证缺失?), retry 30s")
                    await asyncio.sleep(30)
                    continue
                ex = client._exchange
            if not await _ensure_listen_key(ex):
                log("listenKey 获取失败, retry 10s")
                await asyncio.sleep(10)
                continue
            merge_task = asyncio.create_task(_periodic_merge(ex))
            renew_task = asyncio.create_task(_periodic_renew(ex))
            try:
                await _ws_loop(ex)
            finally:
                merge_task.cancel()
                renew_task.cancel()
        except Exception as e:
            log("ws loop error: %s; reconnect in 5s" % str(e)[:160])
        await asyncio.sleep(5)


async def _periodic_merge(ex) -> None:
    while True:
        await asyncio.sleep(MERGE_EVERY)
        try:
            await _merge_leverage_rest(ex)
        except Exception:
            pass


async def _periodic_renew(ex) -> None:
    while True:
        await asyncio.sleep(RENEW_EVERY)
        try:
            await _renew_listen_key(ex)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()
