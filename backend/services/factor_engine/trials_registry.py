"""跨轮假设登记簿 —— DSR 多重检验分母的"滚动不同假设数"（2026-09-03 审查修正 C）。

问题
====
进化主链 ``_promote_factors`` 的 DSR 分母 ``n_trials = len(all_icir_values)`` 只数
**本轮**在验证窗上评估过的候选。而验证窗每天只滑 1 天，连续几轮（每轮几十个新
候选）实际上是在**同一段数据**上反复挑最好的——多重检验的真实次数是这些轮次里
评估过的**不同假设**之和，本轮口径把它少算了几倍到几十倍。

两种错误的修法都已经在历史上出过事：
- 累计计数器（``trials_counter``，单调递增）：把同一假设的重复评估也算进 N，
  闸门随时间单调收紧（棘轮），最终任何因子都过不了（08-19 已从打分器摘除）。
- 只数本轮：漏算跨轮。

本模块的口径
============
按周期档维护 ``{factor_id(表达式哈希): 最近评估时间}``；
``n_rolling = 近 window_days 天内评估过的不同 factor_id 数``。
- 同一假设重复评估只更新时间戳，不加计数 → 不棘轮；
- 超过窗口的条目自然过期 → 验证窗滑过去以后不再算 → 不漏算也不永久累积；
- window_days 默认取该周期的验证窗天数（与被"反复挑选"的那段数据同长）。

调用方取 ``max(本轮数, n_rolling)`` 作 DSR 分母。持久化 JSON，进程重启不丢。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, Optional

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_state: Optional[Dict[str, Dict[str, float]]] = None  # period -> {factor_id: last_seen_ts}

# 每档最多保留的条目数 / 最长保留天数（防文件无界增长；两者都远大于任何验证窗）
_MAX_IDS_PER_PERIOD = 20000
_MAX_KEEP_DAYS = 180.0


def _path() -> str:
    env = os.getenv("FACTOR_TRIALS_REGISTRY_PATH")
    if env:
        return env
    root = Path(__file__).resolve().parents[3]  # <repo>/backend/services/factor_engine → <repo>
    return str(root / "data" / "factor_trials_registry.json")


def _load() -> Dict[str, Dict[str, float]]:
    global _state
    if _state is not None:
        return _state
    with _lock:
        if _state is not None:
            return _state
        st: Dict[str, Dict[str, float]] = {}
        p = _path()
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    raw = json.load(f) or {}
                for period, ids in (raw.get("periods") or {}).items():
                    if isinstance(ids, dict):
                        st[str(period)] = {str(k): float(v) for k, v in ids.items()}
            except Exception as e:  # noqa: BLE001
                logger.warning("[TrialsRegistry] 登记簿读取失败，重建: %s", e)
                st = {}
        _state = st
        return st


def _persist(st: Dict[str, Dict[str, float]]) -> None:
    p = _path()
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"updated_at": time.time(), "periods": st}, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception as e:  # noqa: BLE001
        logger.warning("[TrialsRegistry] 落盘失败: %s", e)


def _prune(ids: Dict[str, float], now: float) -> None:
    cutoff = now - _MAX_KEEP_DAYS * 86400.0
    for k in [k for k, ts in ids.items() if ts < cutoff]:
        ids.pop(k, None)
    if len(ids) > _MAX_IDS_PER_PERIOD:
        # 只留最近的 _MAX_IDS_PER_PERIOD 条
        keep = sorted(ids.items(), key=lambda kv: kv[1], reverse=True)[:_MAX_IDS_PER_PERIOD]
        ids.clear()
        ids.update(dict(keep))


def register_evaluated(period: str, factor_ids: Iterable[str], now: Optional[float] = None) -> int:
    """登记本轮在验证窗上评估过的假设；返回该档当前登记总条目数。"""
    now = float(now if now is not None else time.time())
    key = str(period or "default")
    with _lock:
        st = _load()
        ids = st.setdefault(key, {})
        for fid in factor_ids or ():
            fid = str(fid or "").strip()
            if fid:
                ids[fid] = now  # 重复评估只刷新时间戳，不加计数
        _prune(ids, now)
        _persist(st)
        return len(ids)


def distinct_recent(period: str, window_days: float, now: Optional[float] = None) -> int:
    """近 window_days 天内评估过的不同假设数。"""
    now = float(now if now is not None else time.time())
    cutoff = now - float(window_days) * 86400.0
    with _lock:
        ids = _load().get(str(period or "default"), {})
        return sum(1 for ts in ids.values() if ts >= cutoff)


def register_and_count(period: str, factor_ids: Iterable[str], window_days: float,
                       now: Optional[float] = None) -> int:
    """登记 + 返回滚动不同假设数（调用方与本轮数取 max 作 DSR 分母）。"""
    register_evaluated(period, factor_ids, now=now)
    return distinct_recent(period, window_days, now=now)


def reset() -> None:
    """测试用：清空内存并删除持久化文件。"""
    global _state
    with _lock:
        _state = None
        try:
            p = _path()
            if os.path.exists(p):
                os.remove(p)
        except Exception:  # noqa: BLE001
            pass
