# -*- coding: utf-8 -*-
"""H356 试跑 #2：宇宙扩容（事件研究选币）部署/回滚/判定。

依据（h355b，16 币×168h 事件研究）：DOGE/SUI/NEAR/ARB/ADA/XRP/ENA 的 P1 回调
f120 显著为正且频率高；影子模拟对这些币系统性悲观（h284 填充模型缺陷）⇒ 实盘仲裁。

用法：
  python scripts/h356_universe_trial.py --deploy      # 扩容 + 重置时代（随后重启 worker）
  python scripts/h356_universe_trial.py --rollback    # 恢复部署前宇宙（热采用）
  python scripts/h356_universe_trial.py --judge       # T+12h 判定：分币种去留 + 总量回滚判定

判定规则（预注册）：
  A. 总 legs/h < 0.8×基线 ⇒ 整体回滚；
  B. 总逐腿 net_bp Welch α=0.10 显著转差 ⇒ 整体回滚；
  C. 分币种：腿数 ≥30 且 net_bp<0 ⇒ 摘除；腿数<30 ⇒ 观察（不动）；净 bp>0 ⇒ 保留；
  D. 判定后把幸存宇宙写回 meta（热采用，孤儿持仓自动退出）。
  [h391 2026-09-27 增补] E. 宇宙下限：摘除后至少保留 UNIVERSE_FLOOR 个币（按 net_bp
  排序保最好的）；否则一次判定可能把宇宙摘空 ⇒ 腿速塌陷违反高频使命。
  [h391] F. --dry-run：完整判定路径但不写库、不排定时器、不恢复选币任务（预演）。
  [h405c 2026-09-27 增补] G. 选币任务保持**禁用**（原"恢复"改为"确认禁用"）：
  #2 判决后立即进入连续单变量试跑队列，选币器 --apply 会污染进行中试跑窗口；
  队列清空后手动 /ENABLE。
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
OUT = ROOT / "research_l1" / "out" / "h356_verdict.json"
LANE = "mm_asterdex"
ADD = ["DOGE", "SUI", "NEAR", "ARB", "ADA", "XRP", "ENA"]
ALPHA = 0.10
FREQ_FLOOR = 0.8
MIN_LEGS = 30
UNIVERSE_FLOOR = 5   # [h391] 摘除后最少保留币数（保 net_bp 最好的）


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


def deploy(cur) -> int:
    meta = _load_meta(cur)
    # [h371 2026-09-27] 前置判决守卫：P2 判定（12:38）未落 ⇒ 拒绝部署
    if (meta.get("h354_p2_trial") or {}).get("verdict") is None:
        print("✗ h354 P2 判定未落（h354_p2_trial.verdict 缺失），拒绝部署 #2")
        return 1
    old = [str(s) for s in (meta.get("symbols") or [])]
    new = list(dict.fromkeys(old + [s for s in ADD if s not in old]))
    now = _now_iso()
    meta["symbols"] = new
    pass  # [h476 2026-09-29 禁用] was: meta["stats_since"] = now
    meta = _append_ops(meta, {
        "ts": now, "action": "h356_universe_deploy",
        "from": old, "to": new,
        "note": "事件研究选币扩容（h355b）；回滚=--rollback；判定=T+12h --judge"})
    meta["h356_trial"] = {
        "started_at": now, "baseline_symbols": old, "added": [s for s in new if s not in old],
        "judge_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=12)).isoformat()}
    _save_meta(cur, meta)
    print(f"h356 部署完成：{old} → {new}")
    print("下一步：python scripts/h218_restart_worker.py（回填新币 mid_hist）")
    return 0


def rollback(cur) -> int:
    meta = _load_meta(cur)
    trial = dict(meta.get("h356_trial") or {})
    base = list(trial.get("baseline_symbols") or [])
    if not base:
        print("✗ 无 h356_trial.baseline_symbols，无法回滚")
        return 1
    now = _now_iso()
    old = list(meta.get("symbols") or [])
    meta["symbols"] = base
    pass  # [h476 2026-09-29 禁用] was: meta["stats_since"] = now
    meta = _append_ops(meta, {"ts": now, "action": "h356_universe_rollback",
                              "from": old, "to": base, "note": "试跑 #2 回滚"})
    meta["h356_trial"] = {**trial, "rolled_back_at": now}
    _save_meta(cur, meta)
    print(f"h356 回滚完成：{old} → {base}（热采用 ≤60s，孤儿持仓自动退出）")
    return 0


def _per_symbol(cur, since, until):
    cur.execute("""
        SELECT symbol, count(*), sum(net_bp)::float8 FROM lane_ledger
        WHERE lane_id=%s AND ts > %s::timestamptz AND ts <= %s::timestamptz
        GROUP BY symbol
    """, (LANE, since, until))
    return {r[0]: {"legs": int(r[1] or 0), "net_bp": float(r[2] or 0.0)} for r in cur.fetchall()}


def _legs(cur, since, until):
    cur.execute("SELECT net_bp FROM lane_ledger WHERE lane_id=%s AND ts > %s::timestamptz "
                "AND ts <= %s::timestamptz", (LANE, since, until))
    return [float(r[0] or 0.0) for r in cur.fetchall()]


def judge(cur, a) -> int:
    meta = _load_meta(cur)
    trial = dict(meta.get("h356_trial") or {})
    since = trial.get("started_at")
    if not since:
        print("✗ 无 h356_trial.started_at")
        return 1
    now = _now_iso()
    hours = (dt.datetime.now(dt.timezone.utc)
             - dt.datetime.fromisoformat(since)).total_seconds() / 3600.0
    if hours < 10 and not a.dry_run:
        print(f"✗ 试跑仅 {hours:.1f}h < 10h，未到判定窗口")
        return 2
    base_cut = (dt.datetime.fromisoformat(since) - dt.timedelta(hours=12)).isoformat()

    t_legs = _legs(cur, since, now)
    b_legs = _legs(cur, base_cut, since)
    t_sym = _per_symbol(cur, since, now)
    b_sym = _per_symbol(cur, base_cut, since)
    w = _welch(t_legs, b_legs)

    t_h = len(t_legs) / max(hours, 0.01)
    b_h = len(b_legs) / 12.0
    freq_ok = t_h >= FREQ_FLOOR * b_h
    if not freq_ok:
        verdict, why = "ROLLBACK", f"frequency_collapse {t_h:.1f}/h < {FREQ_FLOOR}×{b_h:.1f}/h"
    elif w is None:
        verdict, why = "INCONCLUSIVE", "insufficient_samples"
    elif w["p"] <= ALPHA and w["delta"] < 0:
        verdict, why = "ROLLBACK", f"welch_p={w['p']:.3f} delta={w['delta']:+.3f}bp"
    else:
        verdict, why = ("PASS" if w["p"] <= ALPHA else "INCONCLUSIVE"), \
                       f"welch_p={w['p']:.3f} delta={w['delta']:+.3f}bp"

    # 分币种去留（与总量判定独立：总 PASS 时也摘除负收益币）
    keep, drop, observe = [], [], []
    for s in sorted({**t_sym, **b_sym}):
        d = t_sym.get(s) or {"legs": 0, "net_bp": 0.0}
        if d["legs"] >= MIN_LEGS and d["net_bp"] < 0:
            drop.append((s, d["legs"], d["net_bp"]))
        elif d["legs"] >= MIN_LEGS:
            keep.append((s, d["legs"], d["net_bp"]))
        else:
            observe.append((s, d["legs"], d["net_bp"]))

    # [h391b 2026-09-27] 首次 INCONCLUSIVE ⇒ 延判期**不摘币、不恢复选币任务**：
    # 摘币/选币器都会在 +12h 再判定窗口内改宇宙 ⇒ 混入两种宇宙口径的腿
    # （h384 同源污染）。摘除延到终判（PASS / 第二次起的 INCONCLUSIVE）。
    _jc = int(trial.get("judged_count") or 0)
    apply_drops = (verdict == "PASS") or (_jc >= 1)

    if verdict == "ROLLBACK":
        if a.dry_run:
            print("[dry-run] 若实际执行：整体回滚宇宙到 baseline_symbols")
        else:
            rollback(cur)
            # [h379 2026-09-27] rollback() 重载 meta ⇒ 判决元数据需补写（此前丢失）
            _m = _load_meta(cur)
            _t = dict(_m.get("h356_trial") or {})
            _m["h356_trial"] = {**_t, "verdict": verdict, "why": why,
                                "judged_at": now, "judged_count": _jc + 1}
            _save_meta(cur, _m)
    elif apply_drops:
        cur_syms = [str(s) for s in (meta.get("symbols") or [])]
        # [h391] 宇宙下限：按 net_bp 降序保底 UNIVERSE_FLOOR 个，防止摘空。
        ranked = sorted(drop, key=lambda d: -d[2])
        floor_keep = ranked[:max(0, len(cur_syms) - UNIVERSE_FLOOR)]
        drop_list = [d[0] for d in floor_keep]
        new_syms = [s for s in cur_syms if s not in set(drop_list)]
        if set(new_syms) != set(cur_syms):
            if not a.dry_run:
                _meta2 = _load_meta(cur)
                _meta2["symbols"] = new_syms
                _meta2 = _append_ops(_meta2, {
                    "ts": now, "action": "h356_symbol_drop", "from": cur_syms, "to": new_syms,
                    "note": f"分币种判定摘除 {drop_list}"
                            f"（h391 下限：保底 {UNIVERSE_FLOOR} 币）"})
                _meta2["h356_trial"] = {**trial, "verdict": verdict, "why": why,
                                        "judged_at": now, "dropped": drop_list,
                                        "judged_count": _jc + 1}
                _save_meta(cur, _meta2)
            print(f"分币种摘除：{drop_list} ⇒ 新宇宙 {new_syms}"
                  + ("  [dry-run 未落库]" if a.dry_run else ""))
        else:
            if not a.dry_run:
                _meta2 = _load_meta(cur)
                _meta2["h356_trial"] = {**trial, "verdict": verdict, "why": why,
                                        "judged_at": now, "dropped": [],
                                        "judged_count": _jc + 1}
                _save_meta(cur, _meta2)
            print("分币种无摘除（或全部被下限保护）")
    else:
        # 首次 INCONCLUSIVE：宇宙保持不动，仅记判决与延判
        if not a.dry_run:
            _meta2 = _load_meta(cur)
            _meta2["h356_trial"] = {**trial, "verdict": verdict, "why": why,
                                    "judged_at": now, "dropped": [],
                                    "judged_count": _jc + 1}
            _save_meta(cur, _meta2)
        print(f"[h391b] 首次 INCONCLUSIVE ⇒ 延判期不摘币（{len(drop)} 个候选暂留），"
              f"宇宙保持 {len(meta.get('symbols') or [])} 币；终判时再摘")

    res = {"verdict": verdict, "why": why, "hours": hours, "welch": w,
           "trial_legs_h": round(t_h, 1), "baseline_legs_h": round(b_h, 1),
           "freq_ok": freq_ok, "keep": keep, "drop": drop, "observe": observe,
           "dry_run": bool(a.dry_run)}
    _out = OUT
    if a.dry_run:
        _out = OUT.with_name("h356_verdict_dryrun.json")
    _out.parent.mkdir(parents=True, exist_ok=True)
    _out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))
    print(f"判定已写 {_out}")

    if a.dry_run:
        print("[dry-run] 不落库、不恢复选币任务、不排定时器")
        return 0

    # [h356] 试跑 #2 结束 ⇒ 选币任务的去留：
    # [h405c 2026-09-27 修正] **保持禁用**——#2 判决后立即进入 #17→#18→#16③→
    # #3~#7 的连续单变量试跑队列；选币器 --apply 每 30 分钟可能改宇宙 ⇒ 任何一次
    # 变更都会污染进行中试跑的窗口（h384/h391b 同源教训）。队列全部清空后再手动
    # 恢复（schtasks /Change DSH_HFT_UNIVERSE_SELECT /ENABLE）。此处改为幂等
    # 确认禁用 + 留痕（若上游从未禁过，这里兜底禁掉）。
    import subprocess
    if verdict != "INCONCLUSIVE" or _jc >= 1:
        r = subprocess.run(["schtasks", "/Change", "/TN", "DSH_HFT_UNIVERSE_SELECT",
                            "/DISABLE"], capture_output=True, text=True, timeout=60)
        print(f"[h405c] 选币任务保持禁用（单变量队列窗口保护）: rc={r.returncode} "
              f"{(r.stdout or '').strip()} {(r.stderr or '').strip()}")
    else:
        print("[h391b] 首次 INCONCLUSIVE ⇒ 延判期不恢复选币任务（防口径污染）")

    # [h369b] INCONCLUSIVE 自动延长：+12h 再判定
    if verdict == "INCONCLUSIVE":
        _nxt = dt.datetime.now() + dt.timedelta(hours=12)
        _tr = ("wscript.exe //B //Nologo "
               r"D:\001Alpha\Hyper-Alpha-Arena\scripts\run-quiet.vbs "
               r"D:\001Alpha\Hyper-Alpha-Arena\.venv\Scripts\python.exe "
               r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h356_universe_trial.py --judge")
        _r = subprocess.run(
            ["schtasks", "/Create", "/TN", "DSH_HFT_H356_JUDGE", "/TR", _tr,
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
    ap.add_argument("--dry-run", action="store_true",
                    help="判定：完整路径但不写库、不排定时器、不恢复选币任务（预演）")
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
                rc = judge(cur, a)
            else:
                print("请给 --deploy / --rollback / --judge 之一")
                return 1
            if a.dry_run:
                c.rollback()      # 预演绝不落库（防御：即使误写也回滚）
                return rc
            c.commit()
            return rc


if __name__ == "__main__":
    raise SystemExit(main())
