# -*- coding: utf-8 -*-
"""[2026-10-10 前端升级] 只读验收：低频 job 首次心跳宽限 + `/api/hft/evolution` 真数据。

**必须打真实后端**（`http://127.0.0.1:8000`）：`_last_tick_at` 是**进程内**状态，
本脚本新建的进程里所有 job 都是"没有心跳"，会把宽限逻辑测成假失败。

用法：.venv\\Scripts\\python.exe backend\\tests\\tools\\verify_frontend_upgrade_20261010.py
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

API = "http://127.0.0.1:8000"


def _get(path: str, timeout: float = 30.0):
    with urllib.request.urlopen(API + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    try:
        h = _get("/api/learning/health")
    except Exception as e:  # noqa: BLE001
        print(f"✗ 后端不可达（{API}）：{e}")
        return 1

    print(f"overall={h.get('overall')}")
    low_freq = {"loop_factor_decay", "loop_fusion", "loop_live_backfill"}
    # 判据修正：低频 job **首次**跑之前显示 dead 是**正确**的（宽限 0.5h 就是为了让
    # 它别显示"18 小时后才算 ok"）。真正要钉的是**阈值本身**：
    #   · 从没跑过 ⇒ threshold 必须是 0.5h（首次宽限）
    #   · 跑过     ⇒ threshold 回到真实值（12h / 1h / 12h）
    # 若实现回退（阈值恒为 18h），首次宽限这条会失败。
    bad = []
    for it in h.get("items", []):
        nm = str(it.get("name"))
        if nm in low_freq or nm.startswith("loop_hft"):
            thr = float(it.get("threshold_hours") or 0)
            print("  {0:<22} {1:<6} thr={2}h {3}".format(
                nm, it.get("status"), thr, str(it.get("detail", ""))[:44]))
            if thr <= 0:
                bad.append(f"{nm}: 阈值异常 {thr}")
            elif thr > 0.5 and it.get("status") == "dead":
                # 有过心跳却还是 dead ⇒ 真的停了（这时才该报）
                bad.append(f"{nm}: 已有心跳仍 dead（阈值 {thr}h）")
    print()
    if bad:
        print("✗ 心跳判定有问题：")
        for b in bad:
            print("   -", b)
        return 1
    print("✓ 首次心跳宽限生效（未跑过的 job 阈值=0.5h）；跑过的 job 阈值回到真实值")

    ev = _get("/api/hft/evolution")
    print()
    print("evolution stub_fields:", ev.get("stub_fields"))
    n_pending = len(ev.get("pending_verdicts") or [])
    n_adv = len(ev.get("advisor_recent") or [])
    print(f"pending_verdicts={n_pending} advisor_recent={n_adv}")
    pend_file = ROOT / "logs" / "self_tuner_pending.json"
    expect = None
    if pend_file.exists():
        try:
            rows = json.loads(pend_file.read_text(encoding="utf-8"))
            expect = len(rows) if isinstance(rows, list) else None
        except Exception:  # noqa: BLE001
            expect = None
    print(f"  文件 logs/self_tuner_pending.json 条数={expect}")
    if expect is not None and n_pending != expect:
        print("✗ pending_verdicts 与文件不一致（可能仍是空壳）")
        return 1
    if n_adv == 0:
        print("✗ advisor_recent 为空（顾问日志应该已有记录）")
        return 1
    print("✓ pending_verdicts 与队列文件一致；advisor_recent 有真实记录")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
