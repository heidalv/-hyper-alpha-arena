# -*- coding: utf-8 -*-
"""1) 当前实例(16:43后)日志尾部 FactorEvo 扫描  2) build_factor_card 实测"""
import os, io, sys, datetime, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = r"d:\001Alpha\Hyper-Alpha-Arena"
LOGS = os.path.join(BASE, "logs")

print("===== 1) backend.log / backend_restart_stdout.log 尾部 FactorEvo =====")
for name in ("backend.log", "backend.log.1", "backend_restart_stdout.log", "backend.out.final.log"):
    p = os.path.join(LOGS, name)
    if not os.path.isfile(p):
        continue
    print("")
    print("---", name, "---")
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        # 只取含 FactorEvo/evolution/factor 的最后 40 行
        keep = [l for l in lines if ("FactorEvo" in l or "factor_evolution" in l.lower() or "evolution_scheduler" in l)]
        for l in keep[-40:]:
            print(l.strip()[:260])
    except Exception as e:
        print("读取失败:", e)

print("")
print("===== 2) build_factor_card 实测（判断 card=0 根因） =====")
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "backend"))
try:
    import pandas as pd
    import numpy as np
    from backend.services.data_center import data_center
    from backend.services.factor_engine.expr.parser import parse
    from backend.services.factor_engine.factor_card import build_factor_card

    result = data_center.get_klines("BTC", "4h", count=2000)
    df = result.to_dataframe()
    print("取数 BTC/4h:", len(df), "根")

    ast = {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]}
    expr = parse(ast)
    dfs = {"BTC": df}
    try:
        card = build_factor_card(factor_id=expr.expr_id, expr=expr, dfs=dfs, period="4h", horizon=5, source="test")
        print("build_factor_card OK, keys:", list(card.keys())[:5], " admission.passed:", card["admission"]["passed"])
        print("card 摘要: ic=", card["ic"], " quantile.sharpe=", card["quantile"].get("sharpe") if isinstance(card.get("quantile"), dict) else card.get("quantile"))
    except Exception as e:
        print("build_factor_card 抛异常:", repr(e))
        traceback.print_exc()

    # 再试 _log_evolution 模拟落库（JSON 序列化路径）
    try:
        from backend.services.evolution.factor_evolution_loop import _log_evolution
        card2 = build_factor_card(factor_id=expr.expr_id, expr=expr, dfs=dfs, period="4h", horizon=5, source="test")
        import json
        s = json.dumps(card2, ensure_ascii=False)
        print("card JSON 序列化 OK,", len(s), "字符")
        _log_evolution(expr.expr_id, "card", expr_ast=ast, source="test", action="card_generated", metrics={"card": card2, "net_ic": 0.01})
        print("_log_evolution 落库调用完成（若 DB 能查到即成功）")
    except Exception as e:
        print("落库路径异常:", repr(e))
        traceback.print_exc()
except Exception as e:
    print("环境初始化失败:", repr(e))
    traceback.print_exc()
