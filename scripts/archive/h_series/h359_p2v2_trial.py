# -*- coding: utf-8 -*-
"""H359 试跑 #4：P2 v2（VWAP 回归 + 薄流确认）部署/回滚/判定。

前置逻辑（预注册 h359）：
  - 若 #1 判决 ROLLBACK（vwap_revert_bp 已回 0）：--deploy 同时开
    vwap_revert_bp=2.0 + vwap_flow_block=0.3（两参数同属 P2 形态包）；
  - 否则只开 vwap_flow_block=0.3。
回滚：两参数都归 0（若 #4 曾开启 vwap_revert_bp）。

用法：--deploy / --rollback / --judge
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h359_verdict.json"
LANE = "mm_asterdex"
ALPHA = 0.10
FREQ_FLOOR = 0.8
VW_BP = 2.0
VFB = 0.3


def read_env_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _welch(a, b):
    n1, n2 = len(a), len(b)
    if n1 < 2 or n2 < 2:
        return None
    m1, m2 = sum(a) / n1, sum(b) / n2
    v1 = sum((x - m1) ** 2 for x in a) / (n1 - 1)
    v2 = sum((x - m2) ** 2 for x in b) / (n2 - 1)
    se = math.sqrt(v1 / n1 + v2 / n2)
    if se <= 0:
        return None
    t = (m1 - m2) / se
    df = (v1 / n1 + v2 / n2) ** 2 / ((v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))

    def _pdf(x):
        return math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) \
            / math.sqrt(math.pi * df) * (1 + x * x / df) ** (-(df + 1) / 2)

    step, s, x = 0.05, 0.0, abs(t)
    while x < abs(t) + 30.0:
        s += step * (_pdf(x) + _pdf(x + step)) / 2
        x += step
    return {"t": t, "p": min(1.0, 2 * s), "delta": m1 - m2,
            "mean_trial": m1, "mean_base": m2, "n_trial": n1, "n_base": n2}


def _load_meta(cur):
    cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"✗ 车道 {LANE} 不存在")
    return json.loads(row[0]) if isinstance(row[0], str) else dict(row[0] or {})


def _save_meta(cur, meta):
    cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now() WHERE lane_id=%s",
                (json.dumps(meta, ensure_ascii=False, default=str), LANE))


def _append_ops(meta, entry):
    ops = list(meta.get("ops_changes") or [])
    ops.append(entry)
    meta["ops_changes"] = ops[-20:]
    return meta


def _legs(cur, since, until):
    cur.execute("SELECT net_bp FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz "
                "AND ts <= %s::timestamptz", (LANE, since, until))
    return [float(r[0] or 0.0) for r in cur.fetchall()]


def deploy(cur) -> int:
    meta = _load_meta(cur)
    # [h371] 前置判决守卫：P2 判定未落或仍 INCONCLUSIVE ⇒ 拒绝部署 #4
    _p2v = (meta.get("h354_p2_trial") or {}).get("verdict")
    if _p2v is None:
        print("✗ h354 P2 判定未落，拒绝部署 #4")
        return 1
    if _p2v == "INCONCLUSIVE":
        print("✗ h354 P2 判定仍 INCONCLUSIVE（延长观察中），拒绝部署 #4；"
              "待其最终判决后重试")
        return 1
    # [h405d 2026-09-27] 同车道串行互斥：任何其它试跑进行中 ⇒ 拒绝（单变量纪律）
    for _k in ("h389_trial", "h392_trial", "h396_trial", "h411_trial", "h357_trial", "h362_trial",
               "h363_trial", "h399_trial", "h400_trial", "h401_trial",
               "h404_p1_trial", "h404_p45_trial", "h406_trial"):
        _t = dict(meta.get(_k) or {})
        if _t.get("started_at") and _t.get("verdict") not in ("PASS", "ROLLBACK"):
            print(f"✗ {_k} 进行中 verdict={_t.get('verdict')!r}，拒绝部署 #4"
                  "（同车道试跑串行）")
            return 1
    params = dict(meta.get("params") or {})
    old_vw = float(params.get("vwap_revert_bp") or 0.0)
    old_vfb = float(params.get("vwap_flow_block") or 0.0)
    # 前置：vwap_revert_bp 为 0（#1 已回滚）⇒ 同开两参（P2 形态包）；否则只开 v2 闸
    set_vw = (old_vw <= 0)
    params["vwap_flow_block"] = VFB
    if set_vw:
        params["vwap_revert_bp"] = VW_BP
    meta["params"] = params
    now = _now_iso()
    pass  # [h476 2026-09-29 禁用] was: meta["stats_since"] = now
    meta = _append_ops(meta, {
        "ts": now, "action": "h359_p2v2_deploy",
        "from": {"vwap_revert_bp": old_vw, "vwap_flow_block": old_vfb},
        "to": {"vwap_revert_bp": params["vwap_revert_bp"],
               "vwap_flow_block": params["vwap_flow_block"]},
        "note": "P2 v2：流驱动偏离时回归侧也封（h358 统一流定律）；回滚=--rollback；"
                "判定=T+12h --judge"})
    meta["h359_trial"] = {"started_at": now, "set_vwap": set_vw,
                          "baseline": {"vwap_revert_bp": old_vw, "vwap_flow_block": old_vfb},
                          "judge_at": (dt.datetime.now(dt.timezone.utc)
                                       + dt.timedelta(hours=12)).isoformat()}
    _save_meta(cur, meta)
    print(f"h359 部署完成：vwap_revert_bp {old_vw}→{params['vwap_revert_bp']}，"
          f"vwap_flow_block {old_vfb}→{VFB}（热采用 ≤60s，无需重启）")
    return 0


def rollback(cur) -> int:
    meta = _load_meta(cur)
    trial = dict(meta.get("h359_trial") or {})
    base = trial.get("baseline") or {"vwap_revert_bp": 0.0, "vwap_flow_block": 0.0}
    params = dict(meta.get("params") or {})
    old = {"vwap_revert_bp": float(params.get("vwap_revert_bp") or 0.0),
           "vwap_flow_block": float(params.get("vwap_flow_block") or 0.0)}
    params["vwap_revert_bp"] = float(base.get("vwap_revert_bp", 0.0) or 0.0)
    params["vwap_flow_block"] = float(base.get("vwap_flow_block", 0.0) or 0.0)
    meta["params"] = params
    now = _now_iso()
    pass  # [h476 2026-09-29 禁用] was: meta["stats_since"] = now
    meta = _append_ops(meta, {"ts": now, "action": "h359_p2v2_rollback",
                              "from": old, "to": params, "note": "试跑 #4 回滚"})
    meta["h359_trial"] = {**trial, "rolled_back_at": now}
    _save_meta(cur, meta)
    print(f"h359 回滚完成：{old} → {base}（热采用 ≤60s）")
    return 0


def judge(cur) -> int:
    meta = _load_meta(cur)
    trial = dict(meta.get("h359_trial") or {})
    since = trial.get("started_at")
    if not since:
        print("✗ 无 h359_trial.started_at")
        return 1
    now = _now_iso()
    hours = (dt.datetime.now(dt.timezone.utc)
             - dt.datetime.fromisoformat(since)).total_seconds() / 3600.0
    if hours < 10:
        print(f"✗ 试跑仅 {hours:.1f}h < 10h，未到判定窗口")
        return 2
    base_cut = (dt.datetime.fromisoformat(since) - dt.timedelta(hours=12)).isoformat()
    t_legs = _legs(cur, since, now)
    b_legs = _legs(cur, base_cut, since)
    w = _welch(t_legs, b_legs)
    t_h = len(t_legs) / max(hours, 0.01)
    b_h = len(b_legs) / 12.0
    freq_ok = t_h >= FREQ_FLOOR * b_h
    if not freq_ok:
        verdict, why = "ROLLBACK", f"frequency_collapse {t_h:.1f}/h < {FREQ_FLOOR}×{b_h:.1f}/h"
    elif w is None:
        verdict, why = "INCONCLUSIVE", "insufficient_samples"
    elif w["p"] <= ALPHA and w["delta"] > 0:
        verdict, why = "PASS", f"welch_p={w['p']:.3f} delta={w['delta']:+.3f}bp"
    elif w["p"] <= ALPHA and w["delta"] < 0:
        verdict, why = "ROLLBACK", f"welch_p={w['p']:.3f} delta={w['delta']:+.3f}bp"
    else:
        verdict, why = "INCONCLUSIVE", f"welch_p={w['p']:.3f} delta={w['delta']:+.3f}bp"

    meta = _append_ops(meta, {"ts": now, "action": f"h359_judge_{verdict.lower()}", "note": why})
    if verdict == "ROLLBACK":
        rollback(cur)
        meta = _load_meta(cur)
    meta["h359_trial"] = {**trial, "verdict": verdict, "why": why, "judged_at": now}
    _save_meta(cur, meta)

    res = {"verdict": verdict, "why": why, "hours": hours, "welch": w,
           "trial_legs_h": round(t_h, 1), "baseline_legs_h": round(b_h, 1), "freq_ok": freq_ok}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    print(f"判定已写 {OUT}")
    # [h369b] INCONCLUSIVE 自动延长：+12h 再判定
    if verdict == "INCONCLUSIVE":
        import subprocess
        _nxt = dt.datetime.now() + dt.timedelta(hours=12)
        _tr = ("wscript.exe //B //Nologo "
               r"D:\001Alpha\Hyper-Alpha-Arena\scripts\run-quiet.vbs "
               r"D:\001Alpha\Hyper-Alpha-Arena\.venv\Scripts\python.exe "
               r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h359_p2v2_trial.py --judge")
        _r = subprocess.run(
            ["schtasks", "/Create", "/TN", "DSH_HFT_H359_JUDGE", "/TR", _tr,
             "/SC", "ONCE", "/ST", _nxt.strftime("%H:%M"),
             "/SD", _nxt.strftime("%Y/%m/%d"), "/F"],
            capture_output=True, text=True, timeout=60)
        print(f"[h369b] INCONCLUSIVE ⇒ 已排 +12h 再判定 @ {_nxt:%Y-%m-%d %H:%M} "
              f"rc={_r.returncode}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deploy", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--judge", action="store_true")
    a = ap.parse_args()
    import psycopg
    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            rc = 0
            if a.deploy:
                rc = deploy(cur)
            elif a.rollback:
                rc = rollback(cur)
            elif a.judge:
                rc = judge(cur)
            else:
                print("请给 --deploy / --rollback / --judge 之一")
                return 1
            c.commit()
            return rc


if __name__ == "__main__":
    raise SystemExit(main())
