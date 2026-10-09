"""h472：把 h441（ofi_flatten_threshold）标为 SUPERSEDED，并注销其自动判定任务。

为什么必须做（否则会**静默毁掉 h472**）：
  · h441 的试跑把 `ofi_flatten_threshold` 由 0 改成 0.5，`rollback_to = 0.0`，
    判定任务排在 09:13L；若它按频率/显著性判死，会把该参数**回滚成 0**；
  · 而 h472 的"被动执行"分支就在 `_fl_th > 0` 这个 if 里面 ⇒ 一旦 `_fl_th=0`，
    h472 的 maker-only 路径**永远不会进入**，改动静默失效；
  · 证据（h471/h470）显示信号本身是对的：ofi_flatten 腿价格项 +4.89bp/腿、
    出场后 300s 离场方向有利漂移 +9.91bp（t=2.15）⇒ **信号保留、只换执行**。

本脚本（幂等）：
  1. 在 lane_meta 锁下把 `h441_trial` 记为 SUPERSEDED（保留原始证据字段）；
  2. 追加一条 ops_changes 审计；
  3. 删除 `DSH_HFT_H441_JUDGE` 计划任务。

用法：python scripts/h472_supersede_h441.py
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402

from scripts.h425_repair_trial import (  # noqa: E402
    LANE, _MetaLock, _append_ops, _load_meta, _save, read_env_dsn)

TASK = "DSH_HFT_H441_JUDGE"
OUT = pathlib.Path(__file__).resolve().parents[1] / "research_l1" / "out" / \
    "h472_supersede_h441.json"


def main() -> int:
    with _MetaLock():
        with psycopg.connect(read_env_dsn()) as c:
            with c.cursor() as cur:
                meta = _load_meta(cur)
                tr = dict(meta.get("h441_trial") or {})
                if not tr:
                    print("✗ meta 里没有 h441_trial")
                    return 1
                now = dt.datetime.now(dt.timezone.utc).isoformat()
                tr.update({
                    "verdict": "SUPERSEDED",
                    "superseded_by": "h472",
                    "superseded_at": now,
                    "why": ("信号保留、执行方式改由 h472 接管（taker→maker-only）。"
                            "证据：h471 该路径付 −3.53bp/腿 taker 费 + −1.19bp/腿穿价差、"
                            "吞掉全部手续费 49%；h470 出场后 300s 离场方向有利漂移 "
                            "+9.91bp(t=2.15) ⇒ 该时点被动减仓本有对手方流量。"
                            "自动判定任务已注销，避免把 _fl_th 回滚成 0 从而静默废掉 h472。"),
                    "original_verdict_fields": {
                        k: tr.get(k) for k in ("from", "to", "criteria", "started_at",
                                               "judge_at", "rollback_to")},
                })
                meta["h441_trial"] = tr
                meta = _append_ops(meta, {
                    "ts": now, "action": "h441_supersede",
                    "field": "meta.h441_trial",
                    "from": {"verdict": tr.get("original_verdict_fields", {}).get("from")},
                    "to": {"verdict": "SUPERSEDED", "superseded_by": "h472"},
                    "note": ("信号保留、执行改由 h472（ofi_flatten_maker_only）接管；"
                             "注销 DSH_HFT_H441_JUDGE 以免自动回滚 _fl_th=0 静默废掉 h472")})
                _save(cur, c, meta)
                print("✓ h441_trial → SUPERSEDED")
    r = subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"],
                       capture_output=True, text=True, timeout=60)
    ok = r.returncode == 0
    print(("✓ 已注销 " if ok else "✗ 注销失败 ") + TASK,
          (r.stdout or "").strip() or (r.stderr or "").strip())
    OUT.write_text(json.dumps({"superseded": "h441", "by": "h472",
                               "task_deleted": ok,
                               "task": TASK}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("已写:", OUT.relative_to(pathlib.Path(__file__).resolve().parents[1]))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
