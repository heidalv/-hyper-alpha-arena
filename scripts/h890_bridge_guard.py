# -*- coding: utf-8 -*-
"""[h890] 桥检守护:每小时自动核对并恢复被并行方改动的关键项。

并行协作的历史(10-05 ~ 10-07 已发生):
  · 账户余额被反复重置($9,977→$150→$10k→$70k→$150→$300…)
  · worker 被重启且丢失关键 env(MM_AF_LEG_CAP_PCT / MM_HOLD_HARD_EXIT_SEC)
  · "单笔 ≤ 权益 5%"上限三度被改写/默认关闭 ⇒ 巨腿 $45k~$67k 三次
本脚本按小时跑(计划任务),自动:
  ① 余额 < $1000 ⇒ 恢复 $10,000(车道名义有意义的底线)
  ② 关键 env 缺失 ⇒ 打告警日志(不动对方的 worker,只记录)
  ③ 三道上限被改 ⇒ 打告警日志
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

guard_log = ROOT / "logs" / "mm_bridge_guard.log"


def log(msg: str) -> None:
    line = f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(guard_log, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ① 余额:**只读,绝不修改**。
# 用户 10-07 13:2x 明确反对:"为什么要随意更改账户金额" —— 对,越权了。
# 之前"<1000 自动恢复 10000"已移除;金额归账户管理者(对方/用户)管。
_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
import psycopg  # noqa: E402

with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute("SELECT total_equity, updated_at FROM arbitrage_paper_accounts"
                " WHERE id=101")
    r = cur.fetchone()
    if not r:
        log("账户 101 不存在,跳过")
    else:
        log(f"余额 {float(r[0] or 0):.0f}(只读,不修改)")

# ② 三道上限的代码核对
af = (ROOT / "backend/services/market_maker/active_flow.py").read_text(encoding="utf-8")
fr = (ROOT / "backend/services/market_maker/flow_rules.py").read_text(encoding="utf-8")
if "MM_AF_LEG_CAP_PCT" not in af:
    log("⚠ 单笔上限(MM_AF_LEG_CAP_PCT)逻辑已不在 active_flow.py!")
if "STOP_WIDE_MULT" not in fr:
    log("⚠ 止损×2(STOP_WIDE_MULT)已不在 flow_rules.py!")
if "max_gross_notional_ratio" not in af:
    log("⚠ 总敞口闸(max_gross_notional_ratio)已不在 active_flow.py!")

# ③ 巨腿检测(近 1h 出现 >3× 权益的单腿 ⇒ 告警)
with psycopg.connect(h.read_env_dsn(), autocommit=True) as c, c.cursor() as cur:
    cur.execute("SELECT COALESCE(total_equity,10000) FROM arbitrage_paper_accounts"
                " WHERE id=101")
    eq = float(cur.fetchone()[0] or 10000)
    cur.execute("SELECT max(notional) FROM lane_ledger WHERE lane_id='mm_asterdex'"
                " AND event='fill' AND ts > now() - interval '1 hour'")
    mx = float(cur.fetchone()[0] or 0)
    if mx > eq * 0.5:
        log(f"⚠ 近 1h 最大单腿 {mx:.0f} > 权益 50%({eq*0.5:.0f}) —— 上限疑似失效")
    else:
        log(f"近 1h 最大单腿 {mx:.0f}(≤ 权益 50%)✓")
