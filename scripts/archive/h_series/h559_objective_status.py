"""h559：**目标状态一览**——三件事 + 车道 + 守卫 + 排期，一条命令看完。

为什么需要：三件事已进入"等墙钟"阶段（① 完成；② 判定 21:48；③ 链 21:55），
每轮都手查 alarm/任务/试跑/心跳既慢又容易漏项。本脚本把它们合成一份可复核快照，
并对**异常**给出明确标记（腿速 <60/h、数据滞后、代理不通、守卫心跳停、任务被停等）。

用法：python scripts/h559_objective_status.py [--json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ITEMS = {
    "① reversal_decay 反事实": "完成：168h/n=86 双尺子复核 ⇒ 价格 Δ≈−0.2bp、尾部保护 11.6% ⇒ **保留**",
    "② max_one_side_seconds 90": None,
    "③ ofi_confirm_threshold 0.9": None,
}


def sh(args: list, timeout: int = 60) -> str:
    try:
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return f"(执行失败: {str(e)[:60]})"


def task_field(name: str, field: str) -> str:
    out = sh(["schtasks", "/query", "/tn", name, "/fo", "LIST", "/v"])
    for line in out.splitlines():
        if line.strip().startswith(field + ":"):
            return line.split(":", 1)[1].strip()
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    snap: dict = {"at": dt.datetime.now().isoformat(timespec="seconds")}
    alerts: list[str] = []

    # ── 1) 车道 ──
    try:
        from scripts.h536_lane_alarm import proxy_data_path_ok  # type: ignore
        prox = []
        for h in ("fapi.asterdex.com", "fstream.asterdex.com"):
            ok, msg = proxy_data_path_ok(host=h, port=443)
            prox.append({"host": h, "ok": ok, "detail": msg})
        snap["proxy"] = prox
        if not any(x["ok"] for x in prox):
            alerts.append("代理到交易所两个主机都不通 ⇒ 跑 h542 --apply 换节点")
    except Exception as e:  # noqa: BLE001
        snap["proxy"] = [{"host": "?", "ok": None, "detail": str(e)[:80]}]
    import psycopg
    from scripts.h425_repair_trial import LANE, read_env_dsn, SPECS
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT count(*) FILTER (WHERE ts > now() - interval '60 minutes'),"
                        " count(*) FILTER (WHERE ts > now() - interval '15 minutes'),"
                        " max(ts) FROM lane_ledger WHERE lane_id=%s", (LANE,))
            n60, n15, last_leg = cur.fetchone()
            snap["legs_60m"] = int(n60 or 0)
            snap["legs_15m"] = int(n15 or 0)
            snap["last_leg"] = str(last_leg)
            if (n60 or 0) < 60:
                alerts.append(f"近 60 分钟仅 {n60} 腿（< 60/h 硬约束）")
            if (n15 or 0) == 0:
                alerts.append("近 15 分钟 0 腿 ⇒ 可能又停摆")
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            meta = cur.fetchone()[0] or {}
    params = meta.get("params") or {}
    snap["params"] = {k: params.get(k) for k in
                      ("max_one_side_seconds", "ofi_confirm_threshold",
                       "ofi_flatten_maker_only", "trend_only_q", "stop_loss_bp")}
    tj = dict(meta.get("h463_trial") or {})
    snap["h463_trial"] = {"verdict": tj.get("verdict"), "judge_at": tj.get("judge_at"),
                          "started_at": tj.get("started_at")}
    # [R46] ② 窗口的**可交易覆盖率**预测：覆盖率 <50% 时判定会走安全阀（不判死但无结论），
    # 提前看到就能补救（例如重开窗口）。这是"这条判定到时候能不能成立"的直接预判。
    try:
        from scripts.h425_repair_trial import _covered_hours  # type: ignore
        sa = tj.get("started_at")
        if sa:
            syms = [str(s) for s in (meta.get("symbols") or [])]
            cov, ratio = _covered_hours(str(sa), dt.datetime.now(dt.timezone.utc).isoformat(),
                                        syms)
            with psycopg.connect(read_env_dsn(), autocommit=True) as c3:
                with c3.cursor() as cur3:
                    cur3.execute("SELECT count(*) FROM lane_ledger WHERE lane_id=%s "
                                 "AND ts > %s::timestamptz", (LANE, sa))
                    legs_win = int(cur3.fetchone()[0] or 0)
            snap["h463_window"] = {"covered_h": (None if cov is None else round(cov, 3)),
                                   "coverage": (None if ratio is None else round(ratio, 4)),
                                   "legs": legs_win,
                                   "rate_covered": (None if not cov else round(legs_win / cov, 1))}
            if ratio is not None and ratio < 0.5:
                alerts.append(f"② 窗口覆盖率仅 {ratio:.0%}（<50%）⇒ 21:48 会走 "
                              f"insufficient_covered_time（不出结论）⇒ 考虑重开窗口")
    except Exception as e:  # noqa: BLE001
        snap["h463_window"] = {"error": str(e)[:80]}
    tj4 = dict(meta.get("h464_trial") or {})
    snap["h464_trial"] = {"verdict": tj4.get("verdict"), "judge_at": tj4.get("judge_at")}
    # 市场数据新鲜度
    try:
        mk = read_env_dsn().replace("/alpha_arena", "/alpha_market")
        with psycopg.connect(mk, autocommit=True) as c2:
            with c2.cursor() as cur2:
                cur2.execute("SELECT extract(epoch from now()) - max(timestamp)/1000.0 "
                             "FROM market_trades_aggregated")
                lag = float(cur2.fetchone()[0] or 0.0)
        snap["market_lag_s"] = round(lag, 1)
        if lag > 300:
            alerts.append(f"行情数据滞后 {lag/60:.1f} 分钟 ⇒ 采集中断")
    except Exception as e:  # noqa: BLE001
        snap["market_lag_s"] = None
        alerts.append(f"读行情库失败：{str(e)[:60]}")

    # ── 2) 三件事 ──
    ITEMS["② max_one_side_seconds 90"] = (
        f"已部署 {params.get('max_one_side_seconds')} + 运行态验证 ✓；判定 "
        f"{(tj.get('judge_at') or '?')[:16]}（UTC）")
    ITEMS["③ ofi_confirm_threshold 0.9"] = (
        f"SPEC 就绪（当前注册表 {params.get('ofi_confirm_threshold')}）；链 "
        f"{(task_field('DSH_HFT_H464_CHAIN', 'Next Run Time') or '?')} 部署（② 终局才推进）")
    snap["items"] = ITEMS

    # ── 3) 守卫与任务 ──
    guards = {}
    for t in ("DSH_HFT_LANE_ALARM", "DSH_HFT_AUTO_SWITCH", "DSH_MM_WORKER",
              "DSH_HFT_H463_JUDGE", "DSH_HFT_H464_CHAIN"):
        guards[t] = {"status": task_field(t, "Status"),
                     "next": task_field(t, "Next Run Time"),
                     "last_result": task_field(t, "Last Result")}
        if guards[t]["status"] not in ("Ready", "Running"):
            alerts.append(f"任务 {t} 状态={guards[t]['status']}（应为 Ready）")
    snap["tasks"] = guards
    st = ROOT / "research_l1" / "out" / "h556_state.json"
    if st.exists():
        try:
            d = json.loads(st.read_text(encoding="utf-8"))
            age = (dt.datetime.now(dt.timezone.utc)
                   - dt.datetime.fromisoformat(d["last_check_ts"])).total_seconds()
            snap["auto_switch"] = {"heartbeat_age_s": round(age), "consec_fail":
                                   d.get("consec_fail"), "switches_6h": len(d.get("switches") or [])}
            if age > 1800:
                alerts.append(f"自动换节点守卫心跳停 {age/60:.0f} 分钟 ⇒ 可能任务被停")
        except Exception as e:  # noqa: BLE001
            alerts.append(f"读守卫状态失败：{str(e)[:60]}")
    snap["alerts"] = alerts

    if a.json:
        print(json.dumps(snap, ensure_ascii=False, indent=2, default=str))
        return 0
    print("=" * 90)
    print(f"目标状态一览 · {snap['at']}")
    print("=" * 90)
    print("\n【三件事】")
    for k, v in ITEMS.items():
        print(f"  {k}\n      {v}")
    print("\n【车道】")
    print(f"  腿速：近 60 分钟 {snap['legs_60m']} 腿 / 近 15 分钟 {snap['legs_15m']} 腿"
          f"（硬约束 ≥60/h {'✓' if snap['legs_60m'] >= 60 else '✗'}）")
    print(f"  最近一条腿：{snap['last_leg']}")
    print(f"  行情数据滞后：{snap['market_lag_s']}s")
    print(f"  代理：{'、'.join(('✓' if x['ok'] else '✗') + x['host'] for x in snap['proxy'])}")
    print(f"  关键参数：{snap['params']}")
    if snap.get("h463_window") and "rate_covered" in snap["h463_window"]:
        w = snap["h463_window"]
        print(f"  ② 窗口：可交易 {w['covered_h']}h（覆盖 {w['coverage']:.0%}）"
              f"、{w['legs']} 腿 ⇒ **可交易口径 {w['rate_covered']}/h**"
              f"（21:48 判定能否成立 {'✓' if (w['coverage'] or 0) >= 0.5 else '⚠️ 覆盖率不足'}）")
    print("\n【守卫与任务】")
    for t, g in guards.items():
        print(f"  {t:<24} {g['status']:<9} next={g['next']:<22} last={g['last_result']}")
    if "auto_switch" in snap:
        s = snap["auto_switch"]
        print(f"  自动换节点心跳：{s['heartbeat_age_s']}s 前，连续失败 {s['consec_fail']}，"
              f"近 6h 换过 {s['switches_6h']} 次")
    print("\n" + "=" * 90)
    if alerts:
        print("⚠️ 异常：")
        for x in alerts:
            print(f"  · {x}")
    else:
        print("✓ 无异常：三件事按计划推进，车道与守卫均正常。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
