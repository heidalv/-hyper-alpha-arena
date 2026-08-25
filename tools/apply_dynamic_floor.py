#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补丁: 仲裁器门槛改为动态读取 .env(改动秒级生效,免重启)(2026-08-25)。

背景: 多个重启周期里 .env 的 FUSION_SCALP_PWIN_MIN 与运行进程不一致,
调试成本高。改为每次决策时检查 .env mtime,变了就 override=True 重载。
"""
import shutil

PATH = "backend/services/decision_fusion_arbiter.py"
BAK = PATH + ".bak_20260825_dynfloor"
shutil.copy(PATH, BAK)
print("backup ->", BAK)

src = open(PATH, encoding="utf-8").read()

old1 = '''def effective_pwin_floor() -> float:
    return float(_pwin_floor_override or PWIN_MIN)'''
new1 = '''_ENV_MTIME: Dict[str, float] = {}


def _maybe_reload_env() -> None:
    """.env 文件 mtime 变化时重新载入(override=True),让门槛调整免重启生效。"""
    try:
        import os as _os
        _root = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
        _env_path = _os.path.join(_root, ".env")
        if not _os.path.isfile(_env_path):
            return
        _mt = _os.path.getmtime(_env_path)
        if _ENV_MTIME.get("env") == _mt:
            return
        from dotenv import load_dotenv
        load_dotenv(_env_path, override=True)
        _ENV_MTIME["env"] = _mt
    except Exception:
        pass


def effective_pwin_floor() -> float:
    if _pwin_floor_override:
        return float(_pwin_floor_override)
    _maybe_reload_env()
    return _f("FUSION_SCALP_PWIN_MIN", PWIN_MIN)'''
assert src.count(old1) == 1, f"e1={src.count(old1)}"
src = src.replace(old1, new1)

# RR 下限同样动态
old2 = '''    # 6. RR 下限：TP/SL < 1.2 的结构必亏（历史 RR=0.9/0.32 类），LLM 特批除外
    if tp_pct and sl_pct and sl_pct > 0:
        rr = float(tp_pct) / float(sl_pct)
        if rr < RR_FLOOR:'''
new2 = '''    # 6. RR 下限：TP/SL < 1.2 的结构必亏（历史 RR=0.9/0.32 类），LLM 特批除外
    if tp_pct and sl_pct and sl_pct > 0:
        rr = float(tp_pct) / float(sl_pct)
        _rr_floor = _f("FUSION_RR_FLOOR", RR_FLOOR)
        if rr < _rr_floor:'''
assert src.count(old2) == 1, f"e2={src.count(old2)}"
src = src.replace(old2, new2)

open(PATH, "w", encoding="utf-8").write(src)
print("patched OK")
