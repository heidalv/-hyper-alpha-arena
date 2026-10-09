# -*- coding: utf-8 -*-
"""H410 队列推进器：判决（01:00/01:38）后自动执行 **#19 持仓硬上限** 部署
（按 h395 手册 + h405f 规则 + 用户裁决：合规优先，硬上限先于 #17）。

动机：判决链与部署队列全部就绪后，第一个实质动作按用户裁决是 #19（持仓硬上限
300s——"30s~5min 时域"是用户验证过的硬约束，现行 maker-only 超时违反它）。
本脚本把 h395 手册的决策规则程序化，排为 01:45 计划任务（DSH_HFT_H410_QUEUE）。
#17 尾随锁利改在 h411 判定（T+12h）后单独试跑（单变量纪律）。

决策规则（与 h395 §3 + h405f 逐字一致）：
  1. h356_trial.verdict != PASS ⇒ 等待（#2 未判 PASS，不部署）；
  2. P2 verdict ∈ {PASS, ROLLBACK} ⇒ 直接 h411 --deploy（守卫通过即部署）；
  3. P2 verdict == INCONCLUSIVE：
     · P2 效应 Δ（welch.delta，读 h354_p2_verdict.json）∈ [−0.2, +0.2]bp
       ⇒ h411 --deploy --force（guards_bypassed 记审计；h405f 预注册）；
     · Δ 超界 ⇒ 等待（不部署）；
  4. 任何读取失败 ⇒ 等待（fail-safe，绝不盲部署）。

用法: python scripts/h410_queue_runner.py [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LANE = "mm_asterdex"
P2_VERDICT_JSON = ROOT / "research_l1" / "out" / "h354_p2_verdict.json"
H405F_DELTA_BOUND = 0.2


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


def _log(msg: str) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    _fp = ROOT / "logs" / "h410_queue_runner.log"
    _fp.parent.mkdir(parents=True, exist_ok=True)
    with open(_fp, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只判不部署")
    a = ap.parse_args()

    import psycopg
    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json FROM lane_registry WHERE lane_id=%s", (LANE,))
            row = cur.fetchone()
            if not row:
                _log("✗ 车道不存在，退出")
                return 1
            meta = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0] or {})

    h356v = (meta.get("h356_trial") or {}).get("verdict")
    if h356v != "PASS":
        _log(f"等待：#2 判决未 PASS（verdict={h356v!r}），不部署 #19")
        return 0
    # [h405h] 用户裁决提前部署后：01:45 不得重跑（会重置 h411_trial.started_at）
    if (meta.get("h411_trial") or {}).get("started_at"):
        _log("h411 已部署（用户裁决提前上线），跳过（不重置试跑元信息）")
        return 0

    p2v = (meta.get("h354_p2_trial") or {}).get("verdict")
    deploy_args = [str(sys.executable),
                   str(ROOT / "scripts" / "h411_timeout_hard_taker_trial.py"),
                   "--deploy"]
    if p2v in ("PASS", "ROLLBACK"):
        _log(f"P2 已终判（{p2v}）⇒ 尝试 #19 硬上限部署（守卫将终审）")
    elif p2v == "INCONCLUSIVE":
        # h405f 规则：P2 Δ 在 ±0.2bp 内（效应≈零）且其下次判定晚于 #19 窗口结束 ⇒
        # 允许带 --force 部署（guards_bypassed 记审计）。
        delta = None
        try:
            vd = json.loads(P2_VERDICT_JSON.read_text(encoding="utf-8"))
            w = vd.get("welch") or {}
            delta = float(w.get("delta", 0.0))
        except Exception:
            pass
        if delta is None:
            _log("等待：P2 INCONCLUSIVE 且 Δ 读取失败（fail-safe，不部署）")
            return 0
        if abs(delta) <= H405F_DELTA_BOUND:
            _log(f"h405f 规则触发：P2 INCONCLUSIVE 且 Δ={delta:+.3f}bp 在 ±0.2 内"
                 " ⇒ 尝试 #19 部署 --force（guards_bypassed 记审计）")
            deploy_args.append("--force")
        else:
            _log(f"等待：P2 INCONCLUSIVE 且 Δ={delta:+.3f}bp 超出 h405f 界"
                 "（±0.2bp），不部署")
            return 0
    else:
        _log(f"等待：P2 verdict={p2v!r} 未落，不部署")
        return 0

    if a.dry_run:
        _log(f"[dry-run] 将执行: {' '.join(deploy_args[2:])}")
        return 0

    r = subprocess.run(deploy_args, cwd=str(ROOT), text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for line in (r.stdout or "").splitlines():
        _log(f"[h411] {line}")
    _log(f"h411 #19 部署 rc={r.returncode}"
         + ("（硬上限 300s 已生效：存量 >300s 持仓将在下个 tick 被强平；"
            "判定任务已自动排 T+12h）" if r.returncode == 0
            else "（部署被守卫拒绝或失败——见上方输出；#17 不自动推进，等人工核对）"))
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
