"""h554：验证串行链新增的**单变量隔离闸**决策表（离线，无副作用）。

`h464_chain.py` 新增 `_gate()`：只有 ② 的判决**终局**（PASS/ROLLBACK）才部署 ③；
否则把链改期到 ② 下次判定之后——因为判定框架在 INCONCLUSIVE 时会把窗口延长 +13h，
此时部署 ③ 会落进 ② 的窗口内 ⇒ 两边都判不出来。

本脚本只 import 该函数并喂合成状态，**不部署、不改期、不写库**。

用法：python scripts/h554_verify_chain_gate.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.h464_chain import _defer_target, _gate  # noqa: E402

CASES = [
    ({}, True, "读不到状态 ⇒ 回退旧行为（放行）"),
    ({"verdict": "PASS"}, True, "② 判 PASS ⇒ 放行"),
    ({"verdict": "ROLLBACK"}, True, "② 判 ROLLBACK ⇒ 放行（参数已回滚，窗口已收口）"),
    ({"verdict": "INCONCLUSIVE",
      "judge_at": "2026-09-29T13:48:11+00:00"}, False,
     "② INCONCLUSIVE（窗口已延长）⇒ **拦下**并改期"),
    ({"started_at": "2026-09-29T01:48:11+00:00",
      "judge_at": "2026-09-29T13:48:11+00:00"}, False,
     "② 尚未判定 ⇒ **拦下**并改期"),
    ({"verdict": ""}, False, "verdict 为空字符串 ⇒ 拦下"),
    # 有界等待：延长 ≥2 次（窗跨度 ≥38h）⇒ 放行，避免 ③ 被永久饿死
    ({"verdict": "INCONCLUSIVE",
      "started_at": "2026-09-29T01:48:11+00:00",
      "judge_at": "2026-09-30T15:48:11+00:00"}, True,
     "② 延长 ≥2 次仍未终局 ⇒ **放行**（有界等待，防饿死）"),
    ({"verdict": "INCONCLUSIVE",
      "started_at": "2026-09-29T01:48:11+00:00",
      "judge_at": "2026-09-30T02:48:11+00:00"}, False,
     "② 只延长 1 次（跨度 25h）⇒ 仍拦下等待"),
    # [R45] 判定未落地的判别（否则新的饿死路径）：verdict 空 + judge_at 已过 30min ⇒ 放行
    ({"started_at": "2026-09-29T01:48:11+00:00",
      "judge_at": "2026-09-29T13:00:00+00:00"}, True,
     "verdict 空且 judge_at 已过 40 分钟 ⇒ **放行**（判定疑似未落地，走链自带补判）"),
    # ── [R74] **延长窗**用例（原来一个都没有 ⇒ 上面那条 38h 用例测的是不可能出现的状态 ✗）──
    # 判定代码在 INCONCLUSIVE 时写的是 `extend_until`，**不更新 `judge_at`**
    # ⇒ 真实状态形如"judge_at 停在原处 + extend_until = 判定时刻 +13h"。
    ({"verdict": "INCONCLUSIVE",
      "started_at": "2026-09-29T01:48:11+00:00",
      "judge_at": "2026-09-29T13:48:11+00:00",
      "extend_until": "2026-09-30T02:48:11+00:00"}, False,
     "[R74] 真实延长 1 次（judge_at 陈旧 + extend_until ⇒ 跨度 25h）⇒ 仍拦下等待"),
    ({"verdict": "INCONCLUSIVE",
      "started_at": "2026-09-29T01:48:11+00:00",
      "judge_at": "2026-09-29T13:48:11+00:00",
      "extend_until": "2026-09-30T15:48:11+00:00"}, True,
     "[R74] 真实延长 2 次（跨度 38h）⇒ **放行**（安全网终于能触发；改前永远看不到 38h ✗）"),
    ({"verdict": "INCONCLUSIVE",
      "started_at": "2026-09-29T01:48:11+00:00",
      "judge_at": "2026-09-29T13:48:11+00:00",
      "extend_until": "not-a-time"}, False,
     "[R74] extend_until 非法 ⇒ 退回 judge_at（跨度 12h）⇒ 拦下（防御式，不因脏字段放行）"),
]


def main() -> int:
    fails = 0
    # 固定"当前时刻"⇒ 用例不随真实时间翻面（R45：`_gate` 现在依赖 now）
    NOW = dt.datetime(2026, 9, 29, 14, 0, tzinfo=dt.timezone.utc)
    print("=" * 88)
    print(f"单变量隔离闸决策表（now 固定为 {NOW.astimezone():%Y-%m-%d %H:%M} 本地）")
    print("=" * 88)
    for st, want, why in CASES:
        got, msg = _gate(st, now=NOW)
        ok = got == want
        fails += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} want={want!s:<5} got={got!s:<5} | {why}")
        print(f"      返回说明：{msg[:96]}")
    print("=" * 88)
    if fails:
        print(f"✗ {fails} 项不符")
        return 1
    # ── 改期目标时刻（隔离闸拦下后唯一的复活机制，必须挡住"过去时刻"）──
    print("\n" + "=" * 88)
    print("改期目标时刻 `_defer_target`（纯函数）")
    print("=" * 88)
    now = dt.datetime(2026, 9, 29, 22, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
    cases = [
        ({"judge_at": "2026-09-30T02:58:11+00:00"}, "2026-09-30 11:08",
         "② 下次判定 10:58L ⇒ 改期到 11:08L"),
        ({"judge_at": "2026-09-01T00:00:00+00:00"}, "2026-09-29 22:30",
         "judge_at 早已过期（过去）⇒ 改期到 now+30min，**不得**排到过去"),
        ({}, "2026-09-29 22:30", "judge_at 缺失 ⇒ now+30min"),
        ({"judge_at": "not-a-time"}, "2026-09-29 22:30", "judge_at 非法 ⇒ now+30min"),
        # [R74] 真实延长态：judge_at 停在过去、extend_until 在未来 ⇒ 应排到**下次判定之后**
        # （而不是退化成本例第 2 条的 now+30min 空转轮询）
        ({"judge_at": "2026-09-29T13:48:11+00:00",
          "extend_until": "2026-09-30T02:58:11+00:00"}, "2026-09-30 11:08",
         "[R74] judge_at 已过期但 extend_until=10:58L ⇒ 改期到 11:08L（对准下次判定）"),
        ({"judge_at": "2026-09-30T02:58:11+00:00",
          "extend_until": "2026-09-29T00:00:00+00:00"}, "2026-09-30 11:08",
         "[R74] 两字段都在但 extend_until 更早 ⇒ 取较晚者（judge_at）"),
    ]
    for st, want, why in cases:
        got = _defer_target(st, now=now).strftime("%Y-%m-%d %H:%M")
        ok = got == want
        fails += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} want={want} got={got} | {why}")
    print("=" * 88)
    if fails:
        print(f"✗ {fails} 项不符")
        return 1
    print("✓ 全部符合预期：③ 只在 ② 终局后才推进，否则改期等待（单变量隔离成立），"
          "且改期目标永不过期。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
