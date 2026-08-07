import asyncio
import hashlib
import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

# 确保日志级别为INFO
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

from backend.database.connection import SessionLocal, MarketSessionLocal, AnalyticsSessionLocal
from backend.database.models import AIDecisionLog, Account, CryptoPrice, Trade, User
from backend.repositories.account_repo import get_account, get_or_create_default_account
from backend.repositories.order_repo import list_orders
from backend.repositories.position_repo import list_positions
from backend.repositories.user_repo import get_or_create_user, get_user
from backend.services.asset_calculator import calc_positions_value
from backend.services.asset_curve_calculator import get_all_asset_curves_data_new
from backend.services.market_data import get_last_price
from backend.services.scheduler import add_account_snapshot_job, remove_account_snapshot_job
from backend.services.hyperliquid_cache import get_cached_account_state, get_cached_positions
from backend.services.hyperliquid_environment import get_hyperliquid_client


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, Set[WebSocket]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, websocket: WebSocket):
        pass  # WebSocket is already accepted in the endpoint

    def register(self, account_id: Optional[int], websocket: WebSocket):
        if account_id is not None:
            self.active_connections.setdefault(account_id, set()).add(websocket)
            # Create snapshot tracker for delta mode
            if WS_DELTA_MODE:
                _ws_trackers.setdefault(account_id, {})[websocket] = _SnapshotTracker()
            # Add scheduled snapshot task for new account with configured interval
            from config.refresh_config import get_refresh_interval
            interval = get_refresh_interval("websocket_snapshot")
            add_account_snapshot_job(account_id, interval_seconds=interval)

    def unregister(self, account_id: Optional[int], websocket: WebSocket):
        if account_id is not None and account_id in self.active_connections:
            logging.info(
                f"[WS] unregister account={account_id} sockets_left={len(self.active_connections[account_id])}"
            )
            self.active_connections[account_id].discard(websocket)
            # Clean up snapshot tracker
            if account_id in _ws_trackers:
                _ws_trackers[account_id].pop(websocket, None)
                if not _ws_trackers[account_id]:
                    del _ws_trackers[account_id]
            if not self.active_connections[account_id]:
                del self.active_connections[account_id]
                # Remove the scheduled task for this account
                remove_account_snapshot_job(account_id)

    async def send_to_account(self, account_id: int, message: dict):
        """Send a message to all sockets for a given account.

        委托 ws_redis_bridge:有 Redis 时 publish 到 ws:account:{id}(跨 worker fanout);
        无 Redis 时 bridge 直接调 _dispatch_local 本地直发(单 worker dev 行为不变)。
        """
        from backend.services import ws_redis_bridge
        ws_redis_bridge.publish_to_account(account_id, message)

    async def broadcast_to_all(self, message: dict):
        """Broadcast message to all connected clients.

        委托 ws_redis_bridge:有 Redis 时 publish 到 ws:broadcast(跨 worker fanout);
        无 Redis 时 bridge 直接调 _dispatch_local 本地直发(单 worker dev 行为不变)。
        """
        from backend.services import ws_redis_bridge
        ws_redis_bridge.publish_broadcast(message)

    def _dispatch_local(self, kind: str, account_id: Optional[int], message: dict) -> None:
        """本地投递(由 ws_redis_bridge 回调,跨 worker 订阅/本地直发共用)。

        kind="account"   → 对 account_id 的本地 socket 投递(保留原 snapshot/delta 逻辑)
        kind="broadcast" → 对全部本地 socket 投递

        保留原 send_to_account / broadcast_to_all 的 socket-liveness 清理:
        - client_state != CONNECTED → discard 跳过
        - send 抛异常 → discard 该 socket
        bridge 回调可能来自后台订阅线程(非 async),通过 schedule_task 把
        _async 真正的发送协程投递到事件循环。
        """
        self.schedule_task(self._dispatch_local_async(kind, account_id, message))

    async def _dispatch_local_async(self, kind: str, account_id: Optional[int], message: dict) -> None:
        """_dispatch_local 的实际 async 实现(原 send_to_account/broadcast_to_all 逻辑)。"""
        if kind == "account":
            if account_id not in self.active_connections:
                return

            msg_type = message.get("type", "")
            is_snapshot = msg_type in ("snapshot", "snapshot_fast", "snapshot_full")

            for ws in list(self.active_connections[account_id]):
                try:
                    # Check if WebSocket is still open before sending
                    if ws.client_state.name != "CONNECTED":
                        self.active_connections[account_id].discard(ws)
                        continue

                    # Delta mode: convert snapshot messages
                    if is_snapshot and WS_DELTA_MODE:
                        tracker = _ws_trackers.get(account_id, {}).get(ws)
                        if tracker:
                            out_msg = _apply_delta_logic(tracker, message)
                            if out_msg is None:
                                continue  # No changes, skip this ws
                            payload = json.dumps(out_msg, ensure_ascii=False)
                        else:
                            payload = json.dumps(message, ensure_ascii=False)
                    else:
                        payload = json.dumps(message, ensure_ascii=False)

                    await ws.send_text(payload)
                except Exception as e:
                    # Log the error and remove broken connection
                    logging.warning(f"Failed to send message to WebSocket: {e}")
                    self.active_connections[account_id].discard(ws)
        elif kind == "broadcast":
            payload = json.dumps(message, ensure_ascii=False)
            for acct, websockets in list(self.active_connections.items()):
                for ws in list(websockets):
                    try:
                        # Check if WebSocket is still open before sending
                        if ws.client_state.name != "CONNECTED":
                            websockets.discard(ws)
                            continue
                        await ws.send_text(payload)
                    except Exception as e:
                        # Log the error and remove broken connection
                        logging.warning(f"Failed to broadcast message to WebSocket: {e}")
                        websockets.discard(ws)

    def has_connections(self) -> bool:
        return any(self.active_connections.values())

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        if loop and loop.is_running():
            self._loop = loop

    def schedule_task(self, coro):
        loop = None
        if self._loop and self._loop.is_running():
            loop = self._loop
        else:
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    self._loop = loop
            except RuntimeError:
                loop = None

        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, loop)
            return

        # Fallback: run in a dedicated daemon thread to avoid blocking the caller
        def _run():
            asyncio.run(coro)

        threading.Thread(target=_run, daemon=True).start()


manager = ConnectionManager()

