"""h561：把文档里"③ 链的时刻"统一更新为生产实测的 21:58（R48）。

背景：R48 在生产环境实跑了一次串行链（闸拦下 ⇒ 改期），任务现在排在 **21:58**
（= ② 判定 21:48 + 10 分钟缓冲）。文档里几处仍写 21:55，需同步，
以免"文档说的"与"任务实际的"不一致（本仓库最贵的教训之一就是这种不一致）。

⚠️ 只改**链的时刻**语义处；预检历史记录里的"当时实测 21:55"保留（那是历史事实）。

用法：python scripts/h561_sync_chain_time.py [--apply]
"""
from __future__ import annotations

import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
EDITS = [
    ("研究结论/三件事验收_20260929.md",
     "| 链任务 `DSH_HFT_H464_CHAIN` | **Ready，next 21:55**，命令行含 `run-quiet.vbs`",
     "| 链任务 `DSH_HFT_H464_CHAIN` | **Ready，next 21:58**（R48 生产实测改期后），命令行含 `run-quiet.vbs`"),
    ("研究结论/停摆事故_20260929.md",
     "> ② 判定改到 **21:48**，③ 的链相应后移到 **21:55**。",
     "> ② 判定改到 **21:48**，③ 的链相应后移到 **21:58**（= ② 判定 +10 分钟缓冲）。"),
    ("研究结论/总账_20260929.md",
     "| **21:30** | **串行链** `DSH_HFT_H464_CHAIN`",
     "| **21:58** | **串行链** `DSH_HFT_H464_CHAIN`"),
]


def main() -> int:
    apply = "--apply" in sys.argv
    for rel, old, new in EDITS:
        p = ROOT / rel
        if not p.exists():
            print(f"✗ 缺文件 {rel}")
            continue
        s = p.read_text(encoding="utf-8")
        if old in s:
            print(f"{'✓ 已更新' if apply else '· 将更新'} {rel}")
            if apply:
                p.write_text(s.replace(old, new, 1), encoding="utf-8")
        else:
            print(f"· {rel}：锚点不存在（可能已更新过）")
    if not apply:
        print("\n⇒ 干跑：加 --apply 才写入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
