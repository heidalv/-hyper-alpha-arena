"""h589 — 验证待命件的**互锁输入**（只读，R104）。

`standby_ingest.py --to-prod` 靠 `_competing_ingestors()` 检测"是否已有别的采集器在跑"，
有则**拒绝写正式表** ✗✓。本脚本只调用该函数（**不采集、不写库** ✓），确认：
  · 原件在跑时，它**能**识别出来（否则互锁形同虚设 ✗）。

用法：python scripts/h589_interlock_check.py
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("sb", ROOT / "scripts" / "standby_ingest.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)  # type: ignore[union-attr]


def main() -> int:
    others = m._competing_ingestors()
    print("=" * 90)
    print("互锁输入检查（只读：不采集、不写库）")
    print("=" * 90)
    if others:
        print(f"  ✓ 检测到 {len(others)} 个采集器进程 ⇒ `--to-prod` 会**拒绝写正式表** ✓")
        for pid, cl in others:
            print(f"    pid={pid}  {cl}")
        return 0
    print("  ⚠️ 未检测到任何采集器进程 ⇒ 互锁**不会**拒绝（此时 --to-prod 会写正式表 ✗）")
    print("     ⇒ 只有在'确认没有别的采集器'时才应出现这种状态 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
