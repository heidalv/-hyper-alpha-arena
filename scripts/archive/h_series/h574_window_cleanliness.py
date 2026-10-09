"""h574 — ② 的判定窗口**清洁度**扫描（只读，R76）。

背景：`h553` 只扫 ③ 的窗口（链触发 → +12h），**② 的窗口（09:48 → 21:48）从未扫过**。
② 今晚 21:48 判定；若窗口内有别的任务写 `lane_registry.params`，判定的自动断点检测
（`h521`）会把它记为 known break ⇒ 负向 Δ 降级为 INCONCLUSIVE（保护性，但会丢判决）。
故先扫一遍，把"会不会发生"搞清楚。

判定窗口取法：从登记表 `h463_trial` 的 `started_at` 到 `judge_at`（**权威源**，不手写）。

⚠️ 注意：对**尚未部署**的试跑，登记表里可能还留着**上一次**（已回滚）试跑的 `started_at`
⇒ 扫出来的是旧窗口。③(h464) 的真实窗口要等它明早部署后才生成；
在那之前请用 `h553_chain_preflight.py`（它按链的下次触发时刻推算 ③ 的未来窗口）。

用法：python scripts/h574_window_cleanliness.py [--trial h463]
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import argparse
import csv
import datetime as dt
import importlib.util
import io
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

# 能写 `lane_registry.params` 的脚本（与 h553 同源；含链与重启器）
WRITERS = ("h425_repair_trial.py", "h356_universe_trial.py", "h354_p2_judge.py",
           "h464_chain.py", "h144_restart_backend.py")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial", default="h463")
    a = ap.parse_args()
    meta_key = f"{a.trial}_trial"
    import psycopg
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
    t = dict(meta.get(meta_key) or {})
    since, judge_at = t.get("started_at"), t.get("judge_at")
    if not since or not judge_at:
        print(f"✗ meta.{meta_key} 缺 started_at/judge_at")
        return 1
    w0 = dt.datetime.fromisoformat(since).astimezone()
    w1 = dt.datetime.fromisoformat(judge_at).astimezone()
    print("=" * 92)
    print(f"{a.trial} 判定窗口清洁度：{w0:%m-%d %H:%M} → {w1:%m-%d %H:%M}（本地）")
    print(f"  权威源：lane_registry.meta_json->'{meta_key}'（started_at / judge_at）")
    print("=" * 92)
    p = subprocess.run(["schtasks", "/query", "/fo", "CSV", "/v"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    rows = list(csv.DictReader(io.StringIO(p.stdout or "")))
    now = dt.datetime.now().astimezone()   # 必须带时区，否则与 `when` 相减抛 TypeError
    # **排除本试跑自己的判定任务**：判定框架的自动断点检测会跳过本试跑自己的 ops
    # （`h425_repair_trial.py` 里 `_a.startswith(f"{key}_") ⇒ continue`），
    # 故它出现在窗口内**不是**混淆源。首版漏了这条 ⇒ 对自己的任务报假警报 ✗。
    own_judge = f"DSH_HFT_{a.trial.upper()}_JUDGE"
    in_win, writers, other, skipped = [], [], [], []
    for r in rows:
        name = (r.get("TaskName") or "").replace("\\", "")
        if not name.startswith("DSH_"):
            continue
        if (r.get("Status") or "") != "Ready":
            continue
        nxt = (r.get("Next Run Time") or "").strip()
        if nxt in ("N/A", ""):
            continue
        try:
            # 计划任务的时间是**本地朴素时间** ⇒ 补上本地时区再与窗口比较
            # （否则 aware vs naive 直接抛 TypeError —— 本脚本首版就是这样崩的）
            when = dt.datetime.strptime(nxt, "%Y/%m/%d %H:%M:%S").astimezone()
        except Exception:  # noqa: BLE001
            continue
        if not (w0 <= when <= w1):
            continue
        if name == own_judge:
            skipped.append((when, name, False))
            continue
        tr = r.get("Task To Run") or ""
        is_writer = any(w in tr for w in WRITERS)
        (writers if is_writer else other).append((when, name, is_writer))

    def _show(items, tag):
        for when, name, _w in sorted(items):
            eta = (when - now).total_seconds() / 3600.0
            print(f"  {tag} {when:%m-%d %H:%M}（{eta:+.1f}h）  {name}")

    print(f"窗口内**会写参数**的任务：{len(writers)} 个")
    _show(writers, "⚠️")
    if skipped:
        for when, name, _w in sorted(skipped):
            print(f"  ✓ {when:%m-%d %H:%M}  {name}（**本试跑自己的判定任务**，"
                  f"判定框架会跳过它自己的 ops ⇒ 不算混淆源）")
    print(f"窗口内其它已排任务（不写参数，无混淆风险）：{len(other)} 个")
    _show(other, "·")
    print("-" * 92)
    if writers:
        print("⇒ ⚠️ 窗口内有写参数任务 ⇒ 判定的自动断点检测会把负向 Δ 降级为 INCONCLUSIVE")
    else:
        print("⇒ ✓ 窗口干净：② 的判决不会被其它参数变更混淆")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
