"""审计 HMAC 链 — DecisionSnapshot content_hash + 可选 Live HMAC。"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CHAIN_STATE_FILE = os.path.join("data", "audit_chain_state.json")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def sha256_content(payload: dict) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _load_chain_state() -> dict:
    if not os.path.isfile(_CHAIN_STATE_FILE):
        return {"last_hash": "", "count": 0}
    try:
        with open(_CHAIN_STATE_FILE, encoding="utf-8") as f:
            return json.load(f) or {"last_hash": "", "count": 0}
    except Exception:
        return {"last_hash": "", "count": 0}


def _save_chain_state(state: dict) -> None:
    os.makedirs(os.path.dirname(_CHAIN_STATE_FILE) or "data", exist_ok=True)
    with open(_CHAIN_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_audit_hmac_key(account_id: int, mode: str = "live") -> Optional[str]:
    """Live 账户 HMAC key；Paper 返回 None（仅 content_hash）。"""
    if (mode or "").lower() != "live":
        return None
    env_key = os.getenv("AUDIT_HMAC_KEY", "").strip()
    if env_key:
        return env_key
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import Account
        db = SessionLocal()
        try:
            acct = db.query(Account).filter(Account.id == int(account_id)).first()
            if acct and getattr(acct, "audit_hmac_key", None):
                return str(acct.audit_hmac_key)
        finally:
            db.close()
    except Exception:
        pass
    return None


def compute_chain_hashes(
    payload: dict,
    *,
    prev_hash: str = "",
    account_id: int = 0,
    mode: str = "paper",
) -> Dict[str, str]:
    """返回 content_hash, prev_hash, hmac_hash(optional)。"""
    content_hash = sha256_content(payload)
    chain_input = content_hash + (prev_hash or "")
    hmac_key = get_audit_hmac_key(account_id, mode)
    result = {"content_hash": content_hash, "prev_hash": prev_hash or None}
    if hmac_key:
        result["hmac_hash"] = hmac.new(
            hmac_key.encode("utf-8"),
            chain_input.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    return result


def append_to_chain(payload: dict, *, account_id: int = 0, mode: str = "paper") -> Dict[str, str]:
    """进程级链状态追加（Live 启用 HMAC）。"""
    state = _load_chain_state()
    prev = state.get("last_hash") or ""
    hashes = compute_chain_hashes(payload, prev_hash=prev, account_id=account_id, mode=mode)
    state["last_hash"] = hashes["content_hash"]
    state["count"] = int(state.get("count") or 0) + 1
    _save_chain_state(state)
    return hashes


def verify_chain(records: List[dict]) -> Dict[str, Any]:
    """验证有序 snapshot 列表的 hash 链。"""
    prev = ""
    ok = True
    errors: List[str] = []
    for i, rec in enumerate(records):
        ch = rec.get("content_hash") or ""
        ph = rec.get("prev_hash") or ""
        if ph != prev and i > 0:
            ok = False
            errors.append(f"row {i}: prev_hash mismatch")
        payload = {
            "symbol": rec.get("symbol"),
            "tier": rec.get("tier") or "mid",
            "action": rec.get("action") or "hold",
            "proposal": rec.get("proposal_json") or {},
            "verdict": rec.get("evaluate_verdict_json") or {},
        }
        expected = sha256_content(payload)
        if ch and ch != expected:
            ok = False
            errors.append(f"row {i}: content_hash mismatch")
        prev = ch
    return {"valid": ok, "count": len(records), "errors": errors}
