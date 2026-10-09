# -*- coding: utf-8 -*-
"""H428 出场架构修复链队列推进器（用户 2026-09-28 "全面修复"委任）。

链（严格串行，每步 12h 可证伪试跑，判定不达标自动回滚）：
  T1 h392 止损宽限归零（stop_maker_grace_sec 30→0，h391 预注册期望 +146bp/4.7h）
  T5 h429 急动冷却激活（sudden_move_cooldown_sec 0→90；深挖②入场侧）
  T2 h425 跳变速退激活（jump_exit_bp 0→12，F348；快行情止损的预防主力）
  T3 h426 波动条件止损（stop_loss_vol_min 0→1.0，F231）
  T4 h427 薄盘加速出库（max_one_side_seconds 90→45）
  （T6 微价闸 h399 已撤出：深挖③否证——止损入场时刻盘口 skew 与全体无差异 t=−0.1，
    逆向信息不在静态盘口里；保留为可选研究项，不占链位）

推进规则（fail-safe）：
  1. h411_trial.verdict ∈ {PASS, ROLLBACK}（#19 已终判，基线稳定）才可部署任一步；
  2. 依序扫描：PASS ⇒ 前进；ROLLBACK ⇒ 链停（人工复核）；进行中/INCONCLUSIVE ⇒ 等待；
  3. 每轮只部署一步（单变量纪律）；h392 因旧守卫（h356/h354 要求）以 --force 部署，
     委任与 guards_bypassed 记审计；
  4. 部署后由各试跑脚本自排 T+12h 判定任务；本推进器每日 01:45 自续脉冲。

用法: python scripts/h428_queue_runner.py [--dry-run]
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
QUEUE_JSON = ROOT / "research_l1" / "out" / "h428_repair_queue.json"
TASK_NAME = "DSH_HFT_H428_QUEUE"

QUEUE = [
    {"key": "h392", "meta_key": "h392_trial",
     "script": "h392_grace_zero_trial.py",
     "args": ["--deploy", "--force"],
     "title": "T1 止损宽限归零（30→0）",
     "note": "--force：绕过 h392 旧守卫（h356/h354 未终判）——用户全面修复委任"},
    {"key": "h429", "meta_key": "h429_trial",
     "script": "h425_repair_trial.py",
     "args": ["--trial", "h429", "--deploy"],
     "title": "T5 急动冷却激活（sudden_move_cooldown_sec 0→90）",
     "note": "深挖②：单笔大额入场即逆奔=暴动后旧挂单被按旧价吃掉；"
             "cooldown=0 只停当拍，90s 冷却阻断暴动中的新入场"},
    {"key": "h425", "meta_key": "h425_trial",
     "script": "h425_repair_trial.py",
     "args": ["--trial", "h425", "--deploy"],
     "title": "T2 跳变速退激活（jump_exit_bp 0→12）", "note": ""},
    {"key": "h426", "meta_key": "h426_trial",
     "script": "h425_repair_trial.py",
     "args": ["--trial", "h426", "--deploy"],
     "title": "T3 波动条件止损（stop_loss_vol_min 0→1.0）", "note": ""},
    {"key": "h427", "meta_key": "h427_trial",
     "script": "h425_repair_trial.py",
     "args": ["--trial", "h427", "--deploy"],
     "title": "T4 薄盘加速出库（max_one_side_seconds 90→45）", "note": ""},
]


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
    _fp = ROOT / "logs" / "h428_queue_runner.log"
    _fp.parent.mkdir(parents=True, exist_ok=True)
    with open(_fp, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _reschedule_self() -> None:
    """自续脉冲：下次 = 下一日的 01:45（覆盖式重注册）。"""
    now = dt.datetime.now()
    nxt = now.replace(hour=1, minute=45, second=0, microsecond=0)
    if nxt <= now:
        nxt += dt.timedelta(days=1)
    _tr = ("wscript.exe //B //Nologo "
           r"D:\001Alpha\Hyper-Alpha-Arena\scripts\run-quiet.vbs "
           r"D:\001Alpha\Hyper-Alpha-Arena\.venv\Scripts\python.exe "
           r"D:\001Alpha\Hyper-Alpha-Arena\scripts\h428_queue_runner.py")
    _r = subprocess.run(
        ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", _tr,
         "/SC", "ONCE", "/ST", nxt.strftime("%H:%M"), "/SD", nxt.strftime("%Y/%m/%d"),
         "/F"], capture_output=True, text=True, timeout=60)
    _log(f"自续脉冲已排 @ {nxt:%Y-%m-%d %H:%M} rc={_r.returncode}")


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

    _log("修复链扫描开始")
    h411v = (meta.get("h411_trial") or {}).get("verdict")
    if h411v not in ("PASS", "ROLLBACK"):
        _log(f"等待：h411（#19 硬上限）未终判（verdict={h411v!r}），不部署任何修复步")
        _reschedule_self()
        return 0

    states = []
    for item in QUEUE:
        t = dict(meta.get(item["meta_key"]) or {})
        item["state"] = t
        states.append(item)
    QUEUE_JSON.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_JSON.write_text(json.dumps({
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "h411_verdict": h411v,
        "chain": [{"key": it["key"], "title": it["title"],
                   "verdict": it["state"].get("verdict"),
                   "started_at": it["state"].get("started_at"),
                   "judge_at": it["state"].get("judge_at")} for it in states],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    for item in states:
        t, key = item["state"], item["key"]
        v = t.get("verdict")
        if v == "PASS":
            _log(f"{item['title']}：已 PASS，前进")
            continue
        if v == "ROLLBACK":
            _log(f"✗ {item['title']}：已回滚——链停，人工复核后再续（不自动跳过）")
            _reschedule_self()
            return 0
        if t.get("started_at") and v in (None, "INCONCLUSIVE"):
            _log(f"{item['title']}：进行中/延长中（judge_at={t.get('judge_at')}），等待")
            _reschedule_self()
            return 0
        # 未部署 ⇒ 本步可部署（h411 已终判 + 依序串行已保证无同行试跑）
        _log(f"⇒ 部署 {item['title']}（{item['note'] or '守卫内直接部署'}）")
        if a.dry_run:
            _log(f"[dry-run] 将执行: {item['script']} {' '.join(item['args'])}")
            _reschedule_self()
            return 0
        _logf = ROOT / "logs" / f"{key}_deploy.log"
        with open(_logf, "a", encoding="utf-8") as f:
            r = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / item["script"]), *item["args"]],
                cwd=str(ROOT), stdout=f, stderr=f)
        _log(f"{key} 部署 rc={r.returncode}（详见 logs/{key}_deploy.log）")
        if r.returncode != 0:
            _log("✗ 部署失败/被守卫拒绝——链停待人工核对（不自动重试）")
            _reschedule_self()
            return r.returncode
        _log(f"✓ {item['title']} 已部署，T+12h 判定由该试跑脚本自排")
        _reschedule_self()
        return 0

    _log("修复链全部终判完毕（无待部署步）")
    _reschedule_self()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
