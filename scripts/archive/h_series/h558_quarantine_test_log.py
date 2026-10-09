"""h558：把被测试污染的日志行**隔离**出去（同 h490 出场探针那次的做法）。

现场：R41 首版 `h557` 只覆盖了状态文件路径、**没覆盖日志路径** ⇒ 它往生产
`logs/auto_switch.log` 写了 3 条假的「连续失败 / 停手」记录。
若不清理，以后排查"守卫到底动过几次手"会被这三条误导（正是"证据链被污染"）。

处置：
  1. 把含测试钩子字样（`H556_FORCE_FAIL`）的行**移出**到
     `research_l1/out/auto_switch_testpollution.log`（保留证据、不销毁）；
  2. 其余行原样保留；
  3. 打印前后行数与移出的内容，便于核对。

用法：python scripts/h558_quarantine_test_log.py [--apply]
"""
from __future__ import annotations

import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "auto_switch.log"
QUAR = ROOT / "research_l1" / "out" / "auto_switch_testpollution.log"
MARK = "H556_FORCE_FAIL"


def main() -> int:
    apply = "--apply" in sys.argv
    all_mode = "--all" in sys.argv
    if not LOG.exists():
        print("日志不存在，无需处理")
        return 0
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    if all_mode:
        # 整份隔离：适用于"从未有过真实动作"的情况（判据：状态文件 switches 为空）
        bad, good = lines, []
    else:
        bad = [x for x in lines if MARK in x]
        good = [x for x in lines if MARK not in x]
    print(f"总行数 {len(lines)}：隔离 {len(bad)} 行，保留 {len(good)} 行"
          f"{'（--all：整份隔离）' if all_mode else ''}")
    for x in bad:
        print("  移出：" + x[:120])
    if not apply:
        print("\n⇒ 干跑：加 --apply 才真正移动。")
        return 0
    if bad:
        prev = QUAR.read_text(encoding="utf-8") if QUAR.exists() else ""
        QUAR.write_text(prev + "\n".join(bad) + "\n", encoding="utf-8")
        LOG.write_text("\n".join(good) + ("\n" if good else ""), encoding="utf-8")
        print(f"✓ 已隔离到 {QUAR.relative_to(ROOT)}（证据保留）")
    else:
        print("✓ 没有需要隔离的行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
