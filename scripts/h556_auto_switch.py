"""h556：**自动换节点守卫**——把"凌晨没人换节点 ⇒ 车道空转数小时"这个缺口补上。

事故依据（2026-09-29 当天实测）：
  · #6「深圳-香港办公室出口」TCP 都不通 ⇒ 零腿 4.6 小时；
  · 换上 #1 后**约 15 分钟即失效**；#14 不通；#12 可用。
  ⇒ 该机场当天多处节点不稳定，而 `h536` 告警虽然每 10 分钟报 CRITICAL，
    **但凌晨没人看着**，车道就会一直空转。

本守卫的**节制的**自动动作：
  1. 自己测「到 fapi/fstream 的新建 TLS 隧道」（与 h536 同一判据，不依赖其输出）；
  2. **连续 2 次（≈20 分钟）都失败**才动作——避免对瞬时抖动反应；
  3. 动作 = 调 `h542_switch_ss_node.py --apply --max-tries 6`（逐个候选试到
     REST+WS 双通为止；**配置先备份**，可 `--restore` 还原）；
  4. **限频**：6 小时内最多 3 次；超过则停手并大声记日志（说明供应商整体挂了，
     继续轮换只会把节点全烧掉）；
  5. 全程写 `logs/auto_switch.log`，状态存 `research_l1/out/h556_state.json`。

⚠️ **它会改动你机器的 Shadowsocks 节点**（仅在该线路已经不通时）。停用：
    `schtasks /change /tn "DSH_HFT_AUTO_SWITCH" /disable`

用法：
  python scripts/h556_auto_switch.py            # 干跑：只报状态与将要做的动作
  python scripts/h556_auto_switch.py --apply     # 允许真正换节点
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
sys.path.insert(0, str(ROOT))
# 状态文件与日志路径都可被环境变量覆盖（**仅供离线测试**，见 h557）；
# ⚠️ 日志**必须**可覆盖：R41 我第一次跑 h557 时忘了这一点，测试往生产
# `logs/auto_switch.log` 写了 3 条假的"失败/停手"记录 ✗（与本会话早先
# "replay 测试污染出场探针"同一类错误）⇒ 测试钩子必须覆盖**所有**落盘路径。
STATE = pathlib.Path(os.environ.get("H556_STATE_PATH")
                     or (ROOT / "research_l1" / "out" / "h556_state.json"))
LOG = pathlib.Path(os.environ.get("H556_LOG_PATH")
                   or (ROOT / "logs" / "auto_switch.log"))
SWITCHER = ROOT / "scripts" / "h542_switch_ss_node.py"
NEED_CONSEC = 2          # 连续失败几次才动作
MAX_PER_6H = 3           # 6 小时内的动作上限


def log(msg: str) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def proxy_ok() -> tuple[bool, str]:
    """测新建隧道（与 h536 同判据：CONNECT + 隧道内真做 TLS 握手）。

    `H556_FORCE_FAIL=1` 时强制返回失败——**仅供离线测试**（见 `h557`），
    用来验证"连续失败计数 / 门槛 / 限频 / 停手"这条平时永远不会走的路径。
    """
    if os.environ.get("H556_FORCE_FAIL") == "1":
        return False, "（测试钩子 H556_FORCE_FAIL=1 强制失败）"
    try:
        from scripts.h536_lane_alarm import proxy_data_path_ok  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return False, f"导入判据失败：{str(exc)[:60]}"
    res = []
    for host in ("fapi.asterdex.com", "fstream.asterdex.com"):
        ok, msg = proxy_data_path_ok(host=host, port=443)
        res.append((host, ok, msg))
    ok_any = any(r[1] for r in res)
    detail = "；".join(f"{h} {'✓' if o else '✗'}" for h, o, _ in res)
    return ok_any, detail


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"consec_fail": 0, "switches": []}


def save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def recent_switches(st: dict, hours: float = 6.0) -> list:
    cut = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    keep = []
    for ts in st.get("switches") or []:
        try:
            t = dt.datetime.fromisoformat(str(ts))
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
            if t >= cut:
                keep.append(str(ts))
        except Exception:  # noqa: BLE001
            continue
    return keep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="允许真正换节点")
    a = ap.parse_args()
    st = load_state()
    ok, detail = proxy_ok()
    # 每次运行都写心跳（不刷日志：正常时不写日志行，但状态文件里能看到"守卫活着"）
    st["last_check_ts"] = dt.datetime.now(dt.timezone.utc).isoformat()
    st["last_ok"] = bool(ok)
    st["last_detail"] = detail
    if ok:
        if st.get("consec_fail"):
            log(f"隧道恢复 ✓（{detail}）⇒ 计数归零（此前连续失败 {st['consec_fail']} 次）")
        st["consec_fail"] = 0
        save_state(st)
        print(f"隧道正常 ✓ {detail}")
        return 0
    st["consec_fail"] = int(st.get("consec_fail") or 0) + 1
    log(f"隧道失败（连续第 {st['consec_fail']} 次）：{detail}")
    save_state(st)
    if st["consec_fail"] < NEED_CONSEC:
        print(f"未达动作门槛（{st['consec_fail']}/{NEED_CONSEC}）⇒ 只记状态")
        return 0
    recent = recent_switches(st)
    if len(recent) >= MAX_PER_6H:
        log(f"✗ 6h 内已换 {len(recent)} 次（上限 {MAX_PER_6H}）⇒ **停手**："
            f"疑似供应商整体故障，继续轮换只会烧掉更多节点；需人工处理")
        return 2
    if not a.apply:
        print(f"[干跑] 将执行：{SWITCHER} --apply --max-tries 6（近 6h 已换 {len(recent)} 次）")
        return 0
    log(f"⇒ 连续失败 {st['consec_fail']} 次，执行换节点（近 6h 第 {len(recent)+1} 次）")
    p = subprocess.run([sys.executable, str(SWITCHER), "--apply", "--max-tries", "6"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-6:]
    for line in tail:
        log("  " + line[:160])
    if p.returncode == 0:
        st.setdefault("switches", []).append(dt.datetime.now(dt.timezone.utc).isoformat())
        st["consec_fail"] = 0
        log("✓ 换节点成功（详见 h542 输出）")
    else:
        log(f"✗ 换节点未成功（rc={p.returncode}）⇒ 保留计数，下次继续")
    save_state(st)
    return p.returncode


if __name__ == "__main__":
    raise SystemExit(main())
