"""h515：把 h448 标为 SUPERSEDED 并注销其自动判定任务（保护 ③/h464 的基线）。

为什么必须做（时序冲突）：
  · h448 的试跑是 `ofi_confirm_threshold` **0.15 → 0.5**，`rollback_to = 0.15`，
    自动判定排在 **11:17L**；若它判死，参数会回到 **0.15**；
  · 而 ③（h464）预注册的是 **0.5 → 0.9**，部署时刻 **14:35L**（在 11:17 之后）
    ⇒ 若 h448 先回滚，③ 就变成"在 0.15 上部署 0.9"，且 `rollback_to` 会写回 0.5
    ——**基线错位、试跑语义被破坏**，判定基线窗还会混入 0.15/0.5/0.9 三段。

处置（与 h441→h472 同一先例）：h448 记 `SUPERSEDED`（保留原证据字段）、
追加 ops 审计、注销 `DSH_HFT_H448_JUDGE`。
理由：它产出的 0.5 是**当前线上值**，且正被 ③ 继承（0.5→0.9）；
再让它回滚到 0.15 只会先破坏一次基线、随后又被 ③ 覆盖 —— 纯churn。

用法：python scripts/h515_supersede_h448.py
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

TASK = "DSH_HFT_H448_JUDGE"
ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "research_l1" / "out" / "h515_supersede_h448.json"


def main() -> int:
    with _MetaLock():
        with psycopg.connect(read_env_dsn()) as c:
            with c.cursor() as cur:
                meta = _load_meta(cur)
                tr = dict(meta.get("h448_trial") or {})
                if not tr:
                    print("✗ meta 里没有 h448_trial")
                    return 1
                now = dt.datetime.now(dt.timezone.utc).isoformat()
                orig = {k: tr.get(k) for k in ("from", "to", "criteria", "started_at",
                                               "judge_at", "rollback_to")}
                tr.update({
                    "verdict": "SUPERSEDED",
                    "superseded_by": "h464",
                    "superseded_at": now,
                    "why": ("时序冲突：其判定（11:17L）会把 ofi_confirm_threshold 回滚到 0.15，"
                            "而 ③/h464 在 14:35L 预注册的是 0.5→0.9 —— 先回滚会破坏 ③ 的基线"
                            "与 rollback_to。h448 产出的 0.5 是当前线上值且正被 ③ 继承，"
                            "故按 h441→h472 先例标记 SUPERSEDED，不再单独判定。"),
                    "original_verdict_fields": orig,
                })
                meta["h448_trial"] = tr
                meta = _append_ops(meta, {
                    "ts": now, "action": "h448_supersede",
                    "field": "meta.h448_trial",
                    "from": {"rollback_to": 0.15}, "to": {"verdict": "SUPERSEDED",
                                                          "superseded_by": "h464"},
                    "note": ("保护 ③ 的预注册基线（0.5→0.9）；注销 DSH_HFT_H448_JUDGE；"
                             "0.5 为当前线上值，由 ③ 继承")})
                _save(cur, c, meta)
                print("✓ h448_trial → SUPERSEDED（by h464/③）")
    r = subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"],
                       capture_output=True, text=True, timeout=60)
    ok = r.returncode == 0
    print(("✓ 已注销 " if ok else "✗ 注销失败 ") + TASK,
          (r.stdout or "").strip() or (r.stderr or "").strip())
    OUT.write_text(json.dumps({"superseded": "h448", "by": "h464",
                               "task_deleted": ok, "task": TASK},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
