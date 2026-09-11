# -*- coding: utf-8 -*-
"""[§81 修复收尾 2026-09-11] 恢复被"读一次就抹掉"破坏的持久化状态。

背景：修复前，任何进程读一次 `data/fusion_attribution.json` 都会把 `breaker_shadow`
从磁盘抹掉（本次由我自己的审计脚本 Z216 触发，11:30:14 实测 7 真 → 0）。运行中的后端
（pid 3756）内存里仍是 13 键/7 真，因此**线上判定当时未受影响**，但磁盘持久化已失真。

本脚本按纪律做三件事：
  ① 先备份（`*.bak_<ts>`）；
  ② 用**修复后**的加载路径重建标志（口径与 record_close 相同，来自 `breaker` 滚动窗）；
  ③ 落盘并核对：`shadow_mode=rolling`、真值数、与 `breaker` 窗口重算一致。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import os  # noqa: E402
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=False)

import backend.services.source_attribution as sa  # noqa: E402

STATE = Path(sa._STATE_PATH)


def main() -> int:
    print("=" * 96)
    print("修复前状态")
    print("=" * 96)
    before = json.loads(STATE.read_text(encoding="utf-8"))
    b_flags = {k: v for k, v in (before.get("breaker_shadow") or {}).items() if v}
    print(f"  路径：{STATE}")
    print(f"  breaker={len(before.get('breaker') or {})} 键；breaker_shadow 真值={len(b_flags)}；"
          f"标记={before.get(sa.SHADOW_MODE_KEY)!r}")

    backup = STATE.with_name(STATE.name + f".bak_{time.strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(STATE, backup)
    print(f"  已备份 → {backup.name}")

    print()
    print("=" * 96)
    print("加载（重建）+ 落盘")
    print("=" * 96)
    a = sa.SourceAttribution()
    a._ensure_loaded()
    mem = {k: v for k, v in (a._breaker_shadow or {}).items() if v}
    print(f"  内存重建：{len(a._breaker_shadow or {})} 键评估 / {len(mem)} 键 shadow")
    if a._breaker_shadow != (before.get("breaker_shadow") or {}):
        a._maybe_save(force=True)
        print("  已落盘（磁盘与内存不一致）")

    after = json.loads(STATE.read_text(encoding="utf-8"))
    a_flags = {k: v for k, v in (after.get("breaker_shadow") or {}).items() if v}
    print()
    print("=" * 96)
    print("修复后核对")
    print("=" * 96)
    print(f"  标记 = {after.get(sa.SHADOW_MODE_KEY)!r}（应为 'rolling'）")
    print(f"  breaker_shadow 真值 = {len(a_flags)} 键：{sorted(a_flags)}")
    print(f"  tags/stats/breaker 未受影响：{len(after.get('tags') or {})}/"
          f"{len(after.get('stats') or {})}/{len(after.get('breaker') or {})}")
    # 独立复核：用生效阈值重算一遍，必须逐键一致
    try:
        min_n = int(float(os.environ.get("EXIT_CHANNEL_SHADOW_MIN_N", "30") or 30))
        max_wr = float(os.environ.get("EXIT_CHANNEL_SHADOW_MAX_WR", "0.40") or 0.40)
    except (TypeError, ValueError):
        min_n, max_wr = 30, 0.40
    win = min(min_n, sa._ROLLING_WINDOW)
    recomputed = {}
    for k, v in (after.get("breaker") or {}).items():
        rec = [x for x in (v.get("recent") or []) if x in (0, 1, True, False)]
        if len(rec) < win:
            continue
        recomputed[k] = (sum(1 for x in rec[-win:] if x) / win) < max_wr
    recomputed_true = {k for k, v in recomputed.items() if v}
    print(f"  独立重算（窗口 {win} / 阈值 {max_wr}）：{len(recomputed)} 键评估 / "
          f"{len(recomputed_true)} 键 shadow")
    same = recomputed_true == set(a_flags)
    print(f"  两者一致：{'✅' if same else '❗不一致 ' + str(recomputed_true ^ set(a_flags))}")
    return 0 if same and after.get(sa.SHADOW_MODE_KEY) == sa.SHADOW_MODE_ROLLING else 1


if __name__ == "__main__":
    raise SystemExit(main())
