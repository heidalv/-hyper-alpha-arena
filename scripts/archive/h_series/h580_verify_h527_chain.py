"""h580 — `h527_chain.py`（④ 的串行链）纯函数与解析逻辑的离线验证（R84）。

为什么要写：这条链明晚 22:08 第一次真跑。它的"改期复核"首版用字符串匹配
（`"2026/09/30"` 对 schtasks 实际输出的 `2026/9/30`）⇒ **必然误报**，靠读代码不易发现。
本用例覆盖：
  1. `_gate`：真实现状（终局但无判定产物）⇒ 拦下；终局+产物 ⇒ 放行；非终局 ⇒ 拦下；
     空状态 ⇒ 放行；判定疑似未落地（verdict 空 + judge_at 已过 30min）⇒ 放行；
  2. `_window_end`：取 `max(judge_at, extend_until)`，脏字段被忽略；
  3. `_defer_target`：对准窗口结束 +10min；过期/缺失 ⇒ now+30min；**永不落在过去**；
  4. `_parse_next_run`：容忍补零/不补零/缺秒；无该行 ⇒ None。
**不触碰生产**：判定产物路径与日志都用 `H527_*` 钩子指向临时目录，并在结尾断言
生产日志未被创建（R41/R57/R73 纪律）。

用法：python scripts/h580_verify_h527_chain.py
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import datetime as dt
import hashlib
import importlib.util
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROD_LOG = ROOT / "research_l1" / "out" / "h527_chain.log"
PROD_DEP_VERDICT = ROOT / "research_l1" / "out" / "h464_verdict.json"


def _sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "(absent)"


def _load(dep_verdict: pathlib.Path, log_path: pathlib.Path):
    os.environ["H527_DEP_VERDICT_PATH"] = str(dep_verdict)
    os.environ["H527_LOG_PATH"] = str(log_path)
    spec = importlib.util.spec_from_file_location("c527", ROOT / "scripts" / "h527_chain.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)  # type: ignore[union-attr]
    return m


def main() -> int:
    log0, vd0 = _sha(PROD_LOG), _sha(PROD_DEP_VERDICT)
    fails = 0
    NOW = dt.datetime(2026, 9, 30, 22, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        vp, lp = tdp / "h464_verdict.json", tdp / "chain.log"
        st_term = {"verdict": "PASS", "started_at": "2026-09-30T01:58:00+00:00",
                   "judge_at": "2026-09-30T13:58:00+00:00"}
        st_incon = {**st_term, "verdict": "INCONCLUSIVE"}
        cases = [
            ("终局但**无判定产物**（当前真实现状）⇒ 拦下", st_term, 0, False),
            ("终局 + 有产物 ⇒ 放行", st_term, 1, True),
            ("非终局（INCONCLUSIVE）⇒ 拦下", st_incon, 1, False),
            ("读不到状态（空 dict）⇒ 放行（回退旧行为）", {}, 1, True),
            ("判定疑似未落地（verdict 空 + judge_at 已过 30min）⇒ 放行",
             {"started_at": "2026-09-30T01:58:00+00:00",
              "judge_at": "2026-09-30T12:00:00+00:00"}, 0, True),
        ]
        print("=" * 88)
        print("① 隔离闸 `_gate`（现实库状态注入：先无产物、再有产物）")
        print("=" * 88)
        for i, (name, st, make_artifact, want) in enumerate(cases):
            if make_artifact and not vp.exists():
                vp.write_text(json.dumps({"verdict": st.get("verdict"), "hours": 12.0}),
                              encoding="utf-8")
            elif not make_artifact and vp.exists():
                vp.unlink()
            m = _load(vp, lp)
            got, why = m._gate(st, now=NOW)
            ok = got == want
            fails += 0 if ok else 1
            print(f"  {'✓' if ok else '✗'} {name}")
            print(f"      got={got} | {why[:88]}")

        print("\n" + "=" * 88)
        print("② 窗口结束 `_window_end` 与改期目标 `_defer_target`")
        print("=" * 88)
        m = _load(vp, lp)
        we_cases = [
            ({"judge_at": "2026-09-30T13:58:00+00:00"}, "2026-09-30 21:58",
             "只有 judge_at"),
            ({"judge_at": "2026-09-30T13:58:00+00:00",
              "extend_until": "2026-10-01T02:58:00+00:00"}, "2026-10-01 10:58",
             "extend_until 更晚 ⇒ 取它（R74）"),
            ({"judge_at": "2026-09-30T13:58:00+00:00", "extend_until": "not-a-time"},
             "2026-09-30 21:58", "extend_until 脏 ⇒ 退回 judge_at"),
            ({}, None, "都没有 ⇒ None"),
        ]
        for st, want, why in we_cases:
            got = m._window_end(st)
            gs = got.astimezone().strftime("%Y-%m-%d %H:%M") if got else None
            ok = gs == want
            fails += 0 if ok else 1
            print(f"  {'✓' if ok else '✗'} `_window_end` {why}: want={want} got={gs}")
        df_cases = [
            ({"judge_at": "2026-10-01T02:58:00+00:00"}, "2026-10-01 11:08",
             "对准窗口结束 +10min"),
            ({"judge_at": "2026-09-01T00:00:00+00:00"}, "2026-09-30 22:30",
             "窗口早已结束 ⇒ now+30min，**不得**排到过去"),
            ({}, "2026-09-30 22:30", "字段缺失 ⇒ now+30min"),
        ]
        for st, want, why in df_cases:
            got = m._defer_target(st, now=NOW).strftime("%Y-%m-%d %H:%M")
            ok = got == want
            fails += 0 if ok else 1
            print(f"  {'✓' if ok else '✗'} `_defer_target` {why}: want={want} got={got}")

        print("\n" + "=" * 88)
        print("③ 改期复核的解析 `_parse_next_run`（R84 修的那个 bug）")
        print("=" * 88)
        pn_cases = [
            ("Next Run Time:                        2026/9/30 22:08:00",
             "2026-09-30 22:08", "schtasks 实际格式（月/日**不补零**）"),
            ("Next Run Time:                        2026/09/30 22:08:00",
             "2026-09-30 22:08", "补零也容忍"),
            ("Next Run Time:                        2026/9/30 22:08",
             "2026-09-30 22:08", "缺秒也容忍"),
            ("Status: Ready\nTask To Run: x", None, "无该行 ⇒ None"),
        ]
        for text, want, why in pn_cases:
            got = m._parse_next_run(text)
            gs = got.strftime("%Y-%m-%d %H:%M") if got else None
            ok = gs == want
            fails += 0 if ok else 1
            print(f"  {'✓' if ok else '✗'} {why}: want={want} got={gs}")
        # 复核逻辑本身：目标 22:08 与解析出的 22:08 应判"通过"
        target = dt.datetime(2026, 9, 30, 22, 8)
        parsed = m._parse_next_run("Next Run Time:   2026/9/30 22:08:00")
        ok = parsed is not None and abs((parsed - target).total_seconds()) <= 120
        fails += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} 复核判定：目标 22:08 vs 任务报 22:08 ⇒ "
              f"{'通过' if ok else '误报未通过'}（R84 前必错）")

    os.environ.pop("H527_DEP_VERDICT_PATH", None)
    os.environ.pop("H527_LOG_PATH", None)
    same = (_sha(PROD_LOG) == log0) and (_sha(PROD_DEP_VERDICT) == vd0)
    print(f"\n  {'✓' if same else '✗'} 生产路径未被触碰（日志与 ③ 判定产物 sha256 不变）")
    print("=" * 88)
    print("✓ 全部通过" if (not fails and same) else f"✗ {fails} 项不符 / 生产被触碰={not same}")
    return 0 if (not fails and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
