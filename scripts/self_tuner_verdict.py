# -*- coding: utf-8 -*-
"""[h706] 自进化快判器:比对"改前/改后"指标,不达标自动回滚。

输入:`logs/self_tuner_pending.json`(self_tuner.apply_proposal 登记)
规则:
  - 指标 net_bp_per_leg / fills_per_hour:改后 < 改前 × 0.8 ⇒ 回滚;
  - 指标 stop_loss_total(负值=亏损):改后 ≤ 改前(亏得更多或持平)⇒ 回滚;
  - 样本不足(n<30 腿)⇒ 延期,不判;
  - 判定与回滚都写 `logs/self_tuner_history.jsonl`(append-only 审计)。
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LANE = "mm_asterdex"
PENDING = ROOT / "logs" / "self_tuner_pending.json"
HISTORY = ROOT / "logs" / "self_tuner_history.jsonl"
MIN_LEGS = 30
WORSE_FACTOR = 0.8


def _db(sql: str, params: tuple = ()):
    import importlib.util as _ilu
    import psycopg
    _spec = _ilu.spec_from_file_location("h425", ROOT / "scripts" / "h425_repair_trial.py")
    _h = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_h)
    with psycopg.connect(_h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def metric_value(metric: str, t0_ts: float, t1_ts: float) -> tuple:
    from datetime import datetime, timezone
    t0 = datetime.fromtimestamp(t0_ts, tz=timezone.utc).isoformat()
    t1 = datetime.fromtimestamp(t1_ts, tz=timezone.utc).isoformat()
    if metric == "stop_loss_total":
        r = _db("SELECT count(*), SUM(net_bp*notional)/10000.0 FROM lane_ledger"
                " WHERE lane_id=%s AND event='fill' AND ts >= %s AND ts < %s"
                " AND COALESCE(meta_json->>'exit_path','') LIKE '%%stop%%'", (LANE, t0, t1))
        n, v = (r or (0, None))
        return int(n or 0), float(v or 0.0)
    if metric == "fills_per_hour":
        r = _db("SELECT count(*) FROM lane_ledger WHERE lane_id=%s AND event='fill'"
                " AND ts >= %s AND ts < %s", (LANE, t0, t1))
        n = int((r or (0,))[0] or 0)
        dur_h = max(0.1, (t1_ts - t0_ts) / 3600.0)
        return n, n / dur_h
    if metric == "exit_cost":
        # [h782 2026-10-03] 出场类参数的**机制判据**(读 data/exit_cost_last.json):
        # 每腿净测的是市场波动(一条跳空腿就能翻负),与出场成本无关 ——
        # 22:43 / 23:58 两次因此误回滚 reduce_touch 的教训。
        # 返回值 (timeout_n, timeout_cross_bp);裁决规则见 main 里的专用分支。
        try:
            _ec = json.loads((ROOT / "data" / "exit_cost_last.json").read_text(encoding="utf-8"))
        except Exception:
            return 0, 0.0
        return int(_ec.get("timeout_n") or 0), float(_ec.get("timeout_cross_bp") or 0.0)
    r = _db("SELECT count(*), AVG(net_bp) FROM lane_ledger WHERE lane_id=%s"
            " AND event='fill' AND ts >= %s AND ts < %s", (LANE, t0, t1))
    n, v = (r or (0, None))
    return int(n or 0), float(v or 0.0)


def rollback(entry: dict) -> bool:
    if str(entry.get("era") or "") == "flow" or entry.get("param") in {
        "disaster_stop_floor_bp", "disaster_stop_cap_bp", "hold_sec",
        "entry_margin_bp", "min_notional_60s", "notional_loss_frac",
    }:
        from backend.services.market_maker.flow_rules import load_learn_params, save_learn_params
        current = load_learn_params(ROOT)
        current[entry["param"]] = float(entry["rollback"])
        save_learn_params(ROOT, current)
        return True
    from backend.services import lane_registry as reg
    from backend.services.market_maker import evolution as evo
    meta = (reg.get_lane(LANE) or {}).get("meta") or {}
    params = dict(meta.get("params") or {})
    param = entry["param"]
    prev = {param: params.get(param)}
    params[param] = float(entry["rollback"])
    meta["params"] = params
    ok = evo._apply_params(LANE, meta, {param: float(entry["rollback"])}, prev=prev,
                           reason=f"[self-tune h706 自动回滚] {entry.get('reason','')[:160]}")
    if ok:
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location("sync_live_cfg",
                                                 ROOT / "scripts" / "sync_live_cfg.py")
            _m = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_m)
            _m.main()
        except Exception:
            pass
    return bool(ok)


def main() -> int:
    try:
        pending = json.loads(PENDING.read_text(encoding="utf-8"))
    except Exception:
        pending = []
    if not pending:
        print("无待判定提案。")
        return 0
    now = time.time()
    keep = []
    for entry in pending:
        if now < float(entry.get("verdict_at") or 0.0):
            keep.append(entry)
            continue
        t_applied = float(entry.get("applied_at") or 0.0)
        dur = now - t_applied
        verdict = "keep"
        n_b = v_b = n_a = v_a = 0
        if str(entry.get("era") or "") == "flow" or entry.get("verdict_metric") == "roundtrip_y":
            from backend.services.market_maker.flow_rules import (
                should_rollback_flow, window_stats,
            )
            try:
                lines = (ROOT / "data" / "flow_roundtrip_log.jsonl").read_text(encoding="utf-8").splitlines()
                rows = [json.loads(x) for x in lines if x.strip()]
            except Exception:
                rows = []
            before = window_stats(rows, t_applied - dur, t_applied)
            after = window_stats(rows, t_applied, now)
            n_b, v_b = int(before["n"]), float(before["mean_y"])
            n_a, v_a = int(after["n"]), float(after["mean_y"])
            if n_a < MIN_LEGS:
                keep.append(entry)
                continue
            verdict = "rollback" if should_rollback_flow(before, after) else "keep"
        else:
            n_b, v_b = metric_value(entry["verdict_metric"], t_applied - dur, t_applied)
            n_a, v_a = metric_value(entry["verdict_metric"], t_applied, now)
        rec = {"ts": now, "param": entry["param"], "old": entry["old"],
               "new": entry["new"], "rollback": entry["rollback"],
               "metric": entry["verdict_metric"],
               "before": {"n": n_b, "v": round(float(v_b), 4)},
               "after": {"n": n_a, "v": round(float(v_a), 4)}}
        if str(entry.get("era") or "") == "flow" or entry.get("verdict_metric") == "roundtrip_y":
            pass
        elif entry.get("verdict_metric") == "exit_cost":
            # [h782 2026-10-03] 出场类参数专用判据(**禁止每腿净**):
            try:
                _ec = json.loads((ROOT / "data" / "exit_cost_last.json").read_text(encoding="utf-8"))
            except Exception:
                _ec = {}
            if entry["param"] == "reduce_touch_after_sec":
                _b0 = float(entry.get("baseline_cross_bp") or 0.0)
                _ca = float(_ec.get("timeout_cross_bp") or 0.0)
                # [h783 2026-10-04] 只在**恶化**时才回滚:00:43 用"改善≥1bp"的
                # 硬门槛误杀(实测改善 +0.68bp、腿数 −23.5%,都是好方向但没达标)。
                verdict = "rollback" if (_ca < _b0 - 1.0) else "keep"
            elif entry["param"] == "jump_exit_bp":
                _sg = float(_ec.get("stop_gap_bp") or 0.0)
                # [h783] 提高阈值(25→70)的测试:只有"深跳空重现(止损又被打到
                # <−100bp)"才说明高阈值没接住真跳空 ⇒ 回滚到低阈值;
                # 假摔率(jump_n)不是回滚理由 —— 那是低阈值的病。
                verdict = "rollback" if (_sg < -100.0) else "keep"
            elif entry["param"] == "max_net_directional_ratio":
                # 仓位上限类:焊死的代码硬上限(_HARD_POS_MAX_MULT)兜底,自动循环不回滚
                verdict = "keep"
        elif n_a >= MIN_LEGS:
            if entry["verdict_metric"] == "stop_loss_total":
                if v_a <= v_b - 1e-9:
                    verdict = "rollback"
            elif v_a < v_b * WORSE_FACTOR:
                verdict = "rollback"
        rec["verdict"] = verdict
        if verdict == "rollback":
            rec["rolled_back"] = rollback(entry)
        else:
            rec["rolled_back"] = False
        with open(HISTORY, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        # [h707] **经验账本**:每次判决(keep/rollback)连同改前改后写入
        # data/self_tuner_experience.jsonl ⇒ 供 DSH 桥复核时作 few-shot 记忆,
        # 自进化"越用越聪明"的积累来源。
        try:
            _exp = {"ts": now, "param": entry["param"], "old": entry["old"],
                    "new": entry["new"], "rollback": entry["rollback"],
                    "reason": entry.get("reason", ""),
                    "metric": entry["verdict_metric"],
                    "before": {"n": n_b, "v": round(v_b, 4)},
                    "after": {"n": n_a, "v": round(v_a, 4)},
                    "verdict": verdict,
                    "era": entry.get("era") or "mm"}
            _exp_path = ROOT / "data" / "self_tuner_experience.jsonl"
            with open(_exp_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(_exp, ensure_ascii=False) + "\n")
        except Exception:
            pass
        print(json.dumps(rec, ensure_ascii=False))
    PENDING.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    raise SystemExit(main())
