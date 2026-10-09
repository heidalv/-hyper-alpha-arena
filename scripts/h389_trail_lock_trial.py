# -*- coding: utf-8 -*-
"""H389 试跑 #9 部署 / 回滚 / 判定 —— 尾随锁利（trail_lock_bp）。

预注册协议：`研究结论/h389_trail_lock_trial_protocol_20260927.md`。

部署（幂等）：
  python scripts/h389_trail_lock_trial.py --deploy [--bp 20] [--force]
  1. 守卫（治理规则：单变量试跑必须落在已裁决的稳定基线上）：
     · h356_trial（#2 宇宙试跑）verdict 必须存在且 == PASS；
     · h354_p2_trial（P2 入口侧）verdict 必须存在且 != INCONCLUSIVE；
     `--force` 可绕过（trial meta 里记 guards_bypassed）。
  2. meta.params.trail_lock_bp = bp（20，0=关）。
  3. meta.h389_trial 写预注册元信息（started_at / judge_at / baseline_symbols / bp）。
  4. meta.stats_since 重置（新统计时代）。
  5. ops_changes 审计。
  6. 重启 worker（h218）：尾随逻辑是**新代码**，仅热采用参数不够。
  7. 排初始 T+12h 判定任务（DSH_HFT_H389_JUDGE，ONCE）。

回滚：
  python scripts/h389_trail_lock_trial.py --rollback
  ⇒ trail_lock_bp=0（参数热采用 ≤60s，无需重启），stats_since 重置，ops 审计。

判定（T+12h，自动执行，可手动重跑）：
  python scripts/h389_trail_lock_trial.py --judge [--force-rollback] [--dry-run]
  口径：基线 = 部署前 12h（baseline_symbols 过滤，h384 修正），试跑 = started_at → now。
  A. legs/h < 0.8×基线 ⇒ 回滚（用户高频使命）；
  B. 逐腿净 bp 双侧 Welch α=0.10：p≤0.10 且 Δ>0 ⇒ PASS；p≤0.10 且 Δ<0 ⇒ 回滚；
     否则 INCONCLUSIVE ⇒ 排 +13h 再判（schtasks DSH_HFT_H389_JUDGE，h369b 修正）；
  C. 子口径必报（协议 §4C）：
     · stop_loss_taker 腿数 / 均值 bp（试跑 vs 基线，应显著收窄）；
     · trail_lock_taker 腿数 / 均值 bp；
     · 被尾随腿的 +30min 后续 MFE（回踩后再冲的腿被提前出场的机会成本）。
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
OUT = ROOT / "research_l1" / "out" / "h389_trail_verdict.json"
LANE = "mm_asterdex"
DEFAULT_BP = 20.0
ALPHA = 0.10
FREQ_FLOOR = 0.8
BASELINE_HOURS = 12.0
MIN_JUDGE_HOURS = 10.0
EXTEND_HOURS = 13.0
OPP_WINDOW_SEC = 1800.0   # 被尾随腿出场后 +30min 的后续 MFE 观察窗


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


def _append_ops(meta: dict, entry: dict) -> dict:
    ops = list(meta.get("ops_changes") or [])
    ops.append(entry)
    meta["ops_changes"] = ops[-20:]
    return meta


# ───────────────────────────── 部署 / 回滚 ─────────────────────────────

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
            old_bp = float(params.get("trail_lock_bp") or 0.0)
            new_bp = a.bp
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()

            # ── 守卫：基线必须已裁决且稳定 ──
            guards = []
            _h356 = dict(meta.get("h356_trial") or {})
            if _h356.get("verdict") != "PASS":
                guards.append(f"h356_trial.verdict={_h356.get('verdict')!r}（需 PASS，#2 宇宙未裁决/被回滚）")
            _h354 = dict(meta.get("h354_p2_trial") or {})
            if not _h354.get("verdict") or _h354.get("verdict") == "INCONCLUSIVE":
                guards.append(f"h354_p2_trial.verdict={_h354.get('verdict')!r}（入口侧未终判，试跑会污染口径）")
            # [h389] 与 #3 互斥：同车道试跑必须串行（h357 deploy 亦含反向守卫）。
            _h357 = dict(meta.get("h357_trial") or {})
            if _h357.get("started_at") and _h357.get("verdict") in (None, "INCONCLUSIVE"):
                guards.append(f"h357_trial（#3）进行中 verdict={_h357.get('verdict')!r}（同车道，试跑必须串行）")
            # [h405g 2026-09-27] #19 硬上限（用户裁决：合规优先，先于 #17）进行中 ⇒ 拒绝
            _h411 = dict(meta.get("h411_trial") or {})
            if _h411.get("started_at") and _h411.get("verdict") in (None, "INCONCLUSIVE"):
                guards.append(f"h411_trial（#19 硬上限）进行中 verdict={_h411.get('verdict')!r}（同车道，试跑必须串行）")
            if guards and not a.force:
                print("✗ 部署守卫拒绝（治理规则：单变量试跑必须落在已裁决的稳定基线上）：")
                for g in guards:
                    print(f"    - {g}")
                print("   若确需强制：--force（trial meta 会记 guards_bypassed）")
                return 3

            params["trail_lock_bp"] = new_bp
            meta["params"] = params
            pass  # [h476 2026-09-29 禁用] was: meta["stats_since"] = now_iso
            meta = _append_ops(meta, {
                "ts": now_iso,
                "action": "h389_trail_deploy",
                "field": "params.trail_lock_bp",
                "from": old_bp,
                "to": new_bp,
                "note": ("试跑 #9 部署：MFE≥20bp 后止损线抬至保本并逐档上移（−5+10×⌊(MFE−20)/10⌋）；"
                         "判定=T+12h h389 判定脚本；回滚=--rollback；"
                         + ("【guards_bypassed：--force】" if guards else "")
                         + " 代码=新逻辑，部署后重启 worker"),
            })
            meta["h389_trial"] = {
                "started_at": now_iso,
                "bp": new_bp,
                "baseline_params": {"trail_lock_bp": old_bp},
                "baseline_symbols": [str(s) for s in (meta.get("symbols") or []) if str(s)],
                "rollback_to": 0.0,
                "judge_at": (dt.datetime.now(dt.timezone.utc)
                             + dt.timedelta(hours=12)).isoformat(),
                "criteria": "A legs/h≥0.8×基线；B 逐腿 net_bp Welch α=0.10；"
                            "C stop 腿均值应收窄 + 被尾随腿后续 MFE 机会成本",
                "guards_bypassed": bool(guards and a.force),
            }

            cur.execute(
                "UPDATE lane_registry SET meta_json = %s, updated_at = now() WHERE lane_id = %s",
                (json.dumps(meta, ensure_ascii=False, default=str), LANE))
            c.commit()

    print("=" * 88)
    print("h389 试跑 #9 部署完成（尾随锁利）：")
    print(f"  车道          : {LANE}")
    print(f"  trail_lock_bp : {old_bp} → {new_bp}（参数热采用 ≤60s）")
    print(f"  stats_since   : {now_iso}（新统计时代）")
    print("  worker 重启   : 尾随逻辑是新代码，正在执行 h218 重启…")
    print("=" * 88)
    import subprocess
    # [h405c] 单变量窗口保护：选币器 --apply 会改宇宙 ⇒ 试跑期间保持禁用（幂等）
    _r0 = subprocess.run(["schtasks", "/Change", "/TN", "DSH_HFT_UNIVERSE_SELECT",
                          "/DISABLE"], capture_output=True, text=True, timeout=60)
    print(f"  选币任务保持禁用（单变量窗口保护）: rc={_r0.returncode}")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "h218_restart_worker.py")],
                       cwd=str(ROOT), text=True)
    print(f"  h218 重启 rc={r.returncode}")
    # 初始 T+12h 判定：部署即排（与 #2 判定任务同构；INCONCLUSIVE 时判定脚本
    # 会自行 +13h 重排 DSH_HFT_H389_JUDGE）。
    _nxt = dt.datetime.now() + dt.timedelta(hours=12)
    _tr = ("wscript.exe //B //Nologo "
           r"D:\001Alpha\Hyper-Alpha-Arena\scripts\run-quiet.vbs "
           r"D:\001Alpha\Hyper-Alpha-Arena\.venv\Scripts\python.exe "
           r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h389_trail_lock_trial.py --judge")
    _r2 = subprocess.run(
        ["schtasks", "/Create", "/TN", "DSH_HFT_H389_JUDGE", "/TR", _tr,
         "/SC", "ONCE", "/ST", _nxt.strftime("%H:%M"),
         "/SD", _nxt.strftime("%Y/%m/%d"), "/F"],
        capture_output=True, text=True, timeout=60)
    print(f"  初始判定任务 @ {_nxt:%Y-%m-%d %H:%M} rc={_r2.returncode}")
    return 0 if (r.returncode == 0 and _r2.returncode == 0) else 4


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
            old_bp = float(params.get("trail_lock_bp") or 0.0)
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            params["trail_lock_bp"] = 0.0
            meta["params"] = params
            pass  # [h476 2026-09-29 禁用] was: meta["stats_since"] = now_iso
            meta = _append_ops(meta, {
                "ts": now_iso, "action": "h389_trail_rollback",
                "field": "params.trail_lock_bp", "from": old_bp, "to": 0.0,
                "note": "试跑 #9 手动回滚：尾随锁利关闭（参数热采用 ≤60s，无需重启）",
            })
            meta["h389_trial"] = {
                **dict(meta.get("h389_trial") or {}),
                "rolled_back_at": now_iso, "verdict": "ROLLBACK",
                "why": "manual_rollback",
            }
            cur.execute(
                "UPDATE lane_registry SET meta_json = %s, updated_at = now() WHERE lane_id = %s",
                (json.dumps(meta, ensure_ascii=False, default=str), LANE))
            c.commit()
    print(f"✓ h389 回滚完成：trail_lock_bp {old_bp} → 0.0（热采用，无需重启）")
    return 0


# ───────────────────────────── 判定 ─────────────────────────────

def _welch(a: list[float], b: list[float]):
    """双侧 Welch t 检验，返回 (t, p, mean_trial, mean_base, n_trial, n_base, delta)。"""
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
    p = 2 * s  # 双侧
    return {"t": t, "p": min(1.0, p), "mean_trial": m1, "mean_base": m2,
            "n_trial": n1, "n_base": n2, "delta": m1 - m2}


def _per_leg_netbp(cur, since_iso: str, until_iso: str, symbols=None):
    _sym_f = " AND symbol = ANY(%s)" if symbols else ""
    _q = ("SELECT net_bp FROM lane_ledger "
          "WHERE lane_id=%s AND ts > %s::timestamptz AND ts <= %s::timestamptz" + _sym_f)
    _p = (LANE, since_iso, until_iso) + ((symbols,) if symbols else ())
    cur.execute(_q, _p)
    return [float(r[0] or 0.0) for r in cur.fetchall()]


def _window_summary(cur, since_iso: str, until_iso: str, hours: float, symbols=None):
    _sym_f = " AND symbol = ANY(%s)" if symbols else ""
    cur.execute(
        "SELECT count(*) AS legs, sum(net_bp)::float8 AS net_bp,"
        " sum(notional)::float8 AS notional"
        " FROM lane_ledger"
        " WHERE lane_id=%s AND ts > %s::timestamptz AND ts <= %s::timestamptz" + _sym_f,
        (LANE, since_iso, until_iso) + ((symbols,) if symbols else ()))
    r = cur.fetchone()
    legs = int(r[0] or 0)
    return {"legs": legs, "net_bp": float(r[1] or 0.0), "notional": float(r[2] or 0.0),
            "legs_per_hour": legs / max(hours, 0.01),
            "net_bp_per_leg": (float(r[1] or 0.0) / legs) if legs else 0.0}


def _exit_path_stats(cur, since_iso: str, until_iso: str, exit_path: str, symbols=None):
    """某 exit_path 的腿数与均值 net_bp（协议 §4C 子口径）。"""
    _sym_f = " AND symbol = ANY(%s)" if symbols else ""
    cur.execute(
        "SELECT count(*), COALESCE(avg(net_bp), 0.0)::float8 FROM lane_ledger"
        " WHERE lane_id=%s AND ts > %s::timestamptz AND ts <= %s::timestamptz"
        " AND meta_json->>'exit_path' = %s" + _sym_f,
        (LANE, since_iso, until_iso, exit_path) + ((symbols,) if symbols else ()))
    r = cur.fetchone()
    return {"legs": int(r[0] or 0), "mean_net_bp": float(r[1] or 0.0)}


def _trail_opportunity(cur, since_iso: str, until_iso: str, symbols=None):
    """被尾随打掉的腿：出场后 +30min 后续 MFE（机会成本，协议 §4C）。

    口径：side='sell' 的平仓腿 = 原多头 ⇒ 机会 = 出场后最高 mid − 出场 mid；
          side='buy' ⇒ 原空头 ⇒ 机会 = 出场 mid − 出场后最低 mid。
    """
    _sym_f = " AND symbol = ANY(%s)" if symbols else ""
    cur.execute(
        "SELECT ts, symbol, meta_json->>'side' AS side, (meta_json->>'mid_px')::float8"
        " FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz"
        " AND ts <= %s::timestamptz AND meta_json->>'exit_path' = 'trail_lock_taker'" + _sym_f,
        (LANE, since_iso, until_iso) + ((symbols,) if symbols else ()))
    legs = cur.fetchall()
    opps = []
    for ts, sym, side, mid_px in legs:
        if not side or not mid_px or mid_px <= 0:
            continue
        cur.execute(
            "SELECT MIN((bid_px+ask_px)/2.0), MAX((bid_px+ask_px)/2.0)"
            " FROM asterdex_book_ticker"
            " WHERE symbol=CONCAT(CAST(:s AS TEXT),'USDT')"
            " AND event_ts_ms >= :t0 AND event_ts_ms <= :t1"
            " AND bid_px>0 AND ask_px>bid_px",
            {"s": sym, "t0": int(ts.timestamp() * 1000),
             "t1": int((ts.timestamp() + OPP_WINDOW_SEC) * 1000)})
        r = cur.fetchone()
        if not r or r[0] is None:
            continue
        if side == "sell":     # 原多头：后续再冲 = 向上
            opp_bp = (float(r[1]) - mid_px) / mid_px * 1e4
        else:                  # 原空头：后续再冲 = 向下
            opp_bp = (mid_px - float(r[0])) / mid_px * 1e4
        opps.append(opp_bp)
    n = len(opps)
    return {
        "legs": n,
        "mean_opp_bp": (sum(opps) / n) if n else 0.0,
        "median_opp_bp": (sorted(opps)[n // 2]) if n else 0.0,
        "max_opp_bp": (max(opps)) if n else 0.0,
        "sum_opp_bp": sum(opps),
        "opps": [round(x, 2) for x in opps[:50]],
    }


def do_judge(a) -> int:
    import psycopg

    _out_path = OUT
    if a.dry_run:
        _out_path = OUT.with_name("h389_trail_verdict_dryrun.json")

    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            row = cur.fetchone()
            if not row:
                print(f"✗ 车道 {LANE} 不存在")
                return 1
            meta = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0] or {})
            trial = dict(meta.get("h389_trial") or {})
            since_iso = trial.get("started_at")
            if not since_iso:
                print("✗ meta.h389_trial.started_at 缺失，无法判定（试跑未部署）")
                return 1
            now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
            hours = (dt.datetime.now(dt.timezone.utc)
                     - dt.datetime.fromisoformat(since_iso)).total_seconds() / 3600.0
            if hours < MIN_JUDGE_HOURS and not a.force_rollback and not a.dry_run:
                print(f"✗ 试跑仅 {hours:.1f}h < {MIN_JUDGE_HOURS:.0f}h，未到判定窗口")
                return 2

            # [h384 修正] 基线币种过滤：延判窗口混入宇宙变化时仍可比。
            _syms = trial.get("baseline_symbols")
            if not _syms:
                _syms = [str(s) for s in (meta.get("symbols") or []) if str(s)]
            trial_legs = _per_leg_netbp(cur, since_iso, now_iso, _syms)
            base_cut = (dt.datetime.fromisoformat(since_iso)
                        - dt.timedelta(hours=BASELINE_HOURS)).isoformat()
            base_legs = _per_leg_netbp(cur, base_cut, since_iso, _syms)
            w = _welch(trial_legs, base_legs)
            tsum = _window_summary(cur, since_iso, now_iso, hours, _syms)
            bsum = _window_summary(cur, base_cut, since_iso, BASELINE_HOURS, _syms)

            # §4C 子口径
            stop_t = _exit_path_stats(cur, since_iso, now_iso, "stop_loss_taker", _syms)
            stop_b = _exit_path_stats(cur, base_cut, since_iso, "stop_loss_taker", _syms)
            trail_t = _exit_path_stats(cur, since_iso, now_iso, "trail_lock_taker", _syms)
            opp = _trail_opportunity(cur, since_iso, now_iso, _syms)

            freq_ok = (tsum["legs_per_hour"] >= FREQ_FLOOR * bsum["legs_per_hour"])
            if a.force_rollback:
                verdict = "ROLLBACK"
                why = "manual_force_rollback"
            elif not freq_ok:
                verdict = "ROLLBACK"
                why = (f"frequency_collapse {tsum['legs_per_hour']:.1f}/h < "
                       f"{FREQ_FLOOR}×{bsum['legs_per_hour']:.1f}/h")
            elif w is None:
                verdict, why = "INCONCLUSIVE", "insufficient_samples"
            elif w["p"] <= ALPHA and w["delta"] > 0:
                verdict, why = "PASS", f"welch_p={w['p']:.3f} delta={w['delta']:+.3f}bp"
            elif w["p"] <= ALPHA and w["delta"] < 0:
                verdict, why = "ROLLBACK", f"welch_p={w['p']:.3f} delta={w['delta']:+.3f}bp"
            else:
                verdict, why = "INCONCLUSIVE", f"welch_p={w['p']:.3f} delta={w['delta']:+.3f}bp"

            entry = {"ts": now_iso, "action": f"h389_trail_judge_{verdict.lower()}",
                     "note": why, "trial": tsum, "baseline": bsum, "welch": w,
                     "stop_loss": {"trial": stop_t, "baseline": stop_b},
                     "trail_lock": trail_t, "opportunity_cost": opp}
            meta = _append_ops(meta, entry)

            if verdict == "ROLLBACK":
                params = dict(meta.get("params") or {})
                old = float(params.get("trail_lock_bp") or 0.0)
                params["trail_lock_bp"] = 0.0
                meta["params"] = params
                pass  # [h476 2026-09-29 禁用] was: meta["stats_since"] = now_iso
                meta["h389_trial"] = {**trial, "verdict": verdict,
                                      "rolled_back_at": now_iso, "why": why,
                                      "baseline_symbols": _syms}
                meta = _append_ops(meta, {
                    "ts": now_iso, "action": "h389_trail_rollback",
                    "field": "params.trail_lock_bp", "from": old, "to": 0.0,
                    "note": f"12h 判定：{why}"})
            else:
                meta["h389_trial"] = {**trial, "verdict": verdict,
                                      "judged_at": now_iso, "why": why,
                                      "baseline_symbols": _syms,
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
                      f"trail_lock_bp 将{'置 0' if verdict == 'ROLLBACK' else '保持不变'}")

    res = {"verdict": verdict, "why": why, "judged_at": now_iso, "hours": hours,
           "trial": tsum, "baseline": bsum, "welch": w, "freq_ok": freq_ok,
           "stop_loss": {"trial": stop_t, "baseline": stop_b},
           "trail_lock": trail_t, "opportunity_cost": opp,
           "dry_run": bool(a.dry_run)}
    _out_path.parent.mkdir(parents=True, exist_ok=True)
    _out_path.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    print(f"判定已写 {_out_path}")
    if verdict == "ROLLBACK" and not a.dry_run:
        print("已回滚：trail_lock_bp=0（meta 热采用 ≤60s，无需重启）")

    if verdict == "INCONCLUSIVE" and not a.dry_run:
        import subprocess
        _nxt = dt.datetime.now() + dt.timedelta(hours=EXTEND_HOURS)
        _tr = ("wscript.exe //B //Nologo "
               r"D:\001Alpha\Hyper-Alpha-Arena\scripts\run-quiet.vbs "
               r"D:\001Alpha\Hyper-Alpha-Arena\.venv\Scripts\python.exe "
               r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h389_trail_lock_trial.py --judge")
        _r = subprocess.run(
            ["schtasks", "/Create", "/TN", "DSH_HFT_H389_JUDGE", "/TR", _tr,
             "/SC", "ONCE", "/ST", _nxt.strftime("%H:%M"),
             "/SD", _nxt.strftime("%Y/%m/%d"), "/F"],
            capture_output=True, text=True, timeout=60)
        print(f"INCONCLUSIVE ⇒ 已排 +{EXTEND_HOURS:.0f}h 再判定 @ "
              f"{_nxt:%Y-%m-%d %H:%M} rc={_r.returncode}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="H389 尾随锁利试跑：部署/回滚/判定")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--deploy", action="store_true")
    g.add_argument("--rollback", action="store_true")
    g.add_argument("--judge", action="store_true")
    ap.add_argument("--bp", type=float, default=DEFAULT_BP)
    ap.add_argument("--force", action="store_true",
                    help="部署：绕过基线裁决守卫（trial meta 记 guards_bypassed）")
    ap.add_argument("--force-rollback", action="store_true", help="判定：强制回滚")
    ap.add_argument("--dry-run", action="store_true",
                    help="判定：完整路径但不写库、不排定时器（预演）")
    a = ap.parse_args()

    if a.deploy:
        return do_deploy(a)
    if a.rollback:
        return do_rollback()
    return do_judge(a)


if __name__ == "__main__":
    raise SystemExit(main())
