# -*- coding: utf-8 -*-
"""H400 试跑 #10 部署 / 回滚 / 判定 —— P1 触发区下限（p1_trigger_bp 15→10）。

依据（h366，168h）：P1 触发下限 15→10bp ⇒ 频率 ×1.8、总 edge/h +59%
（+1.22bp@15.1 事件/h vs +1.40bp@8.3 事件/h）。live 映射 = pullback_flow_block
（#3）的 P1 触发区判定 `|300s 趋势| ≥ p1_trigger_bp`（此前硬编码 15，h400 参数化，
默认 15 = 旧行为逐字一致）。只改触发区下限，不动 trend_pause_bp（独立闸）。

部署守卫：
  · h356 PASS + P2 终判（基线稳定）；
  · **h357_trial（#3）verdict == PASS**（本试跑扩的是 #3 闸的触发区，闸必须在场）；
  · 其余试跑（出口侧 #17/#18/#16③、#9、#4/#6/#7）均已判定或未部署（串行互斥）；
  --force 可绕过（trial meta 记 guards_bypassed）。

判定（T+12h）：A legs/h ≥ 0.8×基线（下限放宽应**增加**触发频率，此为方向性校验）；
B 逐腿 net_bp Welch α=0.10；C 子口径：pullback_flow 封锁侧计数不可事后读（进程内），
改报：试跑窗 legs/h 增量 + 分币种 net_bp。

用法:
  python scripts/h400_p1_trigger_trial.py --deploy [--force]
  python scripts/h400_p1_trigger_trial.py --rollback
  python scripts/h400_p1_trigger_trial.py --judge [--force-rollback] [--dry-run]
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
OUT = ROOT / "research_l1" / "out" / "h400_p1trigger_verdict.json"
LANE = "mm_asterdex"
ALPHA = 0.10
FREQ_FLOOR = 0.8
BASELINE_HOURS = 12.0
MIN_JUDGE_HOURS = 10.0
EXTEND_HOURS = 13.0
ON = 10.0
OFF = 15.0
SERIAL_KEYS = ["h389_trial", "h392_trial", "h396_trial", "h411_trial", "h399_trial",
               "h359_trial", "h362_trial", "h363_trial"]


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


def _guards(meta) -> list[str]:
    g = []
    _h356 = dict(meta.get("h356_trial") or {})
    if _h356.get("verdict") != "PASS":
        g.append(f"h356_trial.verdict={_h356.get('verdict')!r}（需 PASS）")
    _h354 = dict(meta.get("h354_p2_trial") or {})
    if not _h354.get("verdict") or _h354.get("verdict") == "INCONCLUSIVE":
        g.append(f"h354_p2_trial.verdict={_h354.get('verdict')!r}（入口侧未终判）")
    _h357 = dict(meta.get("h357_trial") or {})
    if _h357.get("verdict") != "PASS":
        g.append(f"h357_trial（#3）verdict={_h357.get('verdict')!r}（需 PASS——"
                 "本试跑扩的是 #3 闸的触发区，闸必须在场）")
    for k in SERIAL_KEYS:
        t = dict(meta.get(k) or {})
        if t.get("started_at") and t.get("verdict") not in ("PASS", "ROLLBACK"):
            g.append(f"{k} 进行中 verdict={t.get('verdict')!r}（同车道试跑串行）")
    return g


def do_deploy(a) -> int:
    import psycopg
    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            row = cur.fetchone()
            if not row:
                print(f"✗ 车道 {LANE} 不存在")
                return 1
            meta = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0] or {})
            params = dict(meta.get("params") or {})
            old = float(params.get("p1_trigger_bp") or 15.0)
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            guards = _guards(meta)
            if guards and not a.force:
                print("✗ 部署守卫拒绝：")
                for g in guards:
                    print(f"    - {g}")
                print("   若确需强制：--force（trial meta 会记 guards_bypassed）")
                return 3
            params["p1_trigger_bp"] = ON
            meta["params"] = params
            pass  # [h476 2026-09-29 禁用] was: meta["stats_since"] = now_iso
            meta = _append_ops(meta, {
                "ts": now_iso, "action": "h400_p1trigger_deploy",
                "field": "params.p1_trigger_bp", "from": old, "to": ON,
                "note": ("试跑 #10 部署：P1 触发区下限 15→10bp（h366 频率×1.8、edge/h +59%）；"
                         "判定=T+12h；回滚=--rollback（热采用 ≤60s，无需重启）；"
                         + ("【guards_bypassed：--force】" if guards else "")),
            })
            meta["h400_trial"] = {
                "started_at": now_iso, "from": old, "to": ON,
                "baseline_symbols": [str(s) for s in (meta.get("symbols") or []) if str(s)],
                "rollback_to": OFF,
                "judge_at": (dt.datetime.now(dt.timezone.utc)
                             + dt.timedelta(hours=12)).isoformat(),
                "criteria": "A legs/h≥0.8×基线（下限放宽应增频）；B Welch α=0.10；"
                            "C legs/h 增量 + 分币种",
                "guards_bypassed": bool(guards and a.force),
            }
            cur.execute(
                "UPDATE lane_registry SET meta_json = %s, updated_at = now() WHERE lane_id = %s",
                (json.dumps(meta, ensure_ascii=False, default=str), LANE))
            c.commit()

    print(f"✓ h400 #10 部署：p1_trigger_bp {old} → {ON}（热采用 ≤60s，无需重启）")
    _nxt = dt.datetime.now() + dt.timedelta(hours=12)
    _tr = ("wscript.exe //B //Nologo "
           r"D:\001Alpha\Hyper-Alpha-Arena\scripts\run-quiet.vbs "
           r"D:\001Alpha\Hyper-Alpha-Arena\.venv\Scripts\python.exe "
           r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h400_p1_trigger_trial.py --judge")
    _r = subprocess.run(
        ["schtasks", "/Create", "/TN", "DSH_HFT_H400_JUDGE", "/TR", _tr,
         "/SC", "ONCE", "/ST", _nxt.strftime("%H:%M"),
         "/SD", _nxt.strftime("%Y/%m/%d"), "/F"],
        capture_output=True, text=True, timeout=60)
    print(f"初始判定任务 @ {_nxt:%Y-%m-%d %H:%M} rc={_r.returncode}")
    return 0 if _r.returncode == 0 else 4


def do_rollback() -> int:
    import psycopg
    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            row = cur.fetchone()
            if not row:
                print(f"✗ 车道 {LANE} 不存在")
                return 1
            meta = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0] or {})
            params = dict(meta.get("params") or {})
            old = float(params.get("p1_trigger_bp") or 15.0)
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            params["p1_trigger_bp"] = OFF
            meta["params"] = params
            pass  # [h476 2026-09-29 禁用] was: meta["stats_since"] = now_iso
            meta = _append_ops(meta, {
                "ts": now_iso, "action": "h400_p1trigger_rollback",
                "field": "params.p1_trigger_bp", "from": old, "to": OFF,
                "note": "试跑 #10 回滚（热采用 ≤60s，无需重启）"})
            meta["h400_trial"] = {**dict(meta.get("h400_trial") or {}),
                                  "rolled_back_at": now_iso, "verdict": "ROLLBACK",
                                  "why": "manual_rollback"}
            cur.execute(
                "UPDATE lane_registry SET meta_json = %s, updated_at = now() WHERE lane_id = %s",
                (json.dumps(meta, ensure_ascii=False, default=str), LANE))
            c.commit()
    print(f"✓ h400 回滚：p1_trigger_bp {old} → {OFF}（热采用）")
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


def do_judge(a) -> int:
    import psycopg
    _out = OUT if not a.dry_run else OUT.with_name("h400_p1trigger_verdict_dryrun.json")
    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            row = cur.fetchone()
            if not row:
                print(f"✗ 车道 {LANE} 不存在")
                return 1
            meta = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0] or {})
            trial = dict(meta.get("h400_trial") or {})
            since = trial.get("started_at")
            if not since:
                print("✗ meta.h400_trial.started_at 缺失（试跑未部署）")
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
                                      "action": f"h400_p1trigger_judge_{verdict.lower()}",
                                      "note": why, "trial": tsum, "baseline": bsum,
                                      "welch": w})
            if verdict == "ROLLBACK":
                params = dict(meta.get("params") or {})
                old = float(params.get("p1_trigger_bp") or 15.0)
                params["p1_trigger_bp"] = OFF
                meta["params"] = params
                pass  # [h476 2026-09-29 禁用] was: meta["stats_since"] = now_iso
                meta["h400_trial"] = {**trial, "verdict": verdict,
                                      "rolled_back_at": now_iso, "why": why,
                                      "baseline_symbols": syms}
                meta = _append_ops(meta, {
                    "ts": now_iso, "action": "h400_p1trigger_rollback",
                    "field": "params.p1_trigger_bp", "from": old, "to": OFF,
                    "note": f"12h 判定：{why}"})
            else:
                meta["h400_trial"] = {**trial, "verdict": verdict,
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
                      f"p1_trigger_bp 将{'恢复 15' if verdict == 'ROLLBACK' else '保持 10'}")

    res = {"verdict": verdict, "why": why, "judged_at": now_iso, "hours": hours,
           "trial": tsum, "baseline": bsum, "welch": w, "freq_ok": freq_ok,
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
               r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h400_p1_trigger_trial.py --judge")
        _r = subprocess.run(
            ["schtasks", "/Create", "/TN", "DSH_HFT_H400_JUDGE", "/TR", _tr,
             "/SC", "ONCE", "/ST", _nxt.strftime("%H:%M"),
             "/SD", _nxt.strftime("%Y/%m/%d"), "/F"],
            capture_output=True, text=True, timeout=60)
        print(f"INCONCLUSIVE ⇒ 已排 +{EXTEND_HOURS:.0f}h 再判定 @ "
              f"{_nxt:%Y-%m-%d %H:%M} rc={_r.returncode}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="H400 #10 P1 触发下限试跑：部署/回滚/判定")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--deploy", action="store_true")
    g.add_argument("--rollback", action="store_true")
    g.add_argument("--judge", action="store_true")
    ap.add_argument("--force", action="store_true", help="部署：绕过守卫")
    ap.add_argument("--force-rollback", action="store_true", help="判定：强制回滚")
    ap.add_argument("--dry-run", action="store_true", help="判定：预演不落库")
    a = ap.parse_args()
    if a.deploy:
        return do_deploy(a)
    if a.rollback:
        return do_rollback()
    return do_judge(a)


if __name__ == "__main__":
    raise SystemExit(main())
