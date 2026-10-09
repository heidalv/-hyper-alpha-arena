"""h557：**离线验证自动换节点守卫的失败路径**（不碰 VPN、不换任何节点）。

为什么必须测：`h556` 是唯一一个会**自动改动本机 Shadowsocks 节点**的执行者，
而它平时永远走"隧道正常 ⇒ 什么都不做"这条路 ⇒ **真正需要它的那条路径从未被验证过**。
自动化最危险的失败模式正是"平时看不出问题、真需要时不动"。

本脚本用 `h556` 的两个**测试钩子**（`H556_FORCE_FAIL=1` / `H556_STATE_PATH`）
把它当子进程跑，逐条验证状态机：

  ① 连续失败 1 次 ⇒ 只记状态、不动作（退出 0）；
  ② 连续失败 2 次 ⇒ 到达门槛；**干跑**下只打印将要执行的动作（退出 0）；
  ③ 恢复成功 ⇒ 计数归零；
  ④ 近 6h 已换 3 次 + 仍失败 ⇒ **停手**（退出 2，且不得调用换节点脚本）。

用法：python scripts/h557_verify_auto_switch.py
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
SCRIPT = ROOT / "scripts" / "h556_auto_switch.py"


def run(state_path: pathlib.Path, force_fail: bool, apply: bool = False,
        log_path: pathlib.Path | None = None) -> tuple:
    env = dict(os.environ)
    env["H556_STATE_PATH"] = str(state_path)
    # ⚠️ 日志路径**也必须**覆盖：否则测试会往生产 logs/auto_switch.log 写假的
    # "失败/停手"记录（R41 首版就污染了 3 条，已隔离到
    # research_l1/out/auto_switch_testpollution.log）。
    env["H556_LOG_PATH"] = str(log_path or (state_path.parent / "auto_switch_test.log"))
    env["PYTHONIOENCODING"] = "utf-8"
    if force_fail:
        env["H556_FORCE_FAIL"] = "1"
    else:
        env.pop("H556_FORCE_FAIL", None)
    args = [str(PY), str(SCRIPT)] + (["--apply"] if apply else [])
    p = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=300)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def main() -> int:
    fails = 0
    with tempfile.TemporaryDirectory() as td:
        stp = pathlib.Path(td) / "state.json"

        print("=" * 88)
        print("① 连续失败 1 次 ⇒ 只记状态、不动作")
        rc, out = run(stp, force_fail=True)
        st = json.loads(stp.read_text(encoding="utf-8"))
        ok = (rc == 0 and st.get("consec_fail") == 1 and "未达动作门槛" in out)
        print(f"  {'✓' if ok else '✗'} rc={rc} consec_fail={st.get('consec_fail')}")
        fails += 0 if ok else 1

        print("\n② 连续失败 2 次 ⇒ 到达门槛（干跑：只打印将执行的动作，不真换）")
        rc, out = run(stp, force_fail=True)
        st = json.loads(stp.read_text(encoding="utf-8"))
        ok = (rc == 0 and st.get("consec_fail") == 2 and "将执行" in out
              and "h542_switch_ss_node.py" in out)
        print(f"  {'✓' if ok else '✗'} rc={rc} consec_fail={st.get('consec_fail')} "
              f"plan={'有' if '将执行' in out else '无'}")
        fails += 0 if ok else 1

        print("\n③ 恢复成功 ⇒ 计数归零")
        rc, out = run(stp, force_fail=False)
        st = json.loads(stp.read_text(encoding="utf-8"))
        ok = (rc == 0 and st.get("consec_fail") == 0 and st.get("last_ok") is True)
        print(f"  {'✓' if ok else '✗'} rc={rc} consec_fail={st.get('consec_fail')} "
              f"last_ok={st.get('last_ok')}")
        fails += 0 if ok else 1

        print("\n④ 近 6h 已换 3 次 + 仍失败 ⇒ **停手**（退出 2，不得调用换节点）")
        now = dt.datetime.now(dt.timezone.utc)
        stp.write_text(json.dumps({
            "consec_fail": 5,
            "switches": [(now - dt.timedelta(minutes=10 * i)).isoformat()
                         for i in range(1, 4)],
        }, ensure_ascii=False), encoding="utf-8")
        rc, out = run(stp, force_fail=True)
        st = json.loads(stp.read_text(encoding="utf-8"))
        ok = (rc == 2 and "停手" in out and "h542_switch_ss_node.py" not in out
              and st.get("consec_fail") == 6)
        print(f"  {'✓' if ok else '✗'} rc={rc}（应=2） 提到换节点={'是 ✗' if 'h542' in out else '否 ✓'}")
        fails += 0 if ok else 1

    print("\n" + "=" * 88)
    if fails:
        print(f"✗ {fails} 项未通过")
        return 1
    print("✓ 状态机全部符合预期：门槛/限频/停手/恢复归零 都成立，"
          "且**干跑从不真的换节点**（真换只在 --apply 且达门槛时）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
