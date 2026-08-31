# -*- coding: utf-8 -*-
"""启动指纹：证明运行中的进程加载的是哪份代码。

背景（2026-08-22 用户反馈）：改动经常"不生效、不执行"——根因是后端进程
可能在改动提交【之前】就启动了（无热重载时不会自动加载新代码），
而健康端点又不暴露代码版本，无法区分"改了但没跑"与"跑了但没变"。

方案：启动时 Capture 一次快照，/api/health 返回：
  - boot_git_hash: 启动时的 git HEAD（73e1d72 等短哈希）
  - boot_at_unix / boot_at_iso: 进程启动时间
  - code_git_head: 当前磁盘的 git HEAD（人工对比；两者一致 = 跑的就是最新代码）
  - live_markers: 关键改动真实性校验（函数/配置是否存在），每项 True/False

校验项（随改动增加）：
  - ev_governor_audit    : EV Governor 每日资金分配存在
  - m0_11_min_hold       : midlong 复查平仓 min_hold 保护存在
  - m0_6_cross_layer     : 持仓管理跨层防护（M0-6）存在
  - m1_1_tenant_auto     : 自动租户填列（M1-1）存在
  - profit_peak_trail    : PROFIT-1 峰值追踪离场存在
  - factor_cost_9bp      : 因子评审成本 0.0009（FACTOR-1）
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any, Dict

_BOOT_AT = time.time()
_HEAD = "unknown"
_marker_validators: Dict[str, Any] = {}
# [2026-08-31 性能根治] marker 快照只求值一次（docstring 原意就是"启动时 Capture"）。
# 此前每次 boot_fingerprint() 都重跑全部校验器：其中两个用 inspect.getsource
# 读 + ast.parse 整个 paper_trading_engine.py（5400+ 行），再加每次 2 次
# git subprocess → /api/health 恒定 500-800ms，事件循环被堵，前端所有刷新排队。
_markers_snapshot: Dict[str, bool] | None = None
_markers_snapshot_ts: float | None = None
# 磁盘 git HEAD 的 TTL 缓存：subprocess 每次 50-100ms，健康探针不可承受。
# matches_disk 语义保留（最多滞后 TTL 时长）；0 = 每次实时（旧行为）。
_HEAD_DISK_TTL_SEC = float(os.getenv("BOOT_FINGERPRINT_DISK_TTL_SEC", "60") or 60)
_head_disk_cache: tuple | None = None


def _git_head() -> str:
    try:
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_root, capture_output=True, text=True, timeout=3,
        )
        if _out.returncode == 0:
            return _out.stdout.strip() or "unknown"
    except Exception:
        pass
    try:
        from backend.version import __version__
        return f"version-{__version__}"
    except Exception:
        pass
    return "unknown"


def _git_head_disk() -> str:
    """当前磁盘 git HEAD，带 TTL 缓存（默认 60s；BOOT_FINGERPRINT_DISK_TTL_SEC=0 关闭）。"""
    global _head_disk_cache
    _now = time.time()
    if _head_disk_cache and (_now - _head_disk_cache[0]) < _HEAD_DISK_TTL_SEC:
        return _head_disk_cache[1]
    _h = _git_head()
    _head_disk_cache = (_now, _h)
    return _h


def _register_marker(name: str, fn) -> None:
    _marker_validators[name] = fn


def _init_markers() -> None:
    """注册所有关键改动校验器（惰性导入，失败即 False，不阻断健康）。"""

    def _has_midlong_min_hold() -> bool:
        try:
            from backend.services.full_auto.midlong_position_manager import (
                _review_min_hold_check,
            )
            return callable(_review_min_hold_check)
        except Exception:
            return False

    def _has_ev_governor() -> bool:
        try:
            from backend.services.ev_governor import audit_and_write
            return callable(audit_and_write)
        except Exception:
            return False

    def _has_m0_6_cross_layer() -> bool:
        try:
            import inspect
            from backend.services.paper_trading_engine import paper_engine
            _src = inspect.getsource(paper_engine.close_position)
            return "position_id" in _src and "trade_nature" in _src
        except Exception:
            return False

    def _has_tenant_auto() -> bool:
        try:
            from backend.database.connection import _auto_fill_tenant_id
            return callable(_auto_fill_tenant_id)
        except Exception:
            return False

    def _has_peak_trail() -> bool:
        try:
            import inspect
            from backend.services.paper_trading_engine import paper_engine
            _cls = type(paper_engine)
            _src = inspect.getsource(_cls)
            return "PAPER_PEAK_TRAIL_PCT" in _src or "_peak_price_from_pos" in _src
        except Exception:
            return False

    def _has_factor_cost_9bp() -> bool:
        try:
            from backend.config import settings
            return abs(float(getattr(settings, "FACTOR_SCORER_COST", 1.0)) - 0.0009) < 1e-9
        except Exception:
            return False

    _register_marker("ev_governor_audit", _has_ev_governor)
    _register_marker("m0_11_min_hold", _has_midlong_min_hold)
    _register_marker("m0_6_cross_layer", _has_m0_6_cross_layer)
    _register_marker("m1_1_tenant_auto", _has_tenant_auto)
    _register_marker("profit_peak_trail", _has_peak_trail)
    _register_marker("factor_cost_9bp", _has_factor_cost_9bp)


def boot_fingerprint() -> Dict[str, Any]:
    """返回启动快照（模块级缓存；进程重启后自动取新值）。

    [2026-08-31 性能根治] marker 校验只在首次调用求值一次（语义=启动快照），
    磁盘 git HEAD 走 TTL 缓存；/api/health 热路径从此零文件解析、零 subprocess。
    """
    global _HEAD, _markers_snapshot, _markers_snapshot_ts
    if _HEAD == "unknown":
        _HEAD = _git_head()
    if not _marker_validators:
        _init_markers()
    if _markers_snapshot is None:
        _markers_snapshot = {k: bool(v()) for k, v in _marker_validators.items()}
        _markers_snapshot_ts = time.time()
    from datetime import datetime, timezone
    _disk = _git_head_disk()
    return {
        "boot_git_hash": _HEAD,
        "code_git_head": _disk,
        "boot_at_unix": round(_BOOT_AT, 3),
        "boot_at_iso": datetime.fromtimestamp(_BOOT_AT, tz=timezone.utc).isoformat(),
        "live_markers": dict(_markers_snapshot),
        "markers_evaluated_at_unix": round(_markers_snapshot_ts or _BOOT_AT, 3),
        "matches_disk": (_HEAD == _disk),
    }


if __name__ == "__main__":
    import json
    # 独立运行时补齐项目根/backend 到 sys.path（与后端进程一致）
    _here = os.path.dirname(os.path.abspath(__file__))
    _root = os.path.dirname(_here)
    for _p in (_root, _here):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    print(json.dumps(boot_fingerprint(), ensure_ascii=False, indent=2))
