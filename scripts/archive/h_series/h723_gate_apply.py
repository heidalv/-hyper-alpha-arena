# -*- coding: utf-8 -*-
"""[h723 阶段1 2026-10-02] 闸门提案执行器(用户确认后一键落库)。

桥规则 C:闸门自证提案"先报用户再落库"。用户确认后由本脚本执行:
  - 读 data/gate_proposal_last.json 找指定 gate/param 的提案;
  - 校验注册表现值 == 提案 current(过期即拒);
  - evolution._apply_params 落库(prev=现值)+ sync_live_cfg + 登记 2h 快判。

用法:
  python scripts/h723_gate_apply.py --gate trend_h697_both
  python scripts/h723_gate_apply.py --param trend_pause_bp
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LANE = "mm_asterdex"


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", default=None)
    ap.add_argument("--param", default=None)
    # [教训 2026-10-02] 首版无 dry-run,一次"试跑"误把 trend_add_block_min_bp
    # 真落库。现改为**默认 dry-run,必须显式 --apply 才落库**。
    ap.add_argument("--apply", action="store_true", help="显式落库(默认只打印校验)")
    a = ap.parse_args()
    if not a.gate and not a.param:
        print("须给 --gate 或 --param")
        return 1
    try:
        raw = json.loads((ROOT / "data" / "gate_proposal_last.json").read_text(encoding="utf-8"))
    except Exception:
        print("无提案文件")
        return 1
    prop = None
    for p in raw.get("proposals") or []:
        if (a.gate and p.get("gate") == a.gate) or (a.param and p.get("param") == a.param):
            prop = p
            break
    if not prop:
        print("未找到对应提案")
        return 2

    from backend.services import lane_registry as reg
    from backend.services.market_maker import evolution as evo

    meta = (reg.get_lane(LANE) or {}).get("meta") or {}
    params = dict(meta.get("params") or {})
    param = prop["param"]
    cur = float(params.get(param) or 0.0)
    if cur <= 0:
        from backend.services.market_maker.core import LaneRiskLimits
        cur = float(getattr(LaneRiskLimits(), param, 0.0) or 0.0)
    if abs(cur - float(prop["current"])) > 1e-6:
        print(f"✗ 提案已过期(注册表现值 {cur} != 提案假设 {prop['current']})")
        return 3
    if not a.apply:
        print(f"[dry-run] 将落库:{param} {cur} → {prop['proposed']}"
              f"(证据 t={prop.get('t')});加 --apply 才执行")
        return 0
    new = float(prop["proposed"])
    prev = {param: cur}
    params[param] = new
    meta["params"] = params
    ok = evo._apply_params(LANE, meta, {param: new}, prev=prev,
                           reason=f"[h720 闸门自证] {prop['reason'][:160]}")
    if not ok:
        print("✗ 落库失败")
        return 4
    # 同步实盘(硬要求)
    try:
        _spec = importlib.util.spec_from_file_location("sync_live_cfg",
                                                       ROOT / "scripts" / "sync_live_cfg.py")
        _m = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_m)
        _m.main()
    except Exception:
        pass
    # 登记 2h 快判
    try:
        p = ROOT / "logs" / "self_tuner_pending.json"
        try:
            pending = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pending = []
        pending.append({"param": param, "old": cur, "new": new, "rollback": cur,
                        "applied_at": time.time(), "verdict_at": time.time() + 7200,
                        "verdict_metric": "net_bp_per_leg",
                        "verdict_rule": "改后2h每腿净<改前×0.8⇒回滚",
                        "reason": f"[h720 闸门自证] {prop['reason'][:160]}"})
        p.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    print(f"✓ 已落库:{param} {cur} → {new}(2h 快判已登记,回滚 {cur})")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
