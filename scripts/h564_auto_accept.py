"""h564：**自动验收守卫**——判定落地后自动把验收记录写进文档（无人值守）。

为什么需要（R57）：目标的三件事里，最后一步"记录验收"原本**只有我会做**
（`h546 --append` 是手工命令）。但 ② 判定在 21:48、③ 在次日 ⇒ 若那时没有人在跑，
**判决会落地、验收记录却没人写** ⇒ 目标收不了口。
（现实约束：本会话的目标有轮次上限，等待期可能耗尽轮次。）

本守卫每 30 分钟做一次：
  1. 对每个关心的试跑，看 `research_l1/out/<key>_verdict.json` 是否存在；
  2. **排除预演产物**（`dry_run=true` 的绝不写入正式验收 ✗）；
  3. 若该判定的 `judged_at` 还没出现在验收文档的对应块里 ⇒ 调
     `h546 --trial <key> --append`（幂等：块内整体替换）✓
  4. 全程写 `logs/auto_accept.log`；心跳写 `research_l1/out/h564_state.json`。

用法：
  python scripts/h564_auto_accept.py            # 干跑：只报"将写入什么"
  python scripts/h564_auto_accept.py --apply     # 真正写入
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "research_l1" / "out"
# ⚠️ 三个落盘路径都可用环境变量覆盖（**仅供离线测试**，见 h565）——
# R41 的教训：测试钩子必须覆盖**全部**落盘路径，否则测试会污染生产文档/日志。
DOC = pathlib.Path(os.environ.get("H564_DOC_PATH")
                   or (ROOT / "研究结论" / "三件事验收_20260929.md"))
H546 = ROOT / "scripts" / "h546_accept_verdict.py"
LOG = pathlib.Path(os.environ.get("H564_LOG_PATH") or (ROOT / "logs" / "auto_accept.log"))
STATE = pathlib.Path(os.environ.get("H564_STATE_PATH")
                     or (OUT / "h564_state.json"))
VERDICT_DIR = pathlib.Path(os.environ.get("H564_VERDICT_DIR") or OUT)
KEYS = ["h463", "h464", "h527", "h529"]   # 三件事（含 h527）+ [R205] ③′（h529，③ 的忠实实现）


def log(msg: str) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    doc = DOC.read_text(encoding="utf-8") if DOC.exists() else ""
    todo, done, missing = [], [], []
    for key in KEYS:
        vp = VERDICT_DIR / f"{key}_verdict.json"
        if not vp.exists():
            missing.append(key)
            continue
        try:
            v = json.loads(vp.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log(f"⚠️ {key}: 判定产物解析失败（{str(exc)[:50]}）")
            continue
        if v.get("dry_run"):
            missing.append(f"{key}(预演)")
            continue
        jd = str(v.get("judged_at") or "")
        if jd and jd in doc:
            done.append(key)
        else:
            todo.append((key, v.get("verdict"), jd))
    print("=" * 84)
    print(f"自动验收守卫 · {dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"  已写入验收：{done or '（无）'}")
    print(f"  尚无正式判定：{missing or '（无）'}")
    print(f"  **待写入**：{[(k, v, j[:16]) for k, v, j in todo] or '（无）'}")
    st = {"last_check_ts": dt.datetime.now(dt.timezone.utc).isoformat(),
          "done": done, "pending": [k for k, _, _ in todo],
          "missing": [str(x) for x in missing]}
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    if not todo:
        print("  ⇒ 无需动作。")
        return 0
    if not a.apply:
        print("  ⇒ DRY-RUN：加 --apply 才写入（或由计划任务 DSH_HFT_AUTO_ACCEPT 执行）。")
        return 0
    rc_all = 0
    for key, verdict, jd in todo:
        p = subprocess.run([sys.executable, str(H546), "--trial", key, "--append"],
                           cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
        tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-2:]
        log(f"{key}（{verdict}）⇒ rc={p.returncode}：" + " / ".join(x[:80] for x in tail))
        rc_all = rc_all or p.returncode
    return rc_all


if __name__ == "__main__":
    raise SystemExit(main())
