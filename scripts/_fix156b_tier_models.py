# -*- coding: utf-8 -*-
"""[轮156] 把 tier 专用列也改成规范名：`model`（quick）与 `model_deep`（deep）。

`_resolve_tier_model`：tier=='deep' 时取 `model_deep`，否则取 `model`。
所以只改 `model` 不够 —— deep 档（主脑/辩论/深度分析都走 deep）仍会拿到旧名。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text  # noqa: E402

from backend.database.connection import engine  # noqa: E402

with engine.connect() as c:
    cols = [r[0] for r in c.execute(text(
        "select column_name from information_schema.columns where table_name='llm_configurations'"))]
    print("列里有 model_deep 吗：", "model_deep" in cols)
    for r in c.execute(text(
            "select id, name, model, model_deep from llm_configurations "
            "where provider='deepseek' order by id")):
        print(f"   改前 id={r[0]} {r[1][:30]:<30} model={r[2]!r} model_deep={r[3]!r}")

with engine.begin() as c:
    n = c.execute(text(
        "update llm_configurations set model='deepseek-flash', model_deep='deepseek-flash' "
        "where provider='deepseek' and id=17")).rowcount
    print(f"\n已更新 {n} 行（id=17：model 与 model_deep 均 → deepseek-flash）")
    for r in c.execute(text(
            "select id, name, model, model_deep from llm_configurations "
            "where provider='deepseek' order by id")):
        print(f"   改后 id={r[0]} {r[1][:30]:<30} model={r[2]!r} model_deep={r[3]!r}")

print("\n解析结果复核（quick / deep 两档）：")
from backend.services.coin_select_platform_service import resolve_admin_tenant_id  # noqa: E402
from backend.services.llm_config_service import get_llm_config_for_usage  # noqa: E402

tid = resolve_admin_tenant_id()
for tier in ("quick", "deep"):
    cfg = get_llm_config_for_usage("deep_analysis", tenant_id=tid, tier=tier)
    print(f"   tier={tier:<6} → id={getattr(cfg,'id',None)} model={getattr(cfg,'model',None)}")
