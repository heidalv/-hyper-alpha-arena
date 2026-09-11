# -*- coding: utf-8 -*-
"""日开仓配额 —— 单一来源（v3 方向 7：“日开仓配额单一来源 runtime_tuning，删除 V5/loop 分裂的旧配额”）。

此前四处各持一份数字：
  - scalp_loop 读 settings.SCALP_DAILY_OPEN_CAP（env）
  - live_gate_policy 读 LIVE_DAILY_OPEN_CAP（env）+ 会话文件计数
  - risk_control_service 安全网读 runtime_tuning.max_daily_trades × 1.5
  - TREND_DAILY_OPEN_CAP 只出现在提示词里，从未拦截（中长线日配额事实上不存在）

现在：
  cap_for(bucket)   唯一读取口 → runtime_tuning（data/runtime_tuning.json，前端/进化热改同一份）
      scalp  → scalp_daily_cap      短线（trade_nature = 'scalp'）
      trend  → trend_daily_cap      中长线（trade_nature ≠ 'scalp'）
      total  → max_daily_trades     账户全部
      live   → live_daily_cap       实盘全部 tier 合计（旧 LIVE_DAILY_OPEN_CAP）
  首次运行若文件里没有显式值，则用旧 env 值**一次性播种**写入文件（迁移），此后 env 不再被读取。
  opens_today(db, account_id, bucket)  统一计数口径：paper_positions.opened_at ≥ 今日 UTC 0 点。
  check(db, account_id, tier, live)    RiskEngine.pre_trade 调用；scalp_loop / live_gate_policy 的
                                       旧读取点改为委托 cap_for，不再各自持有数字。
0 = 不限制。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# bucket → (runtime_tuning 键, 旧 env 键, 兜底默认)
_BUCKETS: Dict[str, tuple] = {
    "scalp": ("scalp_daily_cap", "SCALP_DAILY_OPEN_CAP", 20),
    "trend": ("trend_daily_cap", "TREND_DAILY_OPEN_CAP", 6),
    "total": ("max_daily_trades", "V5_MAX_DAILY_TRADES_PAPER", 10),
    "live": ("live_daily_cap", "LIVE_DAILY_OPEN_CAP", 6),
}

_seed_lock = threading.Lock()
_seeded: Dict[str, bool] = {}


def bucket_for(tier: Optional[str], trade_nature: Optional[str]) -> str:
    """把 (timeframe_tier, trade_nature) 归到配额桶：scalp / trend。"""
    n = str(trade_nature or "").strip().lower()
    t = str(tier or "").strip().lower()
    if n == "scalp" or (not n and t == "short"):
        return "scalp"
    return "trend"


def _file_value_is_schema_default(key: str) -> bool:
    """文件里该键是否仍等于 schema 默认值（apply_patches 会把整份 schema 写回文件，
    所以“键存在”不能作为显式设置的判据，须比较值——与 runtime_gates_compat 同口径）。"""
    try:
        import json
        from backend.services.runtime_tuning_store import TUNING_FILE, _DEFAULT_SCHEMA
        schema = _DEFAULT_SCHEMA.get(key)
        default_val = schema.get("value") if isinstance(schema, dict) else schema
        if not os.path.isfile(TUNING_FILE):
            return True
        with open(TUNING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        if key not in data:
            return True
        entry = data.get(key)
        file_val = entry.get("value") if isinstance(entry, dict) and "value" in entry else entry
        return abs(float(file_val) - float(default_val)) < 1e-9
    except Exception:
        return False


def _seed_from_env_once(bucket: str) -> None:
    """一次性迁移：旧 env 显式设置了值、且文件里仍是 schema 默认值 → 把 env 值写进文件。
    此后 env 不再被读取；文件（前端/进化热改同一份）是唯一权威。"""
    if _seeded.get(bucket):
        return
    with _seed_lock:
        if _seeded.get(bucket):
            return
        key, env_key, _fallback = _BUCKETS[bucket]
        try:
            raw = os.getenv(env_key)
            if raw not in (None, "") and _file_value_is_schema_default(key):
                val = int(raw)
                from backend.services.runtime_tuning_store import apply_patches
                applied = apply_patches({key: val})
                logger.warning(
                    "[DailyQuota] runtime_tuning.%s 仍为 schema 默认，已从 %s=%s 一次性播种 → %s",
                    key, env_key, raw, applied.get(key, val),
                )
        except Exception as exc:
            logger.debug("[DailyQuota] 播种 %s 失败（继续用文件值）: %s", bucket, exc)
        finally:
            _seeded[bucket] = True


def cap_for(bucket: str) -> int:
    """唯一读取口。0=不限制。"""
    b = str(bucket or "").strip().lower()
    if b not in _BUCKETS:
        raise ValueError(f"unknown quota bucket: {bucket}")
    _seed_from_env_once(b)
    key, _env_key, fallback = _BUCKETS[b]
    try:
        from backend.services.runtime_tuning_store import get_tuning_int
        return max(0, int(get_tuning_int(key, int(fallback))))
    except Exception as exc:
        logger.debug("[DailyQuota] 读取 %s 失败，用兜底 %s: %s", key, fallback, exc)
        return int(fallback)


def all_caps() -> Dict[str, int]:
    return {b: cap_for(b) for b in _BUCKETS}


def utc_day_start() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def opens_today(db, account_id: int, bucket: str = "total") -> int:
    """统一计数：今日（UTC）该账户已开仓数。scalp=trade_nature='scalp'；trend=其余；total=全部。"""
    from sqlalchemy import text
    b = str(bucket or "total").lower()
    cond = ""
    if b == "scalp":
        cond = " AND trade_nature = 'scalp'"
    elif b == "trend":
        cond = " AND (trade_nature IS NULL OR trade_nature <> 'scalp')"
    day_start = utc_day_start().replace(tzinfo=None)
    row = db.execute(
        text(
            "SELECT count(*) FROM paper_positions "
            "WHERE account_id = :aid AND opened_at >= :ds" + cond
        ),
        {"aid": int(account_id), "ds": day_start},
    ).fetchone()
    return int(row[0] or 0) if row else 0


@dataclass
class QuotaVerdict:
    allowed: bool
    bucket: str
    used: int
    cap: int
    reason: str = ""
    total_used: int = 0
    total_cap: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed, "bucket": self.bucket, "used": self.used, "cap": self.cap,
            "total_used": self.total_used, "total_cap": self.total_cap, "reason": self.reason,
        }


# 60s 计数缓存（同一账户桶）：热路径避免每次开仓都 count(*)
_count_cache: Dict[tuple, tuple] = {}
_count_lock = threading.Lock()


def _cached_opens(db, account_id: int, bucket: str, max_age: float = 20.0) -> int:
    k = (int(account_id), bucket)
    now = time.time()
    with _count_lock:
        hit = _count_cache.get(k)
        if hit and now - hit[0] < max_age:
            return int(hit[1])
    n = opens_today(db, account_id, bucket)
    with _count_lock:
        _count_cache[k] = (now, n)
    return n


def invalidate_count_cache(account_id: Optional[int] = None) -> None:
    with _count_lock:
        if account_id is None:
            _count_cache.clear()
        else:
            for k in [k for k in _count_cache if k[0] == int(account_id)]:
                _count_cache.pop(k, None)


def check(db, account_id: int, *, tier: Optional[str], trade_nature: Optional[str],
          live: bool = False) -> QuotaVerdict:
    """开仓前配额校验：先查桶配额（scalp/trend），再查账户总配额；实盘再查 live 合计配额。"""
    bucket = bucket_for(tier, trade_nature)
    cap = cap_for(bucket)
    used = _cached_opens(db, account_id, bucket) if cap > 0 else 0
    # 逼近上限（剩余 ≤ 2）时放弃缓存精确重数，避免 20s 缓存窗口内连开数笔越界
    if cap > 0 and cap - used <= 2:
        used = opens_today(db, account_id, bucket)
    total_cap = cap_for("total")
    total_used = _cached_opens(db, account_id, "total") if total_cap > 0 else 0
    if total_cap > 0 and total_cap - total_used <= 2:
        total_used = opens_today(db, account_id, "total")
    if cap > 0 and used >= cap:
        return QuotaVerdict(False, bucket, used, cap, f"daily_quota[{bucket}] {used}/{cap} 已用尽",
                            total_used, total_cap)
    if total_cap > 0 and total_used >= total_cap:
        return QuotaVerdict(False, "total", total_used, total_cap,
                            f"daily_quota[total] {total_used}/{total_cap} 已用尽", total_used, total_cap)
    if live:
        live_cap = cap_for("live")
        if live_cap > 0 and total_used >= live_cap:
            return QuotaVerdict(False, "live", total_used, live_cap,
                                f"daily_quota[live] {total_used}/{live_cap} 已用尽", total_used, total_cap)
    return QuotaVerdict(True, bucket, used, cap, "", total_used, total_cap)


def status(db, account_id: int) -> Dict[str, Any]:
    caps = all_caps()
    return {
        "account_id": int(account_id),
        "caps": caps,
        "used": {
            "scalp": opens_today(db, account_id, "scalp"),
            "trend": opens_today(db, account_id, "trend"),
            "total": opens_today(db, account_id, "total"),
        },
        "day_start_utc": utc_day_start().isoformat(),
        "source": "runtime_tuning(data/runtime_tuning.json)",
    }
