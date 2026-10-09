# -*- coding: utf-8 -*-
"""H404 试跑 #15/#8 部署 / 回滚 / 判定 —— 分形态持有期（p1_hold_sec / p45_hold_sec）。

依据：
  · #15（--kind p1）：P1 回调腿持有期 120s 栈 → 60s——h360 fixed60 +0.80 ≈
    fixed120 +1.08（第一半窗红利，h373 OOS）；前置 #3（P1 薄流闸）PASS。
  · #8（--kind p45）：P4/P5 突破腿持有期 → 300s——h360b fixed300 +1.085(P4)/
    +0.889(P5) vs 现行栈 +0.752/+0.689；前置 #6/#7（P4/P5 with 闸）PASS。
实现：开仓时刻按 mid_hist 形态标记（研究口径固定阈值，与闸门参数无关；
h403 代码已实现，默认 0=旧行为逐字）。纯参数热采用，无需重启。

用法:
  python scripts/h404_pattern_hold_trial.py --kind p1 --deploy [--force]
  python scripts/h404_pattern_hold_trial.py --kind p45 --deploy [--force]
  python scripts/h404_pattern_hold_trial.py --kind p1 --rollback
  python scripts/h404_pattern_hold_trial.py --kind p45 --judge [--force-rollback] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LANE = "mm_asterdex"
ALPHA = 0.10
FREQ_FLOOR = 0.8
BASELINE_HOURS = 12.0
MIN_JUDGE_HOURS = 10.0
EXTEND_HOURS = 13.0

KINDS = {
    "p1": {
        "field": "p1_hold_sec", "on": 60.0, "off": 0.0,
        "meta_key": "h404_p1_trial", "task": "DSH_HFT_H404_P1_JUDGE",
        "out": ROOT / "research_l1" / "out" / "h404_p1_verdict.json",
        "prereq": ("h357_trial", "#3 P1 薄流闸"),
        "note": "#15 P1 持有期 120s→60s（h360 fixed60 +0.80 ≈ fixed120 +1.08）",
    },
    "p45": {
        "field": "p45_hold_sec", "on": 300.0, "off": 0.0,
        "meta_key": "h404_p45_trial", "task": "DSH_HFT_H404_P45_JUDGE",
        "out": ROOT / "research_l1" / "out" / "h404_p45_verdict.json",
        "prereq": ("h363_trial", "#6/#7 P4/P5 with 闸"),
        "note": "#8 P4/P5 持有期 →300s（h360b fixed300 +1.085/+0.889）",
    },
}


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


def _append_ops(meta, entry):
    ops = list(meta.get("ops_changes") or [])
    ops.append(entry)
    meta["ops_changes"] = ops[-20:]
    return meta


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
    p = 2 * s
    return {"t": t, "p": min(1.0, p), "mean_trial": m1, "mean_base": m2,
            "n_trial": n1, "n_base": n2, "delta": m1 - m2}


def _serial_keys(kind: str) -> list[str]:
    keys = ["h389_trial", "h392_trial", "h396_trial", "h411_trial", "h399_trial",
            "h400_trial", "h401_trial", "h357_trial", "h359_trial",
            "h362_trial", "h363_trial", "h404_p1_trial", "h404_p45_trial"]
    self_key = KINDS[kind]["meta_key"]
    return [k for k in keys if k != self_key]


def _guards(meta, kind: str) -> list[str]:
    g = []
    _h356 = dict(meta.get("h356_trial") or {})
    if _h356.get("verdict") != "PASS":
        g.append(f"h356_trial.verdict={_h356.get('verdict')!r}（需 PASS）")
    _h354 = dict(meta.get("h354_p2_trial") or {})
    if not _h354.get("verdict") or _h354.get("verdict") == "INCONCLUSIVE":
        g.append(f"h354_p2_trial.verdict={_h354.get('verdict')!r}（入口侧未终判）")
    prereq_key, prereq_name = KINDS[kind]["prereq"]
    _pre = dict(meta.get(prereq_key) or {})
    if _pre.get("verdict") != "PASS":
        g.append(f"{prereq_key}（{prereq_name}）verdict={_pre.get('verdict')!r}"
                 "（需 PASS——分形态持有期建立在对应闸已生效的腿上）")
    for k in _serial_keys(kind):
        t = dict(meta.get(k) or {})
        if t.get("started_at") and t.get("verdict") not in ("PASS", "ROLLBACK"):
            g.append(f"{k} 进行中 verdict={t.get('verdict')!r}（同车道试跑串行）")
    return g


def do_deploy(a, kind: str) -> int:
    import psycopg
    K = KINDS[kind]
    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            row = cur.fetchone()
            if not row:
                print(f"✗ 车道 {LANE} 不存在")
                return 1
            meta = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0] or {})
            params = dict(meta.get("params") or {})
            old = float(params.get(K["field"]) or 0.0)
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            guards = _guards(meta, kind)
            if guards and not a.force:
                print("✗ 部署守卫拒绝：")
                for g in guards:
                    print(f"    - {g}")
                print("   若确需强制：--force（trial meta 会记 guards_bypassed）")
                return 3
            params[K["field"]] = K["on"]
            meta["params"] = params
            pass  # [h476 2026-09-29 禁用] was: meta["stats_since"] = now_iso
            meta = _append_ops(meta, {
                "ts": now_iso, "action": f"h404_{kind}_deploy",
                "field": f"params.{K['field']}", "from": old, "to": K["on"],
                "note": (f"试跑 {K['note']}；判定=T+12h；回滚=--rollback（热采用）；"
                         + ("【guards_bypassed：--force】" if guards else "")),
            })
            meta[K["meta_key"]] = {
                "started_at": now_iso, "from": old, "to": K["on"], "kind": kind,
                "baseline_symbols": [str(s) for s in (meta.get("symbols") or []) if str(s)],
                "rollback_to": K["off"],
                "judge_at": (dt.datetime.now(dt.timezone.utc)
                             + dt.timedelta(hours=12)).isoformat(),
                "criteria": "A legs/h≥0.8×基线；B Welch α=0.10；C 分币种 + 超时腿均值",
                "guards_bypassed": bool(guards and a.force),
            }
            cur.execute(
                "UPDATE lane_registry SET meta_json = %s, updated_at = now() WHERE lane_id = %s",
                (json.dumps(meta, ensure_ascii=False, default=str), LANE))
            c.commit()

    print(f"✓ h404 {kind} 部署：{K['field']} {old} → {K['on']}（热采用 ≤60s，无需重启）")
    _nxt = dt.datetime.now() + dt.timedelta(hours=12)
    _tr = ("wscript.exe //B //Nologo "
           r"D:\001Alpha\Hyper-Alpha-Arena\scripts\run-quiet.vbs "
           r"D:\001Alpha\Hyper-Alpha-Arena\.venv\Scripts\python.exe "
           rf"D:\001Alpha\Hyper-Alpha-Arena\scripts\h404_pattern_hold_trial.py --kind {kind} --judge")
    _r = subprocess.run(
        ["schtasks", "/Create", "/TN", K["task"], "/TR", _tr,
         "/SC", "ONCE", "/ST", _nxt.strftime("%H:%M"),
         "/SD", _nxt.strftime("%Y/%m/%d"), "/F"],
        capture_output=True, text=True, timeout=60)
    print(f"初始判定任务 @ {_nxt:%Y-%m-%d %H:%M} rc={_r.returncode}")
    return 0 if _r.returncode == 0 else 4


def do_rollback(kind: str) -> int:
    import psycopg
    K = KINDS[kind]
    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            row = cur.fetchone()
            if not row:
                print(f"✗ 车道 {LANE} 不存在")
                return 1
            meta = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0] or {})
            params = dict(meta.get("params") or {})
            old = float(params.get(K["field"]) or 0.0)
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            params[K["field"]] = K["off"]
            meta["params"] = params
            pass  # [h476 2026-09-29 禁用] was: meta["stats_since"] = now_iso
            meta = _append_ops(meta, {
                "ts": now_iso, "action": f"h404_{kind}_rollback",
                "field": f"params.{K['field']}", "from": old, "to": K["off"],
                "note": "分形态持有期回滚（热采用 ≤60s，无需重启）"})
            meta[K["meta_key"]] = {**dict(meta.get(K["meta_key"]) or {}),
                                   "rolled_back_at": now_iso, "verdict": "ROLLBACK",
                                   "why": "manual_rollback"}
            cur.execute(
                "UPDATE lane_registry SET meta_json = %s, updated_at = now() WHERE lane_id = %s",
                (json.dumps(meta, ensure_ascii=False, default=str), LANE))
            c.commit()
    print(f"✓ h404 {kind} 回滚：{K['field']} {old} → {K['off']}（热采用）")
    return 0


def _per_leg(cur, since, until, syms):
    _f = " AND symbol = ANY(%s)" if syms else ""
    cur.execute("SELECT net_bp FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz "
                "AND ts <= %s::timestamptz" + _f,
                (LANE, since, until) + ((syms,) if syms else ()))
    return [float(r[0] or 0.0) for r in cur.fetchall()]


def _window(cur, since, until, hours, syms):
    _f = " AND symbol = ANY(%s)" if syms else ""
    cur.execute("SELECT count(*), COALESCE(sum(net_bp),0.0)::float8 FROM lane_ledger "
                "WHERE lane_id=%s AND ts > %s::timestamptz AND ts <= %s::timestamptz" + _f,
                (LANE, since, until) + ((syms,) if syms else ()))
    r = cur.fetchone()
    legs = int(r[0] or 0)
    return {"legs": legs, "net_bp": float(r[1] or 0.0),
            "legs_per_hour": legs / max(hours, 0.01)}


def do_judge(a, kind: str) -> int:
    import psycopg
    K = KINDS[kind]
    _out = K["out"] if not a.dry_run else K["out"].with_name(K["out"].stem + "_dryrun.json")
    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            row = cur.fetchone()
            if not row:
                print(f"✗ 车道 {LANE} 不存在")
                return 1
            meta = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0] or {})
            trial = dict(meta.get(K["meta_key"]) or {})
            since = trial.get("started_at")
            if not since:
                print(f"✗ meta.{K['meta_key']}.started_at 缺失（试跑未部署）")
                return 1
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            hours = (dt.datetime.now(dt.timezone.utc)
                     - dt.datetime.fromisoformat(since)).total_seconds() / 3600.0
            if hours < MIN_JUDGE_HOURS and not a.force_rollback and not a.dry_run:
                print(f"✗ 试跑仅 {hours:.1f}h < {MIN_JUDGE_HOURS:.0f}h，未到判定窗口")
                return 2
            syms = trial.get("baseline_symbols") or \
                [str(s) for s in (meta.get("symbols") or []) if str(s)]
            t_legs = _per_leg(cur, since, now_iso, syms)
            base_cut = (dt.datetime.fromisoformat(since)
                        - dt.timedelta(hours=BASELINE_HOURS)).isoformat()
            b_legs = _per_leg(cur, base_cut, since, syms)
            w = _welch(t_legs, b_legs)
            tsum = _window(cur, since, now_iso, hours, syms)
            bsum = _window(cur, base_cut, since, BASELINE_HOURS, syms)

            freq_ok = tsum["legs_per_hour"] >= FREQ_FLOOR * bsum["legs_per_hour"]
            if a.force_rollback:
                verdict, why = "ROLLBACK", "manual_force_rollback"
            elif not freq_ok:
                verdict = "ROLLBACK"
                why = f"frequency_collapse {tsum['legs_per_hour']:.1f}/h < " \
                      f"{FREQ_FLOOR}×{bsum['legs_per_hour']:.1f}/h"
            elif w is None:
                verdict, why = "INCONCLUSIVE", "insufficient_samples"
            elif w["p"] <= ALPHA and w["delta"] > 0:
                verdict, why = "PASS", f"welch_p={w['p']:.3f} delta={w['delta']:+.3f}bp"
            elif w["p"] <= ALPHA and w["delta"] < 0:
                verdict, why = "ROLLBACK", f"welch_p={w['p']:.3f} delta={w['delta']:+.3f}bp"
            else:
                verdict, why = "INCONCLUSIVE", f"welch_p={w['p']:.3f} delta={w['delta']:+.3f}bp"

            meta = _append_ops(meta, {"ts": now_iso,
                                      "action": f"h404_{kind}_judge_{verdict.lower()}",
                                      "note": why, "trial": tsum, "baseline": bsum,
                                      "welch": w})
            if verdict == "ROLLBACK":
                params = dict(meta.get("params") or {})
                old = float(params.get(K["field"]) or 0.0)
                params[K["field"]] = K["off"]
                meta["params"] = params
                pass  # [h476 2026-09-29 禁用] was: meta["stats_since"] = now_iso
                meta[K["meta_key"]] = {**trial, "verdict": verdict,
                                       "rolled_back_at": now_iso, "why": why,
                                       "baseline_symbols": syms}
                meta = _append_ops(meta, {
                    "ts": now_iso, "action": f"h404_{kind}_rollback",
                    "field": f"params.{K['field']}", "from": old, "to": K["off"],
                    "note": f"12h 判定：{why}"})
            else:
                meta[K["meta_key"]] = {**trial, "verdict": verdict,
                                       "judged_at": now_iso, "why": why,
                                       "baseline_symbols": syms,
                                       "extend_until": (dt.datetime.now(dt.timezone.utc)
                                                        + dt.timedelta(hours=EXTEND_HOURS))
                                       .isoformat() if verdict == "INCONCLUSIVE" else None}
            if not a.dry_run:
                cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now() "
                            "WHERE lane_id=%s",
                            (json.dumps(meta, ensure_ascii=False, default=str), LANE))
                c.commit()
            else:
                print(f"[dry-run] 不落库。若实际执行：verdict={verdict} "
                      f"{K['field']} 将{'归零' if verdict == 'ROLLBACK' else '保持'}")

    res = {"verdict": verdict, "why": why, "judged_at": now_iso, "hours": hours,
           "kind": kind, "trial": tsum, "baseline": bsum, "welch": w, "freq_ok": freq_ok,
           "dry_run": bool(a.dry_run)}
    _out.parent.mkdir(parents=True, exist_ok=True)
    _out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    print(f"判定已写 {_out}")

    if verdict == "INCONCLUSIVE" and not a.dry_run:
        _nxt = dt.datetime.now() + dt.timedelta(hours=EXTEND_HOURS)
        _tr = ("wscript.exe //B //Nologo "
               r"D:\001Alpha\Hyper-Alpha-Arena\scripts\run-quiet.vbs "
               r"D:\001Alpha\Hyper-Alpha-Arena\.venv\Scripts\python.exe "
               rf"D:\001Alpha\Hyper-Alpha-Arena\scripts\h404_pattern_hold_trial.py --kind {kind} --judge")
        _r = subprocess.run(
            ["schtasks", "/Create", "/TN", K["task"], "/TR", _tr,
             "/SC", "ONCE", "/ST", _nxt.strftime("%H:%M"),
             "/SD", _nxt.strftime("%Y/%m/%d"), "/F"],
            capture_output=True, text=True, timeout=60)
        print(f"INCONCLUSIVE ⇒ 已排 +{EXTEND_HOURS:.0f}h 再判定 @ "
              f"{_nxt:%Y-%m-%d %H:%M} rc={_r.returncode}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="H404 分形态持有期试跑：部署/回滚/判定")
    ap.add_argument("--kind", required=True, choices=["p1", "p45"])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--deploy", action="store_true")
    g.add_argument("--rollback", action="store_true")
    g.add_argument("--judge", action="store_true")
    ap.add_argument("--force", action="store_true", help="部署：绕过守卫")
    ap.add_argument("--force-rollback", action="store_true", help="判定：强制回滚")
    ap.add_argument("--dry-run", action="store_true", help="判定：预演不落库")
    a = ap.parse_args()
    if a.deploy:
        return do_deploy(a, a.kind)
    if a.rollback:
        return do_rollback(a.kind)
    return do_judge(a, a.kind)


if __name__ == "__main__":
    raise SystemExit(main())
