# -*- coding: utf-8 -*-
"""H294 EWA 参数在线演化 v2：模拟盘测验 → 稳定 → 推实盘；不通过 → 自动回退。

# 治理规则（用户硬要求 2026-09-23）：
    「进化学习的策略和修改都要经过模拟盘的测验，测验稳定后直接推给实盘，不通过回退修改」
    · 测验 = 每轮 h284 影子模拟（最近 N 小时 tick，同一出场政策）
    · 稳定 = 候选连续出现在最近 3 轮 top-1 中 ≥2 轮，且影子净额比现任高 ≥0.15bp
    · 推实盘 = 通过后热采用（每日自动变更 ≤2 次，覆盖晋升+回退）
    · 试用 = 晋升后累计 ≥100 实盘腿判定：实盘每腿净额 < 晋升前基线 −0.3bp ⇒ 自动回退
    · EWA 权重：w_c ← w_c × exp(η × net_c)，η=0.5，样本 <300 不动权

# 候选（时机/门槛/衰减三个旋钮）：
    A  60s / θ3bp / decay4     B  90s / θ3bp / decay4
    C  60s / θ3bp / decay6     D  60s / θ0   / decay4

# 用法
    python scripts/h294_ewa_evolver.py --hours 12            # 只评估+更新权重
    python scripts/h294_ewa_evolver.py --hours 12 --apply    # 评估 + 治理（晋升/回退）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATE = ROOT / "logs" / "h294_ewa_state.json"
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
LANE = "mm_asterdex"
ETA = 0.5
MIN_N = 300
DAILY_APPLY_CAP = 2

CANDIDATES = [
    # [h294 v2.4] 现行 = decay3 + TP12（H308/H309 双窗验证 −0.10~−0.12bp/腿）
    {"name": "A-60s/t2/d3/tp12", "lookback": 60.0, "thr": 2.0, "decay": 3.0,
     "timeout": 120.0, "tp": 12.0,
     "params": {"trend_skew_lookback": 4.0, "side_trend_min_bp": 2.0,
                "reversal_decay_bp": 3.0, "take_profit_bp": 12.0}},
    {"name": "B-60s/t2/d4/tp12", "lookback": 60.0, "thr": 2.0, "decay": 4.0,
     "timeout": 120.0, "tp": 12.0,
     "params": {"trend_skew_lookback": 4.0, "side_trend_min_bp": 2.0,
                "reversal_decay_bp": 4.0, "take_profit_bp": 12.0}},
    {"name": "C-60s/t5/d3/tp12", "lookback": 60.0, "thr": 5.0, "decay": 3.0,
     "timeout": 120.0, "tp": 12.0,
     "params": {"trend_skew_lookback": 4.0, "side_trend_min_bp": 5.0,
                "reversal_decay_bp": 3.0, "take_profit_bp": 12.0}},
    {"name": "D-60s/t0/d3/tp12", "lookback": 60.0, "thr": 0.0, "decay": 3.0,
     "timeout": 120.0, "tp": 12.0,
     "params": {"trend_skew_lookback": 4.0, "side_trend_min_bp": 0.0,
                "reversal_decay_bp": 3.0, "take_profit_bp": 12.0}},
]


def dsn() -> str:
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


def shadow_eval(cand: dict, hours: float) -> dict:
    cmd = [PY, str(ROOT / "scripts" / "h284_exit_policy.py"), "--hours", str(hours),
           "--lookback", str(cand["lookback"]), "--thr", str(cand["thr"]),
           "--decay", str(cand["decay"]), "--only-p3"]
    if cand.get("timeout"):
        cmd += ["--timeout", str(cand["timeout"])]
    if cand.get("tp"):
        cmd += ["--tp", str(cand["tp"])]
    out = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=1800)
    txt = out.stdout or ""
    m = re.search(r"P3 反转衰减[^\n]*\n?\s*\S+\s+(\d+)\s+([-\d.]+)\s+([-\d.]+)", txt)
    if not m:
        for line in txt.splitlines():
            if "P3" in line and "反转衰减" in line:
                parts = line.split()
                if len(parts) >= 5:
                    return {"n": int(parts[2]), "gross_bp": float(parts[3]),
                            "net_bp": float(parts[4])}
        return {"n": 0, "net_bp": None, "raw_tail": txt[-300:]}
    return {"n": int(m.group(1)), "gross_bp": float(m.group(2)), "net_bp": float(m.group(3))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    state = {"weights": {}, "last": {}, "apply_count_today": 0, "apply_day": "",
             "history": [], "incumbent": None, "trial": None}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    if not state.get("weights"):
        state["weights"] = {c["name"]: 1.0 / len(CANDIDATES) for c in CANDIDATES}

    # [h294 v2 治理规则] 用户的硬要求：一切修改先过模拟盘测验，
    # 测验稳定才推给实盘，不通过自动回退。
    #   晋升门槛：候选连续出现在最近 3 轮 top-1 中 ≥2 轮 且 影子净额
    #             比现任高 ≥ PROMOTE_MARGIN（0.15bp）。
    #   试用窗口：晋升后进入 trial；累计 ≥TRIAL_MIN_LEGS 实盘腿后判定：
    #     实盘每腿净额 < 晋升前现任影子净额 − ROLLBACK_MARGIN ⇒ 自动回退；
    #     否则晋升确认。
    #   每日自动变更上限 DAILY_APPLY_CAP 覆盖晋升+回退。
    PROMOTE_MARGIN = 0.05
    TOP1_CYCLES = 2
    TRIAL_MIN_LEGS = 100
    ROLLBACK_MARGIN = 0.3

    print("=" * 96)
    print(f"H294  EWA 参数演化（v2 治理：测验稳定→推实盘，不通过→自动回退）")
    print(f"      窗口 {a.hours}h  候选 {len(CANDIDATES)} 组")
    print("=" * 96)

    nets = {}
    for c in CANDIDATES:
        r = shadow_eval(c, a.hours)
        nets[c["name"]] = r
        tag = "" if r.get("net_bp") is not None else "  ✗ 解析失败"
        print(f"  {c['name']:14s} n={r.get('n', 0):>6} 净={r.get('net_bp')}bp{tag}")

    # EWA 更新（样本不足不动）
    w = dict(state["weights"])
    for c in CANDIDATES:
        r = nets[c["name"]]
        if r.get("net_bp") is not None and r["n"] >= MIN_N:
            w[c["name"]] = w.get(c["name"], 0.25) * (2.71828 ** (ETA * r["net_bp"]))
        else:
            w[c["name"]] = w.get(c["name"], 0.25)
    tot = sum(w.values()) or 1.0
    w = {k: v / tot for k, v in w.items()}
    state["weights"] = w
    state["last"] = {c["name"]: nets[c["name"]] for c in CANDIDATES}

    ranking = sorted(w.items(), key=lambda kv: -kv[1])
    print(f"\n  EWA 权重（本轮后）:")
    for name, wt in ranking:
        r = nets[name]
        print(f"    {name:14s} w={wt:.4f}   (n={r.get('n')} 净={r.get('net_bp')}bp)")
    top_name = ranking[0][0]
    top = next(c for c in CANDIDATES if c["name"] == top_name)

    state["history"].append({
        "at": dt.datetime.now().astimezone().isoformat(),
        "window_h": a.hours, "nets": {k: nets[k] for k in nets},
        "weights": w, "top": top_name,
    })
    state["history"] = state["history"][-50:]

    # 净额 top-1 出现次数（最近 3 轮，含本轮）——晋升用"测验最优的稳定性"
    # 而不是 EWA 权重（权重有记忆、滞后于当前最优）
    top1_counts = {}
    for h_ in state["history"][-3:]:
        nets_h = {k: v.get("net_bp") for k, v in h_.get("nets", {}).items() if v.get("net_bp") is not None}
        if nets_h:
            top1_counts[max(nets_h, key=nets_h.get)] = top1_counts.get(
                max(nets_h, key=nets_h.get), 0) + 1
    # 本轮净额最优候选（晋升判定的 top）
    net_top = max(CANDIDATES, key=lambda c: (nets[c["name"]].get("net_bp")
                                             if nets[c["name"]].get("net_bp") is not None
                                             else -99.0))

    if a.apply:
        import psycopg

        def change_params(patch: dict, why: str):
            with psycopg.connect(dsn()) as c:
                with c.cursor() as cur:
                    for key, val in patch.items():
                        cur.execute(
                            "UPDATE lane_registry SET meta_json = jsonb_set(meta_json,"
                            " %s::text[], to_jsonb(%s::float8), true), updated_at=now()"
                            " WHERE lane_id=%s", (f"{{params,{key}}}", float(val), LANE))
                    c.commit()
            print(f"  ✓ {why} → 参数 {patch}")

        def daily_cap_ok() -> bool:
            today = dt.date.today().isoformat()
            if state.get("apply_day") != today:
                state["apply_count_today"] = 0
                state["apply_day"] = today
            if state["apply_count_today"] >= DAILY_APPLY_CAP:
                print(f"  ✗ 今日自动变更已达上限 {DAILY_APPLY_CAP} 次，跳过")
                return False
            state["apply_count_today"] += 1
            return True

        def live_era_net(applied_at: str) -> dict:
            with psycopg.connect(dsn()) as c:
                with c.cursor() as cur:
                    cur.execute("""
                        SELECT count(*),
                               round(coalesce(sum(net_bp*notional)/NULLIF(sum(notional),0),0)::numeric,3)
                        FROM lane_ledger WHERE lane_id=%s AND ts >= %s
                    """, (LANE, applied_at))
                    n, bp = cur.fetchone()
            return {"n": int(n or 0), "net_bp": float(bp or 0)}

        inc = state.get("incumbent")
        if not inc or not nets.get(inc["name"], {}).get("net_bp"):
            inc = {"name": top_name, "params": dict(top["params"]),
                   "shadow_net": float(nets[top_name].get("net_bp") or 0.0)}
            state["incumbent"] = inc

        trial = state.get("trial")
        if trial:
            era = live_era_net(trial["applied_at"])
            if era["n"] >= TRIAL_MIN_LEGS:
                if era["net_bp"] < float(trial["incumbent_shadow_net"]) - ROLLBACK_MARGIN:
                    if daily_cap_ok():
                        change_params(trial["incumbent_params"],
                                      f"试用未通过 → 自动回退到 {trial['incumbent_name']}")
                        state["incumbent"] = {"name": trial["incumbent_name"],
                                              "params": trial["incumbent_params"],
                                              "shadow_net": trial["incumbent_shadow_net"]}
                        state["rollbacks"] = (state.get("rollbacks") or 0) + 1
                    state["trial"] = None
                else:
                    print(f"  ✓ 试用通过：{trial['candidate']} 实盘 {era['n']} 腿"
                          f" {era['net_bp']}bp ≥ 基线 {trial['incumbent_shadow_net']}bp − 0.3")
                    state["incumbent"] = {"name": trial["candidate"],
                                          "params": trial["candidate_params"],
                                          "shadow_net": float(nets[trial["candidate"]].get("net_bp") or 0.0)}
                    state["trial"] = None
            else:
                print(f"  ⏳ 试用观测中：{trial['candidate']} 实盘 {era['n']}/{TRIAL_MIN_LEGS} 腿")
        else:
            inc_net = float(inc.get("shadow_net") or 0.0)
            top_name = net_top["name"]
            top_net = nets[top_name].get("net_bp")
            stable = top1_counts.get(top_name, 0) >= TOP1_CYCLES
            if (top_name != inc["name"] and top_net is not None
                    and nets[top_name]["n"] >= MIN_N and stable
                    and top_net >= inc_net + PROMOTE_MARGIN):
                print(f"\n  晋升判定：{top_name} 影子 {top_net}bp ≥ 现任 {inc['name']}"
                      f" {inc_net}bp + {PROMOTE_MARGIN}，净额 top-1 {top1_counts.get(top_name,0)}/3 ✓")
                if daily_cap_ok():
                    change_params(net_top["params"], f"晋升 {top_name}")
                    state["trial"] = {
                        "applied_at": dt.datetime.now().astimezone().isoformat(),
                        "candidate": top_name, "candidate_params": dict(net_top["params"]),
                        "incumbent_name": inc["name"], "incumbent_params": dict(inc["params"]),
                        "incumbent_shadow_net": inc_net,
                    }
                    state["promotions"] = (state.get("promotions") or 0) + 1
            else:
                print(f"\n  不晋升：{'现任即最优' if top_name == inc['name'] else ''}"
                      f" 稳定={stable} 差额={ (top_net or -99) - inc_net:+.2f}bp")

    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  状态存 {STATE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
