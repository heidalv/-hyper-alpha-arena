"""llm_budget_governor — LLM 2.0 每日 token/调用预算治理（U1-3，2026-08-25）。

统一计划 §3.2 原则 4/5 的最小实现：
  - 每日 scope 级调用硬上限（超限当日该 scope 暂停 = 自动降级规则雏形）；
  - 状态落盘 data/llm2_budget_state.json（重启存活，按 UTC 日期自动清零）；
  - ROAS 完整问责（decision 绑定回填/滚动报表）在 U4 复盘官落地后接入本模块。

Scope 与默认配额（env 可覆盖 LLM2_CAP_<SCOPE>）：
  kline_analysis 400 / thesis 200 / scalp_confirm 100 / master 60 / other 150；
  全局 LLM2_DAILY_GLOBAL_CAP 默认 800。

接入点：KlineAnalyst._llm_deep_analysis / thesis_shadow.run_thesis_shadow /
scalp_llm_confirm.scalp_llm_confirm（全部 try/except fail-open，预算服务故障不阻断交易）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "llm2_budget_state.json",
)

DEFAULT_CAPS: Dict[str, int] = {
    "kline_analysis": 400,
    "thesis": 200,
    "scalp_confirm": 100,
    "master": 60,
    "review": 50,
    "committee": 20,
    "other": 150,
}

_lock = threading.Lock()
_state: Dict[str, Dict[str, int]] = {}   # {"YYYY-MM-DD": {"kline_analysis": 12, ...}}
_paused: Dict[str, str] = {}             # scope -> 暂停日期（当日不再放行）


def _enabled() -> bool:
    return os.getenv("LLM2_BUDGET_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")


def _cap(scope: str) -> int:
    env = os.getenv(f"LLM2_CAP_{scope.upper()}", "")
    if env:
        try:
            return max(0, int(env))
        except ValueError:
            pass
    return DEFAULT_CAPS.get(scope, 150)


def _global_cap() -> int:
    try:
        return int(os.getenv("LLM2_DAILY_GLOBAL_CAP", "800") or 800)
    except ValueError:
        return 800


def _load() -> None:
    global _state
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _state = {k: v for k, v in data.items() if isinstance(v, dict)}
            _paused.update(data.get("__paused__", {}) or {})
    except Exception:
        _state = {}


def _save() -> None:
    try:
        os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({**_state, "__paused__": dict(_paused)}, f, ensure_ascii=False)
    except Exception as e:
        logger.debug("[LLM2预算] 状态落盘失败: %s", e)


_load()


def llm2_allow(scope: str) -> bool:
    """预算闸：true=放行并计数；false=当日该 scope 已暂停（调用方应走规则回退）。"""
    if not _enabled():
        return True
    scope = (scope or "other").strip().lower()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _lock:
        if _paused.get(scope) == today:
            return False
        day = _state.setdefault(today, {})
        day[scope] = int(day.get(scope, 0)) + 1
        day["__global__"] = int(day.get("__global__", 0)) + 1
        if day[scope] > _cap(scope):
            # 持久化暂停标记（重启不重置），告警只打一次
            _paused[scope] = today
            logger.warning(
                "[LLM2预算] scope=%s 当日额度耗尽(%d/%d)，暂停至明日（自动降级）",
                scope, day[scope] - 1, _cap(scope),
            )
            _save()
            return False
        if day["__global__"] > _global_cap():
            _paused[scope] = today
            logger.warning(
                "[LLM2预算] 全局额度耗尽(%d/%d)，scope=%s 暂停至明日",
                day["__global__"] - 1, _global_cap(), scope,
            )
            _save()
            return False
        if day[scope] % 50 == 0:
            _save()
        return True


def llm2_report() -> Dict:
    """当日各 scope 用量与配额（供 /api 面板与周报）。"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _lock:
        day = _state.get(today, {})
    out = {"date": today, "enabled": _enabled(), "global": {"used": day.get("__global__", 0), "cap": _global_cap()}}
    for scope in sorted(set(list(DEFAULT_CAPS.keys()) + [k for k in day.keys() if k != "__global__"])):
        out[scope] = {"used": day.get(scope, 0), "cap": _cap(scope), "paused": _paused.get(scope) == today}
    return out


def roas_adjust_caps() -> dict:
    """[2026-08-26 学习闭环#5] ROAS→预算：按各组近7天净收益给出 scope 配额建议。

    数据源：brain_episodes(复盘官归因含 source 标签) + llm2 用量。
    当前为建议输出（不自动改 env，防误调）；周报任务将读取本函数，
    连续 2 周建议一致时由人工落 .env。"""
    import datetime as _dt
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    return {"date": today, "note": "ROAS 骨架就位；自动调整待 brain_episodes 分组数据≥2周后激活"}
