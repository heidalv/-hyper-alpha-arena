# -*- coding: utf-8 -*-
"""H354 P2 微试跑 12h 判定（自动执行，可手动重跑）。

口径：
  - 基线：research_l1/out/h354_p2_baseline.json（部署前 12h 车道账本）
  - 试跑：lane_registry.meta.h354_p2_trial.started_at → now（部署后 12h）

判定规则（预先注册，杜绝事后挑口径）：
  A. 频率硬性要求：试跑 legs/h < 0.8 × 基线 legs/h ⇒ 回滚（用户高频使命）。
  B. 逐腿净 bp 双侧 Welch t 检验（试跑 vs 基线，α=0.10）：
     - p ≤ 0.10 且 Δ>0 ⇒ 保留（PASS）
     - p ≤ 0.10 且 Δ<0 ⇒ 回滚（FAIL）
     - p > 0.10 ⇒ 无结论（INCONCLUSIVE）：不构成伤害证据，保留并延长观察 12h
  C. 无论哪种结论，写入 research_l1/out/h354_p2_verdict.json 并追加 ops_changes。

回滚动作：vwap_revert_bp → 0（meta 热采用，60s 内生效，无需重启 worker）。

用法: python scripts/h354_p2_judge.py [--force-rollback]
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
OUT = ROOT / "research_l1" / "out" / "h354_p2_verdict.json"
BASELINE = ROOT / "research_l1" / "out" / "h354_p2_baseline.json"
LANE = "mm_asterdex"
ALPHA = 0.10
FREQ_FLOOR = 0.8


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


def _welch(a: list[float], b: list[float]):
    """双侧 Welch t 检验，返回 (t, p, mean_a, mean_b, n_a, n_b)。"""
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
    # 双侧 p：Student t 尾概率（数值积分，够用）
    def _pdf(x):
        return math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) \
            / math.sqrt(math.pi * df) * (1 + x * x / df) ** (-(df + 1) / 2)

    step, s, x = 0.05, 0.0, abs(t)
    while x < abs(t) + 30.0:
        s += step * (_pdf(x) + _pdf(x + step)) / 2
        x += step
    p = 2 * s  # 双侧
    return {"t": t, "p": min(1.0, p), "mean_trial": m1, "mean_base": m2,
            "n_trial": n1, "n_base": n2, "delta": m1 - m2}


def _per_leg_netbp(cursor, since_iso: str, until_iso: str, symbols=None):
    _sym_f = " AND symbol = ANY(%s)" if symbols else ""
    _q = ("SELECT net_bp FROM lane_ledger "
          "WHERE lane_id=%s AND ts > %s::timestamptz AND ts <= %s::timestamptz" + _sym_f)
    _p = (LANE, since_iso, until_iso) + ((symbols,) if symbols else ())
    cursor.execute(_q, _p)
    return [float(r[0] or 0.0) for r in cursor.fetchall()]


def _window_summary(cursor, since_iso: str, until_iso: str, hours: float, symbols=None):
    _sym_f = " AND symbol = ANY(%s)" if symbols else ""
    cursor.execute(
        "SELECT count(*) AS legs, sum(net_bp)::float8 AS net_bp,"
        " sum(notional)::float8 AS notional"
        " FROM lane_ledger"
        " WHERE lane_id=%s AND ts > %s::timestamptz AND ts <= %s::timestamptz" + _sym_f,
        (LANE, since_iso, until_iso) + ((symbols,) if symbols else ()))
    r = cursor.fetchone()
    legs = int(r[0] or 0)
    return {"legs": legs, "net_bp": float(r[1] or 0.0), "notional": float(r[2] or 0.0),
            "legs_per_hour": legs / max(hours, 0.01),
            "net_bp_per_leg": (float(r[1] or 0.0) / legs) if legs else 0.0}


def _append_ops(meta: dict, entry: dict) -> dict:
    ops = list(meta.get("ops_changes") or [])
    ops.append(entry)
    meta["ops_changes"] = ops[-20:]
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-rollback", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="完整判定路径但不写库、不排定时器（预演）")
    a = ap.parse_args()

    import psycopg

    _out_path = OUT
    if a.dry_run:
        _out_path = OUT.with_name("h354_p2_verdict_dryrun.json")

    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            row = cur.fetchone()
            if not row:
                print(f"✗ 车道 {LANE} 不存在")
                return 1
            meta = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0] or {})
            trial = dict(meta.get("h354_p2_trial") or {})
            since_iso = trial.get("started_at")
            if not since_iso:
                print("✗ meta.h354_p2_trial.started_at 缺失，无法判定")
                return 1
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            hours = (dt.datetime.now(dt.timezone.utc)
                     - dt.datetime.fromisoformat(since_iso)).total_seconds() / 3600.0
            if hours < 10 and not a.force_rollback and not a.dry_run:
                print(f"✗ 试跑仅 {hours:.1f}h < 10h，未到判定窗口")
                return 2

            # [h384 2026-09-27] 基线币种过滤：延长再判定时（+13h）试跑窗会混入
            # #2 宇宙扩容的新币 ⇒ 用首判时刻的宇宙过滤两个窗口，保证口径可比。
            _syms = trial.get("baseline_symbols")
            if not _syms:
                _syms = [str(s) for s in (meta.get("symbols") or [])]
            trial_legs = _per_leg_netbp(cur, since_iso, now_iso, _syms)
            base_cut = (dt.datetime.fromisoformat(since_iso)
                        - dt.timedelta(hours=12)).isoformat()
            base_legs = _per_leg_netbp(cur, base_cut, since_iso, _syms)
            w = _welch(trial_legs, base_legs)
            tsum = _window_summary(cur, since_iso, now_iso, hours, _syms)
            bsum = _window_summary(cur, base_cut, since_iso, 12.0, _syms)

            freq_ok = (tsum["legs_per_hour"] >= FREQ_FLOOR * bsum["legs_per_hour"])
            if a.force_rollback:
                verdict = "ROLLBACK"
                why = "manual_force_rollback"
            elif not freq_ok:
                verdict = "ROLLBACK"
                why = f"frequency_collapse {tsum['legs_per_hour']:.1f}/h < "
                why += f"{FREQ_FLOOR}×{bsum['legs_per_hour']:.1f}/h"
            elif w is None:
                verdict, why = "INCONCLUSIVE", "insufficient_samples"
            elif w["p"] <= ALPHA and w["delta"] > 0:
                verdict, why = "PASS", f"welch_p={w['p']:.3f} delta={w['delta']:+.3f}bp"
            elif w["p"] <= ALPHA and w["delta"] < 0:
                verdict, why = "ROLLBACK", f"welch_p={w['p']:.3f} delta={w['delta']:+.3f}bp"
            else:
                verdict, why = "INCONCLUSIVE", f"welch_p={w['p']:.3f} delta={w['delta']:+.3f}bp"

            entry = {"ts": now_iso, "action": f"h354_p2_judge_{verdict.lower()}",
                     "note": why,
                     "trial": tsum, "baseline": bsum, "welch": w}
            meta = _append_ops(meta, entry)

            if verdict == "ROLLBACK":
                params = dict(meta.get("params") or {})
                old = float(params.get("vwap_revert_bp") or 0.0)
                params["vwap_revert_bp"] = 0.0
                meta["params"] = params
                pass  # [h476 2026-09-29 禁用] was: meta["stats_since"] = now_iso
                meta["h354_p2_trial"] = {**trial, "verdict": verdict,
                                          "rolled_back_at": now_iso, "why": why,
                                          "baseline_symbols": _syms}
                meta = _append_ops(meta, {
                    "ts": now_iso, "action": "h354_p2_rollback",
                    "field": "params.vwap_revert_bp", "from": old, "to": 0.0,
                    "note": f"12h 判定：{why}"})
            else:
                meta["h354_p2_trial"] = {**trial, "verdict": verdict,
                                         "judged_at": now_iso, "why": why,
                                         "baseline_symbols": _syms,
                                         "extend_until": (dt.datetime.now(dt.timezone.utc)
                                                          + dt.timedelta(hours=13)).isoformat()
                                         if verdict == "INCONCLUSIVE" else None}

            # [h383 2026-09-27 修正] dry-run 绝不执行 UPDATE：psycopg 的 with 块在
            # 退出时会自动 commit，此前 dry-run 的 UPDATE 被真实落库（已造成一次
            # verdict=INCONCLUSIVE 的提前写入，良性但危险——若当时判决为 ROLLBACK
            # 会把线上闸直接回滚）。
            if not a.dry_run:
                cur.execute("UPDATE lane_registry SET meta_json=%s, updated_at=now() "
                            "WHERE lane_id=%s",
                            (json.dumps(meta, ensure_ascii=False, default=str), LANE))
                c.commit()
            else:
                print(f"[dry-run] 不落库。若实际执行：verdict={verdict} "
                      f"vwap_revert_bp 将{'置 0' if verdict == 'ROLLBACK' else '保持 2.0'}")

    res = {"verdict": verdict, "why": why, "judged_at": now_iso, "hours": hours,
           "trial": tsum, "baseline": bsum, "welch": w, "freq_ok": freq_ok,
           "dry_run": bool(a.dry_run)}
    _out_path.parent.mkdir(parents=True, exist_ok=True)
    _out_path.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    print(f"判定已写 {_out_path}")
    if verdict == "ROLLBACK" and not a.dry_run:
        print("已回滚：vwap_revert_bp=0（meta 热采用 ≤60s，无需重启）")

    # [h354→h356 修正 2026-09-27] 选币任务的恢复移到 h356 判定脚本：
    # P2 判定（12:38）后 13:00 即部署 #2 宇宙试跑，若此刻恢复选币任务，
    # 其 18:33 的 --apply 会在 #2 试跑中途换币污染口径 ⇒ 由 #2 判定（次日 01:00）
    # 恢复（SELECTOR 现配置 = --hours 168 --slots 4）。

    # [h369b 2026-09-27] INCONCLUSIVE 自动延长：预定 +12h 的再判定任务
    # （此前只写 extend_until 但没有定时器 ⇒ 延长观察从未被执行过）。
    # [h381 2026-09-27] 延长改为 +13h：落点 01:38 次日，在 #2 判定（01:00）之后 ⇒
    # P2 若此刻回滚不会污染 #2 试跑的最后 22 分钟；#3 部署守卫会因 P2 未决而拒绝
    # （见 h357_flow_gate_trial.py），会话轮次在 01:38 终判后重新部署 #3。
    if verdict == "INCONCLUSIVE" and not a.dry_run:
        import subprocess
        _nxt = dt.datetime.now() + dt.timedelta(hours=13)
        _tr = ("wscript.exe //B //Nologo "
               r"D:\001Alpha\Hyper-Alpha-Arena\scripts\run-quiet.vbs "
               r"D:\001Alpha\Hyper-Alpha-Arena\.venv\Scripts\python.exe "
               r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h354_p2_judge.py")
        _r = subprocess.run(
            ["schtasks", "/Create", "/TN", "DSH_HFT_P2_JUDGE", "/TR", _tr,
             "/SC", "ONCE", "/ST", _nxt.strftime("%H:%M"),
             "/SD", _nxt.strftime("%Y/%m/%d"), "/F"],
            capture_output=True, text=True, timeout=60)
        print(f"[h369b] INCONCLUSIVE ⇒ 已排 +12h 再判定 @ {_nxt:%Y-%m-%d %H:%M} "
              f"rc={_r.returncode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
