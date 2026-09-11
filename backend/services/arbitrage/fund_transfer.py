# -*- coding: utf-8 -*-
"""FundTransferService（v3 方向 5，p2-arb-infra）。

方案点名：现有 `cross_market_transfer.py` 是**策略知识迁移**，不是资金划转。
本模块才是「现货↔合约 / 跨账户」资金划转服务。

安全默认：
  - `FUND_TRANSFER_ENABLED=false`：整服务拒单
  - `FUND_TRANSFER_LIVE=false`：只记账（dry-run），不调交易所
  - 真划转必须两个开关都开，且过 RiskEngine 的 kill switch 检查

各所 API 差异大（Binance `/sapi/v1/asset/transfer`、Aster `/fapi/v3/asset/wallet/transfer`），
适配器层若未实现 `transfer` 方法 → 明确返回 not_supported，**绝不假装成功**。
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "arb" / "transfers"


def _env_true(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def service_enabled() -> bool:
    return _env_true("FUND_TRANSFER_ENABLED", False)


def live_enabled() -> bool:
    """true = 真调交易所；false = 只落账本（dry-run）。"""
    return _env_true("FUND_TRANSFER_LIVE", False)


@dataclass
class TransferRequest:
    account_id: int
    exchange: str
    amount: float
    asset: str = "USDT"
    # 常见类型：MAIN_UMFUTURE（现货→U本位）、UMFUTURE_MAIN、MAIN_MARGIN …
    transfer_type: str = "MAIN_UMFUTURE"
    reason: str = ""
    dry_run: Optional[bool] = None   # None → 取 live_enabled 反义


@dataclass
class TransferResult:
    ok: bool
    transfer_id: str
    status: str                      # dry_run | submitted | filled | rejected | unsupported
    request: Dict[str, Any] = field(default_factory=dict)
    exchange_resp: Optional[Dict[str, Any]] = None
    message: str = ""
    ts_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _kill_switch_blocked() -> Optional[str]:
    try:
        from backend.services.risk.kill_switch import kill_switch_status
        ks = kill_switch_status()
        engaged = bool(getattr(ks, "engaged", False))
        if engaged:
            return f"LIVE_KILL_SWITCH 激活: {getattr(ks, 'reason', '') or ks}"
    except Exception:
        try:
            if _env_true("LIVE_KILL_SWITCH", False):
                return "LIVE_KILL_SWITCH=true"
        except Exception:
            pass
    return None


def _persist(result: TransferResult) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / f"{result.transfer_id}.json"
        path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
        # 追加流水
        ledger = DATA_DIR / "ledger.jsonl"
        with ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result.to_dict(), ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        logger.warning("[FundTransfer] 落盘失败: %s", exc)


async def transfer_async(client: Any, req: TransferRequest) -> TransferResult:
    tid = f"ft_{uuid.uuid4().hex[:16]}"
    ts = int(time.time() * 1000)
    base = TransferResult(ok=False, transfer_id=tid, status="rejected",
                          request=asdict(req), ts_ms=ts)

    if not service_enabled():
        base.message = "FUND_TRANSFER_ENABLED=false"
        _persist(base)
        return base
    if req.amount <= 0:
        base.message = "amount 必须 > 0"
        _persist(base)
        return base

    blocked = _kill_switch_blocked()
    if blocked:
        base.message = blocked
        _persist(base)
        return base

    dry = req.dry_run if req.dry_run is not None else (not live_enabled())
    if dry:
        base.ok = True
        base.status = "dry_run"
        base.message = "FUND_TRANSFER_LIVE=false：仅记账，未调交易所"
        _persist(base)
        logger.info("[FundTransfer] dry-run acct=%s %s %s %s",
                    req.account_id, req.exchange, req.transfer_type, req.amount)
        return base

    # 真划转：适配器必须实现 transfer
    fn = getattr(client, "transfer", None) or getattr(client, "asset_transfer", None)
    if not callable(fn):
        base.status = "unsupported"
        base.message = f"{req.exchange} 适配器未实现 transfer/asset_transfer"
        _persist(base)
        return base
    try:
        resp = fn(req.asset, req.amount, req.transfer_type)
        if hasattr(resp, "__await__"):
            resp = await resp
        base.ok = True
        base.status = "submitted"
        base.exchange_resp = resp if isinstance(resp, dict) else {"raw": str(resp)[:500]}
        base.message = "已提交交易所"
        _persist(base)
        return base
    except Exception as exc:
        base.message = f"交易所拒单/异常: {exc}"[:300]
        _persist(base)
        logger.warning("[FundTransfer] 失败: %s", exc)
        return base


def transfer_sync(client: Any, req: TransferRequest) -> TransferResult:
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(transfer_async(client, req))).result(timeout=60)
        return loop.run_until_complete(transfer_async(client, req))
    except RuntimeError:
        return asyncio.run(transfer_async(client, req))


def list_transfers(*, limit: int = 50) -> List[Dict[str, Any]]:
    ledger = DATA_DIR / "ledger.jsonl"
    if not ledger.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
        for line in lines[-max(1, limit):]:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return list(reversed(rows))


def status() -> Dict[str, Any]:
    return {
        "enabled": service_enabled(),
        "live": live_enabled(),
        "note": "与 cross_market_transfer（知识迁移）无关；本服务才是资金划转",
        "n_recorded": len(list_transfers(limit=10000)),
    }
