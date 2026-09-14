# -*- coding: utf-8 -*-
"""[F100] 资金池「车道占用 vs 历史账户」拆分契约。

现场：`/api/trading/capital/pool` 把旧返佣账户 #3（$5300）与做市账户 #101（$300）
直接相加成 **$5600**，而「风险与资金」页把它显示为"资金池合计权益"——看起来像做市
业务有 $5600 资金。这正是用户反复抱怨的「旧数据污染」在另一个页面的翻版。

修：接口按「是否被任何车道绑定」拆分（`meta.paper_account_id`），给出
`lane_bound_equity` / `unbound_equity`，每个账户带 `bound` / `bound_lanes`；
前端标题以**车道占用**为主、历史账户单列说明。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def test_capital_pool_splits_lane_bound_from_unbound():
    from backend.api import trading_routes as T

    res = T.capital_pool()
    assert set(res) >= {"items", "count", "total_equity",
                        "lane_bound_equity", "unbound_equity"}
    for it in res["items"]:
        assert "bound" in it and "bound_lanes" in it
    # 口径自洽：占用 + 未绑定 = 合计
    assert res["lane_bound_equity"] + res["unbound_equity"] == \
        __import__("pytest").approx(res["total_equity"], abs=0.02)
    # 做市账户必须被识别为"被车道绑定"
    mm = [i for i in res["items"] if i["account_id"] == 101]
    if mm:
        assert mm[0]["bound"] is True
        assert "mm_asterdex" in mm[0]["bound_lanes"]


def test_capital_pool_does_not_lose_legacy_account():
    """历史账户仍要列出（可审计），只是不计入车道占用。"""
    from backend.api import trading_routes as T

    res = T.capital_pool()
    ids = {i["account_id"] for i in res["items"]}
    if 3 not in ids:
        import pytest
        pytest.skip("旧账户 #3 已不存在")
    legacy = next(i for i in res["items"] if i["account_id"] == 3)
    assert legacy["bound"] is False
    assert legacy["bound_lanes"] == []
    assert res["unbound_equity"] >= legacy["total_equity"] - 0.01


def test_frontend_shows_lane_bound_first():
    """前端标题必须以「车道占用」为主，并单列历史账户（源码契约）。"""
    p = Path(__file__).resolve().parents[3] / "frontend-next/src/app/arbitrage/risk/page.tsx"
    src = p.read_text(encoding="utf-8")
    assert "lane_bound_equity" in src
    assert "历史账户" in src
    assert "车道绑定" in src
