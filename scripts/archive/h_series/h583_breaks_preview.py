"""h583 — ② 窗口内 `ops_changes` 的**自动断点检测预演**（只读，R94）。

判定代码（`h425_repair_trial.py:1197-1223`）会扫描登记表 `ops_changes`，把落在
`(since, now]` 内、且**不是本试跑自己**的 `deploy/rollback/restore` 记成 known break
⇒ 若 Welch 显著为负，判决会被降级为 INCONCLUSIVE（保护性，但会丢判决）。

本脚本把那套逻辑**照搬一遍**（同一份 `ops_changes`、同一组判据），让 21:48 之前就能看到
"到底会不会被降级"。

用法：python scripts/h583_breaks_preview.py [--trial h463]
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import argparse
import datetime as dt
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)  # type: ignore[union-attr]

import psycopg  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial", default="h463")
    a = ap.parse_args()
    key = a.trial
    with psycopg.connect(h.read_env_dsn()) as c, c.cursor() as cur:
        meta = h._load_meta(cur)
        trial = dict(meta.get(f"{key}_trial") or {})
        since = trial.get("started_at")
        if not since:
            print(f"✗ meta.{key}_trial.started_at 缺失")
            return 1
        since_dt = dt.datetime.fromisoformat(since)
        now = dt.datetime.now(dt.timezone.utc)
        print("=" * 92)
        print(f"{key} 窗口 [{since} → now]，照搬判定的自动断点检测")
        print("=" * 92)
        print(f"  窗口内 ops_changes 共 "
              f"{sum(1 for op in (meta.get('ops_changes') or []) if op.get('ts') and dt.datetime.fromisoformat(str(op['ts'])) > since_dt)} 条")
        print(f"  {'本地时间':<20}{'action':<28}{'是否计入断点'}")
        breaks = []
        for op in (meta.get("ops_changes") or []):
            ts = op.get("ts")
            if not ts:
                continue
            try:
                t = dt.datetime.fromisoformat(str(ts))
            except Exception:  # noqa: BLE001
                continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
            if not (since_dt < t <= now):
                continue
            act = str(op.get("action") or "")
            own = act.startswith(f"{key}_")
            counted = (not own) and any(k in act for k in ("deploy", "rollback", "restore"))
            if counted:
                breaks.append({"ts": t.isoformat(), "action": act})
            print(f"  {t.astimezone():%m-%d %H:%M:%S}    {act[:28]:<28}"
                  f"{'⚠️ 计入' if counted else ('本试跑自己的动作 ⇒ 跳过' if own else '不计')}")
        print("-" * 92)
        if breaks:
            print(f"⇒ ⚠️ 窗口内检测到 {len(breaks)} 项其它变更 ⇒ **负向 Δ 会被降级为 INCONCLUSIVE**")
            for b in breaks:
                print(f"     {b['ts']}  {b['action']}")
        else:
            print("⇒ ✓ 无其它变更 ⇒ 若 Welch 显著为负，判决会**如实给出 ROLLBACK**（不被降级）")

        # [R198] **判定的盲区：基线与试跑窗之间的"缝隙变更"** ✗
        # 判定代码（以及上面这段照搬）只扫 `(since, now]` —— 即**试跑窗内**。但主判据是
        # **试跑窗 vs 基线窗（since−24h→since−12h）** 的对比 ⇒ 只要在
        # `(since−12h, since]` 这段**缝隙**里改过任何参数，两侧就不再可比，而框架**看不见** ✗
        # （实测 ② 正是如此：h448 在 09-28 23:17L 把 ofi_confirm 0.15→0.5，正落在缝隙里）。
        b0 = since_dt - dt.timedelta(hours=24)
        b1 = since_dt - dt.timedelta(hours=12)
        print(f"\n  对比涉及**三段**，而判定只扫第三段 ✗；下面把前两段也扫一遍（R198/R198b）：")
        for _label, _x0, _x1 in (("基线窗内（since−24h → since−12h）", b0, b1),
                                 ("缝隙（基线末 → 试跑始）", b1, since_dt)):
            seg = []
            for op in (meta.get("ops_changes") or []):
                ts = op.get("ts")
                if not ts:
                    continue
                try:
                    t = dt.datetime.fromisoformat(str(ts))
                except Exception:  # noqa: BLE001
                    continue
                if t.tzinfo is None:
                    t = t.replace(tzinfo=dt.timezone.utc)
                if not (_x0 < t <= _x1):
                    continue
                act = str(op.get("action") or "")
                if act.startswith(f"{key}_"):
                    continue
                if any(k in act for k in ("deploy", "rollback", "restore")):
                    seg.append((t, act))
            if seg:
                print(f"  ⚠️ {_label}：{len(seg)} 项 ⇒ 对比不均匀 ✗")
                for t, act in seg:
                    print(f"       {t.astimezone():%m-%d %H:%M:%S}  {act[:60]}")
            else:
                print(f"  ✓ {_label}：无变更 ✓")
        print("  ⚠️ `ops_changes` **只保留最近 20 条** ⇒ 以上都是**下界**（更早的变更查不到）✗")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
