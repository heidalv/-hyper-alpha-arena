"""轮134 验证（二）：真实 KlineAnalyst → 落库 → 六域 kline_deep 消费，端到端一次跑通。

这不依赖"会话恢复"（那条路径只在 resume 时触发），而是直接跑周期任务里的同一段代码，
证明：真实 LLM 产物能落库、且被 scorer 以 structured 口径读到。
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.services.analysts.kline_deep_store import persist_report  # noqa: E402
from backend.services.analysts.service import default_symbols  # noqa: E402
from backend.services.trading_analysts import KlineAnalyst  # noqa: E402

syms = [s.upper() for s in default_symbols()[:3]]
print(f"universe 前 3 = {syms}")
rep = KlineAnalyst().analyze(syms)
print(f"AnalystReport: summary={str(getattr(rep, 'summary', ''))[:80]!r} "
      f"recommendation={str(getattr(rep, 'recommendation', ''))[:60]!r} "
      f"signals={len(getattr(rep, 'signals', []) or [])}")
for s in (getattr(rep, "signals", None) or [])[:4]:
    print(f"   {s.get('symbol'):<8} signal={s.get('signal'):<8} score={s.get('score')} "
          f"detail={str(s.get('detail'))[:60]}")

n = persist_report(rep)
print(f"\n落库 = {n} 条")
if n == 0:
    print("⚠️ 落库 0 条 —— 检查 KLINE_DEEP_PERSIST_ENABLED 与 analyze() 返回结构")

from sqlalchemy import text  # noqa: E402

from backend.database.connection import analytics_engine  # noqa: E402

with analytics_engine.connect() as c:
    rows = c.execute(text(
        "select symbol, period, created_at, left(analysis_result, 160) from kline_ai_analysis_logs "
        "where symbol not like 'ZZTEST%' order by id desc limit 4")).fetchall()
print("\n最近真实产物：")
for r in rows:
    print(f"   {r[0]:<8} {r[1]:<6} {str(r[2])[:19]} | {r[3]}")

from backend.services.analysts import scorers as SC  # noqa: E402

sigs = [s for s in SC.score_kline_deep(syms) if s.symbol in syms]
print("\nscorer 读到的 kline_deep 信号：")
for s in sigs:
    print(f"   {s.symbol:<8} score={s.score:+.3f} q={s.data_quality:<6} "
          f"parsed={s.evidence.get('parsed')} as_of={s.as_of} signal={s.evidence.get('signal')}")
from backend.services.analysts import contract_report  # noqa: E402

rep2 = contract_report()
print("\n契约报告 undelivered =", rep2["undelivered"])
print("kline_deep 交付 =", rep2["delivered"].get("kline_deep"))
