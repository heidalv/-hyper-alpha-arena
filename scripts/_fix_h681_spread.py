# -*- coding: utf-8 -*-
"""[h681 修复] mm_apply_params --set 的逗号切分把 dict 拆碎(第 2 次踩):
现场写入垃圾键 '{BNB:1.8' / 'UNI:1.8' / 'ENA:1.8}'。本脚本:
①删除垃圾键;②用 evo._apply_params 正确写入 per_symbol_spread_mult=1.8
(= 0.9×半价差,贴最优价内侧 ⇒ 可成交)。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LANE = "mm_asterdex"
PSM = {"BNB": 1.8, "UNI": 1.8, "ENA": 1.8}


def main() -> int:
    from backend.services import lane_registry as reg
    from backend.services.market_maker import evolution as evo

    lane = reg.get_lane(LANE) or {}
    meta = dict(lane.get("meta") or {})
    params = dict(meta.get("params") or {})
    junk = [k for k in params
            if k.startswith("{") or k.endswith("}") or ":" in k]
    for k in junk:
        params.pop(k, None)
    prev = {"per_symbol_spread_mult": params.get("per_symbol_spread_mult"),
            "junk_removed": {k: None for k in junk}}
    params["per_symbol_spread_mult"] = PSM
    meta["params"] = params
    ok = evo._apply_params(LANE, meta, {"per_symbol_spread_mult": PSM},
                           prev=prev,
                           reason="h681 挂宽贴价差边缘(0.9×半价差)+清理 --set 拆碎垃圾键")
    after = dict(((reg.get_lane(LANE) or {}).get("meta") or {}).get("params") or {})
    print(f"写入: {ok}")
    print(f"清理垃圾键: {junk}")
    print(f"现值 per_symbol_spread_mult = {after.get('per_symbol_spread_mult')}")
    leftover = [k for k in after if k.startswith("{") or k.endswith("}")]
    print(f"残留垃圾键: {leftover or '无'}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
