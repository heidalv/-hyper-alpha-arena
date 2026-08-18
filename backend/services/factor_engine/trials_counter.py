"""多重检验试验计数器（P0-A · 升级计划 v3.0 S0/M0）。

背景：DSR/PBO 多重检验校正的强度必须按「历史上真实打过分/升过级的试验数」计算。
原实现 n_trials = max(40, active_n+1)，active=0 时恒为 40——真实试验数 300+，
校正少算约 8 倍，假阳性系统性放行。

设计：
- 单调递增、线程安全、JSON 持久化（data/factor_trials_counter.json），重启不丢；
- 统一计数：短线+中线同池（同一打分管线、同一假设族，不分周期拆分）；
- 首次初始化迁移：total = custom_factor_store 记录总数 + 130（ai_gen 归档历史）；
- 每次打分（无论晋升/拒绝）调用 bump()；DSR 闸门读 total。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

_PATH = os.path.join("data", "factor_trials_counter.json")
# ai_gen 归档历史（D7 遗留，130+ 全灭）——迁移时一次性并入
_MIGRATION_AI_GEN_ARCHIVE = 130

_lock = threading.RLock()  # 重入锁：total()/bump() 与 _load() 嵌套加锁
_state: dict | None = None  # lazy


def _load() -> dict:
    global _state
    if _state is not None:
        return _state
    with _lock:
        if _state is not None:
            return _state
        st: dict = {"total_scored": 0, "last_bump_at": 0.0}
        if os.path.exists(_PATH):
            try:
                with open(_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                st["total_scored"] = int(loaded.get("total_scored") or 0)
                st["last_bump_at"] = float(loaded.get("last_bump_at") or 0.0)
            except Exception as e:  # noqa: BLE001
                logger.warning("[TrialsCounter] 计数文件读取失败，重建: %s", e)
        else:
            # ── 一次性迁移：store 记录总数 + ai_gen 归档历史 ──
            store_n = 0
            try:
                from backend.services.factor_engine.custom_factor_store import custom_factor_store
                from backend.services.factor_engine.factor_backtest_scorer import _resolve_admin_tenant
                _t = _resolve_admin_tenant()
                try:
                    store_n += len(custom_factor_store.list_candidates(tenant_id=_t) or [])
                except Exception:
                    pass
                try:
                    store_n += len(custom_factor_store.list_active(tenant_id=_t) or [])
                except Exception:
                    pass
            except Exception as e:  # noqa: BLE001
                logger.debug("[TrialsCounter] store 计数迁移跳过: %s", e)
            st["total_scored"] = store_n + _MIGRATION_AI_GEN_ARCHIVE
            st["last_bump_at"] = time.time()
            logger.info(
                "[TrialsCounter] 首次初始化迁移: store=%d + ai_gen归档=%d → total=%d",
                store_n, _MIGRATION_AI_GEN_ARCHIVE, st["total_scored"],
            )
            _persist(st)
        _state = st
        return st


def _persist(st: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_PATH) or ".", exist_ok=True)
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        logger.warning("[TrialsCounter] 落盘失败: %s", e)


def bump() -> int:
    """登记一次试验（打分/晋升/拒绝都算），返回累计总数。"""
    with _lock:
        st = _load()
        st["total_scored"] = int(st["total_scored"]) + 1
        st["last_bump_at"] = time.time()
        _persist(st)
        return int(st["total_scored"])


def total() -> int:
    with _lock:
        return int(_load()["total_scored"])


def reset() -> None:
    """测试用：清零并删除持久化文件。"""
    global _state
    with _lock:
        _state = None
        try:
            if os.path.exists(_PATH):
                os.remove(_PATH)
        except Exception:  # noqa: BLE001
            pass
