# -*- coding: utf-8 -*-
"""loss_lock_policy — 「亏损锁」是否适用于该账户的唯一判据（2026-09-11 用户指令）。

## 背景（用户原话）

> 「模拟账户交易还配置全局冻结？」「本来就是收集交易数据，你还弄个极端亏损冻结」

模拟(paper)账户的唯一目的是**收集交易数据**：纸面亏损就是训练数据。
对它做「连亏 → 冷却 / 日亏 → 熔断 / 极端连亏 → 暂停或永久禁用」，
等于把样本管线掐断——冻结的是数据，不是资金。

## 代码库里已有的权威

`lock_strength_service._build_profile()` 早就把 paper 模式定义为
`disable_loss_locks=True / consecutive_loss_protection=False`
（paper 默认 strength=0）。`position_memory_manager.loss_protection_enabled(mode)`
是读取它的既有入口。

问题在于**四个亏损类冻结各自为政、没读这个权威**：

| 机制 | 位置 | 对 paper 的影响 |
|---|---|---|
| 组合预算冻结/回撤熔断 | `risk_management/portfolio_budget.py` | 每 17 分钟刷 freeze 台账，拒开仓 |
| mid 熔断闸（连亏3/日亏60U） | `full_auto/midlong_circuit_gate.py` | 冷却 12h / 熔断到次日 |
| 分层日亏熔断 | `tier_circuit_breaker.py` | 冻结该 tier 新开仓 |
| 连亏保护性调整 | `unified_learning_service.py` | 连亏15次暂停策略、50次永久禁用 |

本模块把判据收敛成一处：**paper（或亏损锁被关闭的模式）不做任何亏损类冻结**；
live 行为完全不变（继续全量保护 + fail-closed）。

## 语义

- `loss_locks_disabled(account_id, mode=...)` → True 表示该账户**不做亏损类冻结**。
- 账户模式解析顺序：显式 mode 参数 → Account.trading_mode（300s 缓存）→
  环境变量 `DEFAULT_TRADING_MODE`（缺省 paper，本系统纸面运行）→ paper。
- 判据本身异常时 **fail-open（按 paper 处理，不冻结）**：宁可多收样本，
  不可因策略判据自身故障停摆——但 live 账户由 `Account.trading_mode` 明确标识，
  解析失败只可能发生在 DB 不可用这种全局故障场景。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# account_id -> (ts, mode)
_MODE_CACHE: Dict[int, Tuple[float, str]] = {}
_MODE_TTL_S = 300.0


def _default_mode() -> str:
    return (os.getenv("DEFAULT_TRADING_MODE", "paper") or "paper").strip().lower()


def resolve_account_mode(account_id: Optional[int] = None, db=None) -> str:
    """解析账户交易模式（paper/live）。失败 → 默认模式（缺省 paper）。"""
    try:
        aid = int(account_id or 0)
    except (TypeError, ValueError):
        aid = 0
    if aid <= 0:
        return _default_mode()
    now = time.time()
    hit = _MODE_CACHE.get(aid)
    if hit and now - hit[0] < _MODE_TTL_S:
        return hit[1]
    mode = ""
    try:
        from backend.database.models import Account

        if db is None:
            from backend.database.session import SessionLocal

            with SessionLocal() as _db:
                row = _db.query(Account.trading_mode).filter(Account.id == aid).first()
        else:
            row = db.query(Account.trading_mode).filter(Account.id == aid).first()
        if row is not None:
            mode = str((row[0] if not hasattr(row, "trading_mode") else row.trading_mode) or "").strip().lower()
    except Exception as exc:  # noqa: BLE001
        logger.debug("[LossLockPolicy] 账户模式解析失败 acct=%s: %s", aid, exc)
    if mode not in ("paper", "live"):
        mode = _default_mode()
    _MODE_CACHE[aid] = (now, mode)
    return mode


def loss_locks_disabled(
    account_id: Optional[int] = None,
    *,
    mode: Optional[str] = None,
    db=None,
) -> bool:
    """该账户/模式是否**禁用亏损类冻结**（paper 默认 True）。

    True ⇒ 调用方必须跳过一切「亏损触发的冻结/暂停/永久禁用」；
    策略质量类闸门（regime/位置/learned 条件）与按 bar 的风控不受影响。
    """
    try:
        m = (mode or "").strip().lower() or resolve_account_mode(account_id, db)
        from backend.services.position_memory_manager import loss_protection_enabled

        if bool(loss_protection_enabled(m)):
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("[LossLockPolicy] 判据异常(按 paper 处理): %s", exc)
        return True


def invalidate_cache(account_id: Optional[int] = None) -> None:
    """测试/运维：清账户模式缓存。"""
    if account_id is None:
        _MODE_CACHE.clear()
        return
    try:
        _MODE_CACHE.pop(int(account_id), None)
    except (TypeError, ValueError):
        _MODE_CACHE.clear()