# 接线 Redis pub/sub 桥(跨 worker WS 广播)。
# - configure_local_dispatch:让 bridge 收到 Redis 消息时回调 manager._dispatch_local
#   投递给本地 socket。
# - start_subscriber:无 REDIS_URL 时 no-op(单 worker dev 行为不变);
#   有 REDIS_URL 时起后台线程订阅 ws:account:* / ws:broadcast。
try:
    from backend.services import ws_redis_bridge
    ws_redis_bridge.configure_local_dispatch(manager._dispatch_local)
    ws_redis_bridge.start_subscriber()
except Exception as _bridge_err:
    # 桥接失败不应阻断模块加载(降级为仅本地直发,send_to_account 内部仍会尝试 bridge)。
    logging.warning(f"ws_redis_bridge 初始化失败,退化为本地直发: {_bridge_err}")

HYPERLIQUID_SNAPSHOT_CACHE_TTL = 360  # seconds

# Delta mode switch (set WS_DELTA_MODE=false to disable)
WS_DELTA_MODE = os.environ.get("WS_DELTA_MODE", "true").lower() != "false"


class _SnapshotTracker:
    """Track last snapshot state per connection, compute incremental deltas."""

    def __init__(self, full_every_n: int = 10):
        self._last: Dict[str, Any] = {}  # key -> hashed/raw value of last sent data
        self._last_positions: Dict[int, dict] = {}  # position id -> position dict
        self._last_trade_ids: Set[int] = set()
        self._last_ai_ids: Set[int] = set()
        self._last_order_ids: Set[int] = set()
        self._last_overview_hash: str = ""
        self._last_hl_state_hash: str = ""
        self._seq: int = 0
        self._full_every_n: int = full_every_n

    def should_send_full(self) -> bool:
        """Every N deltas, force a full snapshot for consistency."""
        return self._seq == 0 or self._seq % self._full_every_n == 0

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    @staticmethod
    def _hash_dict(d: dict) -> str:
        return hashlib.md5(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()

    def compute_delta(self, snapshot: dict) -> Optional[dict]:
        """Compare *snapshot* with the last state and return a changes dict.

        Returns ``None`` when nothing changed (caller should skip sending).
        Also updates internal tracking state.
        """
        changes: Dict[str, Any] = {}

        # --- overview ---
        overview = snapshot.get("overview")
        if overview:
            h = self._hash_dict(overview)
            if h != self._last_overview_hash:
                changes["overview"] = overview
                self._last_overview_hash = h

        # --- positions (diff by id) ---
        new_positions = snapshot.get("positions", [])
        new_pos_map: Dict[Any, dict] = {}
        for p in new_positions:
            pid = p.get("id") or p.get("symbol")
            new_pos_map[pid] = p

        pos_changes: List[dict] = []
        # Find removed positions
        for pid in list(self._last_positions.keys()):
            if pid not in new_pos_map:
                pos_changes.append({"id": pid, "_removed": True})
        # Find new or changed positions
        for pid, pdata in new_pos_map.items():
            old = self._last_positions.get(pid)
            if old is None or self._hash_dict(pdata) != self._hash_dict(old):
                pos_changes.append(pdata)
        if pos_changes:
            changes["positions"] = pos_changes
        self._last_positions = new_pos_map

        # --- orders (only new) ---
        new_orders = snapshot.get("orders", [])
        cur_order_ids = {o.get("id") for o in new_orders}
        new_order_items = [o for o in new_orders if o.get("id") not in self._last_order_ids]
        # Detect removed orders
        removed_order_ids = self._last_order_ids - cur_order_ids
        if new_order_items or removed_order_ids:
            changes["orders"] = new_order_items
            changes["orders_removed"] = list(removed_order_ids) if removed_order_ids else []
        self._last_order_ids = cur_order_ids

        # --- trades (only new) ---
        new_trades = snapshot.get("trades", [])
        cur_trade_ids = {t.get("id") for t in new_trades}
        new_trade_items = [t for t in new_trades if t.get("id") not in self._last_trade_ids]
        if new_trade_items:
            changes["trades"] = new_trade_items
        self._last_trade_ids = cur_trade_ids

        # --- ai_decisions (only new) ---
        new_ai = snapshot.get("ai_decisions", [])
        cur_ai_ids = {d.get("id") for d in new_ai}
        new_ai_items = [d for d in new_ai if d.get("id") not in self._last_ai_ids]
        if new_ai_items:
            changes["ai_decisions"] = new_ai_items
        self._last_ai_ids = cur_ai_ids

        # --- asset_curves (only when present, always send full) ---
        if "all_asset_curves" in snapshot:
            changes["all_asset_curves"] = snapshot["all_asset_curves"]

        # --- hyperliquid_state ---
        hl_state = snapshot.get("hyperliquid_state")
        if hl_state:
            h = self._hash_dict(hl_state)
            if h != self._last_hl_state_hash:
                changes["hyperliquid_state"] = hl_state
                self._last_hl_state_hash = h

        if not changes:
            return None
        return changes

    def record_full(self, snapshot: dict) -> None:
        """Record the full snapshot state after sending a full_snapshot."""
        overview = snapshot.get("overview")
        if overview:
            self._last_overview_hash = self._hash_dict(overview)
        positions = snapshot.get("positions", [])
        self._last_positions = {}
        for p in positions:
            pid = p.get("id") or p.get("symbol")
            self._last_positions[pid] = p
        self._last_order_ids = {o.get("id") for o in snapshot.get("orders", [])}
        self._last_trade_ids = {t.get("id") for t in snapshot.get("trades", [])}
        self._last_ai_ids = {d.get("id") for d in snapshot.get("ai_decisions", [])}
        hl_state = snapshot.get("hyperliquid_state")
        if hl_state:
            self._last_hl_state_hash = self._hash_dict(hl_state)


# Per-websocket snapshot trackers
_ws_trackers: Dict[int, Dict[WebSocket, _SnapshotTracker]] = {}


def _apply_delta_logic(tracker: _SnapshotTracker, snapshot_msg: dict) -> Optional[dict]:
    """Given a tracker and a full snapshot message, decide whether to send
    a full_snapshot or a delta (or None to skip).

    Returns the message dict to send, or None if nothing changed.
    """
    if tracker.should_send_full():
        seq = tracker.next_seq()
        tracker.record_full(snapshot_msg)
        out = dict(snapshot_msg)
        out["type"] = "full_snapshot"
        out["seq"] = seq
        return out

    # Try delta
    changes = tracker.compute_delta(snapshot_msg)
    if changes is None:
        return None

    seq = tracker.next_seq()
    delta_msg: Dict[str, Any] = {
        "type": "delta",
        "seq": seq,
        "changes": changes,
    }
    # Carry over trading_mode if present
    if "trading_mode" in snapshot_msg:
        delta_msg["trading_mode"] = snapshot_msg["trading_mode"]
    if "warning" in snapshot_msg:
        delta_msg["warning"] = snapshot_msg["warning"]
    return delta_msg


async def broadcast_asset_curve_update(timeframe: str = "1h"):
    """Broadcast asset curve updates to all connected clients"""
    db = SessionLocal()
    try:
        asset_curves = get_all_asset_curves_data(db, timeframe)
        await manager.broadcast_to_all({
            "type": "asset_curve_update",
            "timeframe": timeframe,
            "data": asset_curves
        })
    except Exception as e:
        logging.error(f"Failed to broadcast asset curve update: {e}")
    finally:
        db.close()


async def broadcast_arena_asset_update(update_payload: dict):
    """Broadcast aggregated arena asset update to all connected clients"""
    message = {
        "type": "arena_asset_update",
        **update_payload,
    }
    await manager.broadcast_to_all(message)


async def broadcast_trade_update(trade_data: dict):
    """Broadcast trade update to specific account when trade is executed

    Args:
        trade_data: Dictionary containing trade information including account_id
    """
    account_id = trade_data.get("account_id")
    if not account_id:
        logging.warning("broadcast_trade_update called without account_id")
        return

    try:
        await manager.send_to_account(account_id, {
            "type": "trade_update",
            "trade": trade_data
        })
    except Exception as e:
        logging.error(f"Failed to broadcast trade update: {e}")


async def broadcast_position_update(account_id: int, positions_data: list):
    """Broadcast position update to specific account when positions change

    Args:
        account_id: Account ID to send update to
        positions_data: List of position dictionaries
    """
    try:
        await manager.send_to_account(account_id, {
            "type": "position_update",
            "positions": positions_data
        })
    except Exception as e:
        logging.error(f"Failed to broadcast position update: {e}")


async def broadcast_model_chat_update(decision_data: dict):
    """Broadcast AI decision update to specific account

    Args:
        decision_data: Dictionary containing AI decision information including account_id
    """
    account_id = decision_data.get("account_id")
    if not account_id:
        logging.warning("broadcast_model_chat_update called without account_id")
        return

    try:
        await manager.send_to_account(account_id, {
            "type": "model_chat_update",
            "decision": decision_data
        })
    except Exception as e:
        logging.error(f"Failed to broadcast model chat update: {e}")


def get_all_asset_curves_data(
    db: Session,
    timeframe: str = "1h",
    trading_mode: str = "testnet",
    environment: Optional[str] = None,
    wallet_address: Optional[str] = None,
):
    """Get timeframe-based asset curve data for all accounts - WebSocket version

    Uses the new algorithm that draws curves by accounts and creates all-time lists.

    Args:
        timeframe: Time period for the curve, options: "5m", "1h", "1d"
        trading_mode: Trading mode filter, options: "paper", "testnet", "mainnet"
    """
    return get_all_asset_curves_data_new(
        db,
        timeframe,
        trading_mode,
        environment,
        wallet_address=wallet_address,
    )


async def _send_snapshot_optimized(db: Session, account_id: int):
    """Optimized version of snapshot that reduces expensive operations"""
    account = get_account(db, account_id)
    if not account:
        return
    
    positions = list_positions(db, account_id)
    orders = list_orders(db, account_id)
    trades = (
        db.query(Trade).filter(Trade.account_id == account_id).order_by(Trade.trade_time.desc()).limit(10).all()  # Reduced from 20 to 10
    )
    ai_decisions = []
    _adb = AnalyticsSessionLocal()
    try:
        ai_decisions = (
            _adb.query(AIDecisionLog).filter(AIDecisionLog.account_id == account_id).order_by(AIDecisionLog.decision_time.desc()).limit(10).all()
        )
    finally:
        _adb.close()
    
    # Use cached positions value calculation
    positions_value = calc_positions_value(db, account_id)

    overview = {
        "account": {
            "id": account.id,
            "user_id": account.user_id,
            "name": account.name,
            "account_type": account.account_type,
            "initial_capital": float(account.initial_capital),
            "current_cash": float(account.current_cash),
            "frozen_cash": float(account.frozen_cash),
        },
        "total_assets": positions_value + float(account.current_cash),
        "positions_value": positions_value,
    }
    
    # Optimize position enrichment - batch price fetching
    enriched_positions = []
    price_error_message = None
    
    # Group positions by symbol to reduce API calls
    unique_symbols = set((p.symbol, p.market) for p in positions)
    price_cache = {}
    
    # Fetch all unique prices in one go
    for symbol, market in unique_symbols:
        try:
            price = get_last_price(symbol, market)
            price_cache[(symbol, market)] = price
        except Exception as e:
            price_cache[(symbol, market)] = None
            error_msg = str(e)
            if "cookie" in error_msg.lower() and price_error_message is None:
                price_error_message = error_msg

    for p in positions:
        price = price_cache.get((p.symbol, p.market))
        enriched_positions.append({
            "id": p.id,
            "account_id": p.account_id,
            "symbol": p.symbol,
            "name": p.name,
            "market": p.market,
            "quantity": float(p.quantity),
            "available_quantity": float(p.available_quantity),
            "avg_cost": float(p.avg_cost),
            "last_price": float(price) if price is not None else None,
            "market_value": (float(price) * float(p.quantity)) if price is not None else None,
        })

    # Prepare response data - exclude expensive asset curve calculation for frequent updates
    response_data = {
        "type": "snapshot_fast",  # Different type to indicate this is optimized
        "overview": overview,
        "positions": enriched_positions,
        "orders": [
            {
                "id": o.id,
                "order_no": o.order_no,
                "user_id": o.account_id,
                "symbol": o.symbol,
                "name": o.name,
                "market": o.market,
                "side": o.side,
                "order_type": o.order_type,
                "price": float(o.price) if o.price is not None else None,
                "quantity": float(o.quantity),
                "filled_quantity": float(o.filled_quantity),
                "status": o.status,
            }
            for o in orders[:10]  # Reduced from 20 to 10
        ],
        "trades": [
            {
                "id": t.id,
                "order_id": t.order_id,
                "user_id": t.account_id,
                "symbol": t.symbol,
                "name": t.name,
                "market": t.market,
                "side": t.side,
                "price": float(t.price),
                "quantity": float(t.quantity),
                "commission": float(t.commission),
                "trade_time": str(t.trade_time),
            }
            for t in trades
        ],
        "ai_decisions": [
            {
                "id": d.id,
                "decision_time": str(d.decision_time),
                "reason": d.reason,
                "operation": d.operation,
                "symbol": d.symbol,
                "prev_portion": float(d.prev_portion),
                "target_portion": float(d.target_portion),
                "total_balance": float(d.total_balance),
                "executed": str(d.executed).lower() if d.executed else "false",
                "order_id": d.order_id,
            }
            for d in ai_decisions
        ],
        # Asset curves only included occasionally (every minute)
        "timestamp": datetime.now().timestamp()
    }
    
    # Only include expensive asset curve data every 60 seconds
    current_second = int(datetime.now().timestamp()) % 60
    if current_second < 10:  # First 10 seconds of each minute
        try:
            response_data["all_asset_curves"] = get_all_asset_curves_data(db, "1h")
            response_data["type"] = "snapshot_full"  # Indicate this includes full data
        except Exception as e:
            logger.error(f"Failed to get asset curves: {e}")

    if price_error_message:
        response_data["warning"] = {
            "type": "market_data_error",
            "message": price_error_message
        }

    await manager.send_to_account(account_id, response_data)


async def _send_snapshot_by_mode(db: Session, account_id: int, trading_mode: str):
    """
    Send snapshot based on trading mode

    Args:
        db: Database session
        account_id: Account ID
        trading_mode: "paper", "testnet", or "mainnet"
    """
    try:
        if trading_mode == "paper":
            # Use traditional paper trading snapshot
            await _send_snapshot(db, account_id)
        elif trading_mode in ["testnet", "mainnet"]:
            # Use Hyperliquid real-time snapshot
            await _send_hyperliquid_snapshot(db, account_id, trading_mode)
        else:
            logging.warning(f"Invalid trading_mode: {trading_mode}, falling back to paper")
            await _send_snapshot(db, account_id)  # Fallback to paper
    except Exception as e:
        logging.error(f"_send_snapshot_by_mode failed: {e}", exc_info=True)
        # Try sending paper trading snapshot as emergency fallback
        try:
            await _send_snapshot(db, account_id)
        except:
            pass


async def _send_hyperliquid_snapshot(db: Session, account_id: int, environment: str):
    """
    Send Hyperliquid snapshot, preferring cached data to avoid unnecessary API calls.

    Args:
        db: Database session
        account_id: Account ID
        environment: "testnet" or "mainnet"
    """
    account = get_account(db, account_id)
    if not account:
        logging.error(f"Account {account_id} not found")
        return

    # Check if wallet exists for this environment (multi-wallet architecture)
    from database.models import HyperliquidWallet
    wallet = db.query(HyperliquidWallet).filter(
        HyperliquidWallet.account_id == account_id,
        HyperliquidWallet.environment == environment
    ).first()

    if not wallet:
        # No wallet configured - fallback to paper trading
        logging.info(f"No {environment} wallet configured for account {account.name} (ID: {account_id}), using paper trading")
        await _send_snapshot(db, account_id)
        return

    cached_state = get_cached_account_state(account_id, environment, max_age_seconds=HYPERLIQUID_SNAPSHOT_CACHE_TTL)
    cached_positions = get_cached_positions(account_id, environment, max_age_seconds=HYPERLIQUID_SNAPSHOT_CACHE_TTL)
    account_state = cached_state["data"] if cached_state else None
    positions_data = cached_positions["data"] if cached_positions else None
    wallet_address = None
    if isinstance(account_state, dict):
        wallet_address = account_state.get("wallet_address")

    data_source = "cache"
    client = None

    if account_state is None or positions_data is None:
        try:
            client = get_hyperliquid_client(db, account_id, override_environment=environment)
        except Exception as e:
            logging.warning(f"Failed to initialize Hyperliquid client for account {account.name} ({environment}): {e}")
            # Don't send error to frontend, just log and skip
            return

        try:
            account_state = client.get_account_state(db)
            positions_data = client.get_positions(db)
            wallet_address = client.wallet_address
            data_source = "live"
        except Exception as e:
            logging.error(f"Failed to fetch Hyperliquid data for account {account_id}: {e}", exc_info=True)
            await manager.send_to_account(account_id, {
                "type": "error",
                "message": f"Failed to fetch Hyperliquid data: {str(e)}"
            })
            return
    else:
        # Cache hit but wallet missing? fall back to client for metadata only.
        if wallet_address is None:
            try:
                client = get_hyperliquid_client(db, account_id)
                if client.environment == environment:
                    wallet_address = client.wallet_address
            except Exception:
                wallet_address = None

    if account_state is None or positions_data is None:
        logging.error(f"Hyperliquid snapshot missing state or positions for account {account_id}")
        return

    wallet_address = wallet_address or account_state.get("wallet_address")

    try:
        # Transform Hyperliquid data to frontend format
        overview = {
            "account": {
                "id": account.id,
                "user_id": account.user_id,
                "name": account.name,
                "account_type": f"hyperliquid_{environment}",
                "initial_capital": float(account.initial_capital),
                # Use Hyperliquid balance instead of local database
                "current_cash": account_state.get("available_balance", 0),
                "frozen_cash": account_state.get("used_margin", 0),
            },
            "total_assets": account_state.get("total_equity", 0),
            "positions_value": sum(
                abs(p.get("position_value", 0)) for p in positions_data
            ),
        }

        # Transform Hyperliquid positions to frontend format
        enriched_positions = []
        for p in positions_data:
            enriched_positions.append({
                "id": 0,  # Hyperliquid positions don't have local DB ID
                "account_id": account_id,
                "symbol": p.get("coin", ""),
                "name": p.get("coin", ""),
                "market": "HYPERLIQUID_PERP",
                "quantity": abs(p.get("szi", 0)),  # Absolute size
                "available_quantity": abs(p.get("szi", 0)),
                "avg_cost": p.get("entry_px", 0),
                "last_price": None,  # Can fetch from market data if needed
                "market_value": p.get("position_value", 0),
                "current_value": p.get("position_value", 0),
                "unrealized_pnl": p.get("unrealized_pnl", 0),
                "leverage": p.get("leverage", 1),
                "side": "LONG" if p.get("szi", 0) > 0 else "SHORT",
            })

        # Get orders and trades from local database (filtered by environment)
        orders = list_orders(db, account_id)
        # Filter orders by hyperliquid_environment
        hyperliquid_orders = [
            o for o in orders
            if o.hyperliquid_environment == environment
        ]

        trades = (
            db.query(Trade)
            .filter(Trade.account_id == account_id)
            .filter(Trade.hyperliquid_environment == environment)
            .order_by(Trade.trade_time.desc())
            .limit(20)
            .all()
        )

        ai_decisions = []
        _adb = AnalyticsSessionLocal()
        try:
            # 修复（2026-07-03）：前端传 account_id=5 但 FullAuto 实际用 paper_account_id=14。
            # 查 FullAuto session 获取真实 paper_account_id，用它查决策日志。
            _real_acct_id = account_id
            try:
                from backend.database.models import FullAutoSession
                _fa = db.query(FullAutoSession).filter(
                    FullAutoSession.status == "running"
                ).order_by(FullAutoSession.id.desc()).first()
                if _fa and _fa.paper_account_id:
                    _real_acct_id = _fa.paper_account_id
            except Exception:
                pass
            ai_decisions = (
                _adb.query(AIDecisionLog)
                .filter(AIDecisionLog.account_id == _real_acct_id)
                .order_by(AIDecisionLog.decision_time.desc())
                .limit(20)
                .all()
            )
        finally:
            _adb.close()

        # Prepare response data
        response_data = {
            "type": "snapshot",
            "trading_mode": environment,
            "overview": overview,
            "positions": enriched_positions,
            "orders": [
                {
                    "id": o.id,
                    "order_no": o.order_no,
                    "user_id": o.account_id,
                    "symbol": o.symbol,
                    "name": o.name,
                    "market": o.market,
                    "side": o.side,
                    "order_type": o.order_type,
                    "price": float(o.price) if o.price is not None else None,
                    "quantity": float(o.quantity),
                    "filled_quantity": float(o.filled_quantity),
                    "status": o.status,
                }
                for o in hyperliquid_orders[:20]
            ],
            "trades": [
                {
                    "id": t.id,
                    "order_id": t.order_id,
                    "user_id": t.account_id,
                    "symbol": t.symbol,
                    "name": t.name,
                    "market": t.market,
                    "side": t.side,
                    "price": float(t.price),
                    "quantity": float(t.quantity),
                    "commission": float(t.commission),
                    "trade_time": str(t.trade_time),
                }
                for t in trades
            ],
            "ai_decisions": [
                {
                    "id": d.id,
                    "decision_time": str(d.decision_time),
                    "reason": d.reason,
                    "operation": d.operation,
                    "symbol": d.symbol,
                    "prev_portion": float(d.prev_portion),
                    "target_portion": float(d.target_portion),
                    "total_balance": float(d.total_balance),
                    "executed": str(d.executed).lower() if d.executed else "false",
                    "order_id": d.order_id,
                }
                for d in ai_decisions
            ],
            # PERFORMANCE: Only include asset curves in first 10 seconds of each minute (expensive SQL aggregation)
            **(
                {"all_asset_curves": get_all_asset_curves_data(
                    db,
                    "1h",
                    trading_mode=environment,
                    environment=environment,
                )}
                if int(time.time()) % 60 < 10
                else {}
            ),
            "hyperliquid_state": {
                "environment": environment,
                "total_equity": account_state.get("total_equity", 0),
                "available_balance": account_state.get("available_balance", 0),
                "used_margin": account_state.get("used_margin", 0),
                "margin_usage_percent": account_state.get("margin_usage_percent", 0),
                "source": data_source,
                "wallet_address": wallet_address,
            }
        }

        await manager.send_to_account(account_id, response_data)

    except Exception as e:
        logging.error(f"Failed to get Hyperliquid snapshot: {e}", exc_info=True)
        await manager.send_to_account(account_id, {
            "type": "error",
            "message": f"Failed to fetch Hyperliquid data: {str(e)}"
        })


async def _send_snapshot(db: Session, account_id: int):
    account = get_account(db, account_id)
    if not account:
        return
    positions = list_positions(db, account_id)
    orders = list_orders(db, account_id)
    trades = (
        db.query(Trade).filter(Trade.account_id == account_id).order_by(Trade.trade_time.desc()).limit(20).all()
    )
    ai_decisions = []
    _adb = AnalyticsSessionLocal()
    try:
        ai_decisions = (
            _adb.query(AIDecisionLog).filter(AIDecisionLog.account_id == account_id).order_by(AIDecisionLog.decision_time.desc()).limit(20).all()
        )
    finally:
        _adb.close()
    positions_value = calc_positions_value(db, account_id)

    overview = {
        "account": {
            "id": account.id,
            "user_id": account.user_id,
            "name": account.name,
            "account_type": account.account_type,
            "initial_capital": float(account.initial_capital),
            "current_cash": float(account.current_cash),
            "frozen_cash": float(account.frozen_cash),
        },
        "total_assets": positions_value + float(account.current_cash),
        "positions_value": positions_value,
    }
    # enrich positions with latest price and market value
    enriched_positions = []
    price_error_message = None

    for p in positions:
        try:
            price = get_last_price(p.symbol, p.market)
        except Exception as e:
            price = None
            # Collect price retrieval error messages, especially cookie-related errors
            error_msg = str(e)
            if "cookie" in error_msg.lower() and price_error_message is None:
                price_error_message = error_msg

        enriched_positions.append({
            "id": p.id,
            "account_id": p.account_id,
            "symbol": p.symbol,
            "name": p.name,
            "market": p.market,
            "quantity": float(p.quantity),
            "available_quantity": float(p.available_quantity),
            "avg_cost": float(p.avg_cost),
            "last_price": float(price) if price is not None else None,
            "market_value": (float(price) * float(p.quantity)) if price is not None else None,
        })

    # Prepare response data
    response_data = {
        "type": "snapshot",
        "overview": overview,
        "positions": enriched_positions,
        "orders": [
            {
                "id": o.id,
                "order_no": o.order_no,
                "user_id": o.account_id,
                "symbol": o.symbol,
                "name": o.name,
                "market": o.market,
                "side": o.side,
                "order_type": o.order_type,
                "price": float(o.price) if o.price is not None else None,
                "quantity": float(o.quantity),
                "filled_quantity": float(o.filled_quantity),
                "status": o.status,
            }
            for o in orders[:20]
        ],
        "trades": [
            {
                "id": t.id,
                "order_id": t.order_id,
                "user_id": t.account_id,
                "symbol": t.symbol,
                "name": t.name,
                "market": t.market,
                "side": t.side,
                "price": float(t.price),
                "quantity": float(t.quantity),
                "commission": float(t.commission),
                "trade_time": str(t.trade_time),
            }
            for t in trades
        ],
        "ai_decisions": [
            {
                "id": d.id,
                "decision_time": str(d.decision_time),
                "reason": d.reason,
                "operation": d.operation,
                "symbol": d.symbol,
                "prev_portion": float(d.prev_portion),
                "target_portion": float(d.target_portion),
                "total_balance": float(d.total_balance),
                "executed": str(d.executed).lower() if d.executed else "false",
                "order_id": d.order_id,
            }
            for d in ai_decisions
        ],
        "all_asset_curves": get_all_asset_curves_data(db, "1h"),
    }

    if price_error_message:
        response_data["warning"] = {
            "type": "market_data_error",
            "message": price_error_message
        }

    await manager.send_to_account(account_id, response_data)


async def websocket_endpoint(websocket: WebSocket):
    client_host = websocket.client.host if websocket.client else "unknown"
    logging.info(f"[WS] New WebSocket connection from {client_host}")
    # P2 修复（2026-06-23）：accept() 可能因客户端在握手期间断开而抛异常，
    # 原代码未保护 → 进入 receive 循环后立即报 "WebSocket is not connected.
    # Need to call accept first"（ERROR 级别触发 P2 告警）。
    try:
        await websocket.accept()
    except Exception as accept_err:
        # 握手失败属正常客户端行为（刷新页面/网络抖动），不是服务端错误，
        # 降级为 INFO，避免每小时刷屏 P2 告警。
        logging.info(f"[WS] WebSocket accept 失败（客户端可能在握手期间断开）: {accept_err}")
        try:
            await websocket.close()
        except Exception:
            pass
        return
    logging.info(f"[WS] WebSocket connection accepted from {client_host}")
    try:
        manager.set_event_loop(asyncio.get_running_loop())
    except RuntimeError:
        pass
    account_id: Optional[int] = None
    user_id: Optional[int] = None  # Initialize user_id to avoid UnboundLocalError

    try:
        while True:
            try:
                data = await websocket.receive_text()
            except WebSocketDisconnect:
                # Client disconnected gracefully
                logging.info("[WS] client disconnected (WebSocketDisconnect)")
                break
            except Exception as e:
                # P2 修复：客户端异常断开（RST/掉线/握手失败）属正常行为，
                # 原来记 ERROR 触发 P2 告警。降级为 DEBUG（仅保留排查痕迹）。
                # 常见信息："WebSocket is not connected. Need to call accept first."
                logging.info(f"[WS] receive_text 异常断开: {type(e).__name__}: {e}")
                break
                
            try:
                msg = json.loads(data)
            except json.JSONDecodeError as e:
                logging.error(f"Invalid JSON received: {e}")
                try:
                    await websocket.send_text(json.dumps({"type": "error", "message": "Invalid JSON format"}))
                except Exception as e:
                    logger.warning(f"WebSocket broadcast error: {e}")
                    break
                continue
            kind = msg.get("type")
            logging.info(f"[WS] Received message type: {kind}")
            db: Session = SessionLocal()
            try:
                if kind == "bootstrap":
                    # [2026-08-06 修复] WS 升级不经过 HTTP 租户中间件，且后台无 ContextVar
                    # 身份 → RLS fail-closed 拒绝 accounts 插入（InsufficientPrivilege），
                    # bootstrap 直接崩掉整个 WS 连接（前端卡在 Connecting to trading server）。
                    # 与 llm_config_service / full_auto 后台线程同款修复：连接级设 admin GUC，
                    # 不动 ContextVar；后续 get_or_create_user / default account / snapshot 同连接生效。
                    try:
                        db.connection().exec_driver_sql("SET app.is_admin = 'on'")
                    except Exception:
                        pass
                    #  mode: Create or get default default user
                    username = msg.get("username", "default")
                    trading_mode = msg.get("trading_mode", "testnet")  # Default to testnet
                    logging.info(f"[WS] Bootstrap request: username={username}, trading_mode={trading_mode}")
                    user = get_or_create_user(db, username)
                    
                    # Get existing account for this user
                    account = get_or_create_default_account(
                        db,
                        user.id,
                        account_name=f"{username} AI Trader",
                        initial_capital=float(msg.get("initial_capital", 100000))
                    )

                    if not account:
                        # Allow connection but with no account (frontend will handle this)
                        account_id = None
                    else:
                        account_id = account.id

                    # Register the connection (handles None account_id gracefully)
                    manager.register(account_id, websocket)
                    logging.info(f"[WS] Registered connection for account_id={account_id}")

                    # Send bootstrap confirmation with account info
                    try:
                        if account:
                            logging.info(f"[WS] Sending bootstrap_ok for account {account.id}")
                            await manager.send_to_account(account_id, {
                                "type": "bootstrap_ok",
                                "user": {"id": user.id, "username": user.username},
                                "account": {"id": account.id, "name": account.name, "user_id": account.user_id}
                            })
                            logging.info(f"[WS] Sending {trading_mode} snapshot for account {account.id}")
                            # Use trading mode aware snapshot function
                            await _send_snapshot_by_mode(db, account_id, trading_mode)
                            logging.info(f"[WS] Bootstrap complete for account {account.id}")
                        else:
                            # Send bootstrap with no account info
                            await websocket.send_text(json.dumps({
                                "type": "bootstrap_ok",
                                "user": {"id": user.id, "username": user.username},
                                "account": None
                            }))
                    except Exception as e:
                        logging.error(f"Failed to send bootstrap response: {e}")
                        break
                elif kind == "subscribe":
                    # subscribe existing user_id
                    uid = int(msg.get("user_id"))
                    u = get_user(db, uid)
                    if not u:
                        try:
                            await websocket.send_text(json.dumps({"type": "error", "message": "user not found"}))
                        except Exception as e:
                            logger.warning(f"WebSocket error sending user not found: {e}")
                            break
                        continue
                    user_id = uid
                    manager.register(user_id, websocket)
                    try:
                        await _send_snapshot(db, user_id)
                    except Exception as e:
                        logging.error(f"Failed to send snapshot: {e}")
                        break
                elif kind == "switch_user":
                    # Switch to different user account
                    target_username = msg.get("username")
                    if not target_username:
                        await websocket.send_text(json.dumps({"type": "error", "message": "username required"}))
                        continue

                    # Unregister from current user if any
                    if user_id is not None:
                        manager.unregister(user_id, websocket)

                    # Find target user
                    target_user = get_or_create_user(db, target_username, 100000.0)
                    user_id = target_user.id

                    # Register to new user
                    manager.register(user_id, websocket)

                    # Send confirmation and snapshot
                    await manager.send_to_account(user_id, {
                        "type": "user_switched",
                        "user": {
                            "id": target_user.id,
                            "username": target_user.username
                        }
                    })
                    await _send_snapshot(db, user_id)
                elif kind == "switch_account":
                    # Switch to different account by ID
                    target_account_id = msg.get("account_id")
                    if not target_account_id:
                        await websocket.send_text(json.dumps({"type": "error", "message": "account_id required"}))
                        continue

                    # Unregister from current account if any
                    if account_id is not None:
                        manager.unregister(account_id, websocket)

                    # Get target account
                    target_account = get_account(db, target_account_id)
                    if not target_account:
                        await websocket.send_text(json.dumps({"type": "error", "message": "account not found"}))
                        continue

                    account_id = target_account.id
                    
                    # Register to new account
                    manager.register(account_id, websocket)

                    # Send confirmation and snapshot
                    await manager.send_to_account(account_id, {
                        "type": "account_switched",
                        "account": {
                            "id": target_account.id,
                            "user_id": target_account.user_id,
                            "name": target_account.name
                        }
                    })
                    await _send_snapshot(db, account_id)
                elif kind == "get_snapshot":
                    if account_id is not None:
                        # Get trading mode from request (default to "testnet")
                        trading_mode = msg.get("trading_mode", "testnet")
                        logging.info(f"Received get_snapshot request: account_id={account_id}, trading_mode={trading_mode}")
                        await _send_snapshot_by_mode(db, account_id, trading_mode)
                elif kind == "get_asset_curve":
                    # Get asset curve data with specific timeframe and trading mode
                    timeframe = msg.get("timeframe", "1h")
                    trading_mode = msg.get("trading_mode", "testnet")
                    environment = msg.get("environment")
                    if timeframe not in ["5m", "1h", "1d"]:
                        await websocket.send_text(json.dumps({"type": "error", "message": "Invalid timeframe. Must be 5m, 1h, or 1d"}))
                        continue

                    asset_curves = get_all_asset_curves_data(
                        db,
                        timeframe,
                        trading_mode,
                        environment=environment,
                    )
                    await websocket.send_text(json.dumps({
                        "type": "asset_curve_data",
                        "timeframe": timeframe,
                        "trading_mode": trading_mode,
                        "environment": environment,
                        "data": asset_curves
                    }))
                elif kind == "place_order":
                    if account_id is None:
                        await websocket.send_text(json.dumps({"type": "error", "message": "not authenticated"}))
                        continue

                    try:
                        # Import the order creation service
                        from backend.services.order_matching import create_order

                        # Get account and user object
                        account = get_account(db, account_id)
                        if not account:
                            await websocket.send_text(json.dumps({"type": "error", "message": "account not found"}))
                            continue

                        user = get_user(db, account.user_id)
                        if not user:
                            await websocket.send_text(json.dumps({"type": "error", "message": "user not found"}))
                            continue

                        # Extract order parameters
                        symbol = msg.get("symbol")
                        name = msg.get("name", symbol)  # Use symbol as name if not provided
                        market = msg.get("market", "CRYPTO")
                        side = msg.get("side")
                        order_type = msg.get("order_type")
                        price = msg.get("price")
                        quantity = msg.get("quantity")
                        # 阶段 3.2: 执行算法透传（虚拟撮合，仅留痕）
                        algo = msg.get("algo")
                        algo_config = msg.get("algo_config")

                        # Validate required parameters
                        if not all([symbol, side, order_type, quantity]):
                            await websocket.send_text(json.dumps({"type": "error", "message": "missing required parameters"}))
                            continue

                        # Convert quantity to float (crypto supports fractional quantities)
                        try:
                            quantity = float(quantity)
                        except (ValueError, TypeError):
                            await websocket.send_text(json.dumps({"type": "error", "message": "invalid quantity"}))
                            continue

                        # Create the order
                        order = create_order(
                            db=db,
                            account=account,
                            symbol=symbol,
                            name=name,
                            side=side,
                            order_type=order_type,
                            price=price,
                            quantity=quantity
                        )

                        # Commit the order
                        db.commit()

                        if algo and str(algo).upper() != "MARKET":
                            logger.info(
                                f"[WS] place_order {symbol} algo={algo} config={algo_config} "
                                f"(order_matching 虚拟撮合，算法切片不适用，仅留痕)"
                            )

                        # Send success response
                        await manager.send_to_account(account_id, {"type": "order_pending", "order_id": order.id})

                        # Send updated snapshot
                        await _send_snapshot(db, account_id)

                    except ValueError as e:
                        # Business logic errors (insufficient funds, etc.)
                        try:
                            await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
                        except Exception as e:
                            logger.warning(f"WebSocket error sending business logic error: {e}")
                            break
                    except Exception as e:
                        # Unexpected errors
                        import traceback
                        logger.info(f"Order placement error: {e}")
                        logger.info(traceback.format_exc())
                        try:
                            await websocket.send_text(json.dumps({"type": "error", "message": f"order placement failed: {str(e)}"}))
                        except:
                            break
                elif kind == "ping":
                    try:
                        await websocket.send_text(json.dumps({"type": "pong"}))
                    except Exception as e:
                        logger.warning(f"WebSocket error sending unknown message error: {e}")
                        break
                # ── AI学习系统整合: 3种新消息类型 (v3 整改: 真实订阅并广播) ──
                elif kind == "subscribe_drl_advice":
                    try:
                        from backend.services.ws_broadcast import ws_broadcast_hub, TOPIC_DRL
                        ws_broadcast_hub.subscribe(websocket, TOPIC_DRL)
                        await websocket.send_text(json.dumps({
                            "type": "drl_advice_update",
                            "status": "subscribed",
                            "subscriber_count": ws_broadcast_hub.subscriber_count(TOPIC_DRL),
                        }))
                    except Exception as e:
                        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
                elif kind == "unsubscribe_drl_advice":
                    try:
                        from backend.services.ws_broadcast import ws_broadcast_hub, TOPIC_DRL
                        ws_broadcast_hub.unsubscribe(websocket, TOPIC_DRL)
                        await websocket.send_text(json.dumps({"type": "drl_advice_update", "status": "unsubscribed"}))
                    except Exception as e:
                        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
                elif kind == "subscribe_kelly_allocation":
                    try:
                        from backend.services.ws_broadcast import ws_broadcast_hub, TOPIC_KELLY
                        ws_broadcast_hub.subscribe(websocket, TOPIC_KELLY)
                        await websocket.send_text(json.dumps({
                            "type": "kelly_allocation_update",
                            "status": "subscribed",
                            "subscriber_count": ws_broadcast_hub.subscriber_count(TOPIC_KELLY),
                        }))
                    except Exception as e:
                        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
                elif kind == "unsubscribe_kelly_allocation":
                    try:
                        from backend.services.ws_broadcast import ws_broadcast_hub, TOPIC_KELLY
                        ws_broadcast_hub.unsubscribe(websocket, TOPIC_KELLY)
                        await websocket.send_text(json.dumps({"type": "kelly_allocation_update", "status": "unsubscribed"}))
                    except Exception as e:
                        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
                elif kind == "subscribe_evolution_progress":
                    try:
                        from backend.services.ws_broadcast import ws_broadcast_hub, TOPIC_EVOLUTION
                        ws_broadcast_hub.subscribe(websocket, TOPIC_EVOLUTION)
                        await websocket.send_text(json.dumps({
                            "type": "evolution_progress_update",
                            "status": "subscribed",
                            "subscriber_count": ws_broadcast_hub.subscriber_count(TOPIC_EVOLUTION),
                        }))
                    except Exception as e:
                        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
                elif kind == "unsubscribe_evolution_progress":
                    try:
                        from backend.services.ws_broadcast import ws_broadcast_hub, TOPIC_EVOLUTION
                        ws_broadcast_hub.unsubscribe(websocket, TOPIC_EVOLUTION)
                        await websocket.send_text(json.dumps({"type": "evolution_progress_update", "status": "unsubscribed"}))
                    except Exception as e:
                        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
                # ── P2-1 学习闭环协调器状态订阅 ──
                elif kind == "subscribe_coordinator_status":
                    try:
                        from backend.services.ws_broadcast import ws_broadcast_hub, TOPIC_COORDINATOR
                        ws_broadcast_hub.subscribe(websocket, TOPIC_COORDINATOR)
                        await websocket.send_text(json.dumps({
                            "type": "coordinator_status_update",
                            "status": "subscribed",
                            "subscriber_count": ws_broadcast_hub.subscriber_count(TOPIC_COORDINATOR),
                        }))
                    except Exception as e:
                        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
                elif kind == "unsubscribe_coordinator_status":
                    try:
                        from backend.services.ws_broadcast import ws_broadcast_hub, TOPIC_COORDINATOR
                        ws_broadcast_hub.unsubscribe(websocket, TOPIC_COORDINATOR)
                        await websocket.send_text(json.dumps({"type": "coordinator_status_update", "status": "unsubscribed"}))
                    except Exception as e:
                        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
                # ── K 线实时推送订阅 ──
                elif kind == "subscribe_klines":
                    try:
                        from backend.services.ws_broadcast import ws_broadcast_hub, TOPIC_KLINES
                        ws_broadcast_hub.subscribe(websocket, TOPIC_KLINES)
                        await websocket.send_text(json.dumps({
                            "type": "kline_update",
                            "status": "subscribed",
                            "symbol": msg.get("symbol", ""),
                            "period": msg.get("period", ""),
                            "subscriber_count": ws_broadcast_hub.subscriber_count(TOPIC_KLINES),
                        }))
                    except Exception as e:
                        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
                elif kind == "unsubscribe_klines":
                    try:
                        from backend.services.ws_broadcast import ws_broadcast_hub, TOPIC_KLINES
                        ws_broadcast_hub.unsubscribe(websocket, TOPIC_KLINES)
                        await websocket.send_text(json.dumps({"type": "kline_update", "status": "unsubscribed"}))
                    except Exception as e:
                        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
                # ── 统一进化学习内核：血缘事件实时订阅（进化中枢实时管线）──
                elif kind == "subscribe_learning_events":
                    try:
                        from backend.services.ws_broadcast import ws_broadcast_hub, TOPIC_LEARNING
                        ws_broadcast_hub.subscribe(websocket, TOPIC_LEARNING)
                        await websocket.send_text(json.dumps({
                            "type": "learning_event",
                            "status": "subscribed",
                            "subscriber_count": ws_broadcast_hub.subscriber_count(TOPIC_LEARNING),
                        }))
                    except Exception as e:
                        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
                elif kind == "unsubscribe_learning_events":
                    try:
                        from backend.services.ws_broadcast import ws_broadcast_hub, TOPIC_LEARNING
                        ws_broadcast_hub.unsubscribe(websocket, TOPIC_LEARNING)
                        await websocket.send_text(json.dumps({"type": "learning_event", "status": "unsubscribed"}))
                    except Exception as e:
                        await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
                else:
                    try:
                        await websocket.send_text(json.dumps({"type": "error", "message": "unknown message"}))
                    except Exception as e:
                        logger.warning(f"WebSocket error sending unknown message: {e}")
                        break
            finally:
                db.close()
    except WebSocketDisconnect:
        if account_id is not None:
            manager.unregister(account_id, websocket)
        if user_id is not None:
            manager.unregister(user_id, websocket)
        try:
            from backend.services.ws_broadcast import ws_broadcast_hub
            ws_broadcast_hub.unsubscribe_all(websocket)
        except Exception:
            pass
        return
    finally:
        # Clean up resources when user disconnects
        if account_id is not None:
            manager.unregister(account_id, websocket)
        if user_id is not None:
            manager.unregister(user_id, websocket)
        try:
            from backend.services.ws_broadcast import ws_broadcast_hub
            ws_broadcast_hub.unsubscribe_all(websocket)
        except Exception:
            pass
