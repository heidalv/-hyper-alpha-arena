"""Strict Data Contract — 按 tier 校验 market_data 必填字段。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TIER_REQUIRED_FIELDS: Dict[str, List[str]] = {
    "short": ["price", "volatility_value"],
    "mid": ["price", "indicators_1h", "indicators_4h", "indicators_1d"],
    "long": ["price", "indicators_1d", "indicators_1w"],
}

TIER_OPTIONAL_ORCH: Dict[str, List[str]] = {
    "mid": ["orchestrator.mid_bias"],
    "long": ["orchestrator.long_bias"],
}


@dataclass
class DataContractResult:
    ok: bool
    missing: List[str]
    tier: str
    warn_only: bool = False

    @property
    def reason(self) -> str:
        if self.ok:
            return "ok"
        prefix = "[StrictData-WARN]" if self.warn_only else "[StrictData]"
        return f"{prefix} tier={self.tier} missing={','.join(self.missing)}"


def _get_nested(data: dict, path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _has_field(data: dict, key: str) -> bool:
    if "." in key:
        val = _get_nested(data, key)
    else:
        val = data.get(key)
    if val is None:
        return False
    if isinstance(val, dict) and not val:
        return False
    if key == "price" and float(val or 0) <= 0:
        return False
    return True


def check_data_contract(
    tier: str,
    market_data: Optional[dict],
    *,
    mode: str = "paper",
    strict: Optional[bool] = None,
) -> DataContractResult:
    """校验 tier 数据契约。Live 或 STRICT_DATA_GATE 缺字段 → block。"""
    tier = (tier or "mid").lower()
    if tier in ("short", "scalp"):
        tier_key = "short"
    elif tier in ("long", "trend"):
        tier_key = "long"
    else:
        tier_key = "mid"

    if not isinstance(market_data, dict):
        market_data = {}

    if strict is None:
        try:
            from backend.config.settings import STRICT_DATA_GATE
            strict = bool(STRICT_DATA_GATE)
        except Exception:
            strict = True

    missing: List[str] = []
    for key in TIER_REQUIRED_FIELDS.get(tier_key, []):
        if not _has_field(market_data, key):
            alt = "current_price" if key == "price" else None
            if alt and _has_field(market_data, alt):
                continue
            missing.append(key)

    warn_only = False
    if (mode or "").lower() == "paper" and missing and not strict:
        warn_only = True

    ok = not missing or warn_only
    if missing and not ok:
        logger.info("[StrictData] BLOCK tier=%s missing=%s", tier_key, missing)
    elif missing and warn_only:
        logger.debug("[StrictData] WARN tier=%s missing=%s", tier_key, missing)

    return DataContractResult(ok=ok, missing=missing, tier=tier_key, warn_only=warn_only)


def apply_data_contract_gate(
    tier: str,
    market_data: Optional[dict],
    mode: str = "paper",
) -> Tuple[bool, str]:
    """evaluate 入口用：返回 (allowed, reason)。"""
    res = check_data_contract(tier, market_data, mode=mode)
    if res.ok:
        return True, res.reason
    return False, res.reason
