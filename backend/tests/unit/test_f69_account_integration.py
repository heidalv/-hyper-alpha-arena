# -*- coding: utf-8 -*-
"""[F69] 做市策略并入统一模拟账户的单测。

覆盖：策略规格注册、按策略分配资金、配额比例重算、成交入账（含零成本留痕）、
统一账户视图聚合、车道绑定字段。
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.services.rebate_arb.arbitrage_paper_account_service import (  # noqa: E402
    ArbitragePaperAccountService,
)
from backend.services.rebate_arb.strategy_runtime_registry import (  # noqa: E402
    get_runtime_spec,
    runtime_spec_to_dict,
)


class TestStrategySpec:
    def test_mm_registered(self):
        spec = get_runtime_spec("MM")
        assert spec is not None
        assert spec.required_exchanges == ("asterdex",)
        assert spec.paper_auto_executable is True
        assert spec.requires_trader_profile is False

    def test_mm_dict_shape(self):
        d = runtime_spec_to_dict("MM")
        assert d["strategy_id"] == "MM"
        assert d["min_equity_usd"] > 0
        assert "asterdex" in d["summary"] or "Aster" in d["summary"]

    def test_case_insensitive_lookup(self):
        assert get_runtime_spec("mm") is not None


class TestAccountIntegration:
    """真实 DB 往返（失败则跳过）。"""

    @staticmethod
    def _svc():
        return ArbitragePaperAccountService()

    def _make_account(self, svc):
        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal

        name = f"test_f69_{uuid4().hex[:8]}"
        with system_identity():
            with SessionLocal() as db:
                acc = svc.create_account(db, name=name, total_equity=300.0,
                                         preset_id="single_asterdex_s8")
                return int(acc["id"])

    def _purge(self, svc, account_id):
        try:
            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    try:
                        svc.stop_paper_verification(db, account_id)
                    except Exception:
                        pass
                    svc.delete_account(db, account_id)
        except Exception:
            pass

    def test_allocate_capital_and_limits(self):
        svc = self._svc()
        acct = self._make_account(svc)
        if not acct:
            pytest.skip("DB 不可用")
        try:
            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    res = svc.allocate_strategy_capital(
                        db, acct, "asterdex", 5000.0,
                        strategy_type="MM", strategy_limit_pct=0.9,
                        note="pytest",
                    )
            assert res["allocated_usd"] == pytest.approx(300.0 + 5000.0, abs=0.01)
            assert res["strategy_type"] == "MM"
            with system_identity():
                with SessionLocal() as db:
                    acct_d = svc.get_account(db, acct)
            ast = acct_d["exchange_balances"]["asterdex"]
            assert ast["strategy_limits"].get("MM") == pytest.approx(0.9)
            assert acct_d["total_equity"] == pytest.approx(5300.0, abs=0.01)
        finally:
            self._purge(svc, acct)

    def test_set_strategy_limits_preserves_absolute_budgets(self):
        """追加资金后重算比例，其它策略的**绝对预算**不能变。"""
        svc = self._svc()
        acct = self._make_account(svc)
        try:
            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    before = svc.get_account(db, acct)["exchange_balances"]["asterdex"]
                    old_alloc = float(before["allocated_usd"])
                    old_limits = dict(before["strategy_limits"] or {})
                    old_budgets = {k: old_alloc * float(v)
                                   for k, v in old_limits.items() if float(v) > 0}
                    svc.allocate_strategy_capital(db, acct, "asterdex", 5000.0,
                                                  strategy_type="MM",
                                                  strategy_limit_pct=0.9434)
                    after = svc.get_account(db, acct)["exchange_balances"]["asterdex"]
                    new_alloc = float(after["allocated_usd"])
                    rescale = {k: round(v / new_alloc, 6) for k, v in old_budgets.items()}
                    rescale["MM"] = 0.9434
                    svc.set_strategy_limits(db, acct, "asterdex", rescale)
                    final = svc.get_account(db, acct)["exchange_balances"]["asterdex"]
            new_limits = final["strategy_limits"]
            for k, budget in old_budgets.items():
                assert new_alloc * float(new_limits[k]) == pytest.approx(budget, abs=0.5), k
            # 比例之和 ≤ 1（允许四舍五入的 1e-3 误差）
            assert sum(float(v) for v in new_limits.values()) <= 1.0 + 1e-3
        finally:
            self._purge(svc, acct)

    def test_record_fill_creates_strategy_ledger_rows(self):
        svc = self._svc()
        acct = self._make_account(svc)
        try:
            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    svc.allocate_strategy_capital(db, acct, "asterdex", 5000.0,
                                                  strategy_type="MM",
                                                  strategy_limit_pct=0.9)
                    # 一笔有盈亏的成交：手续费 0（Aster maker 0%）、价差 +0.5 美元
                    svc.record_paper_leg_fill(
                        db, acct, "asterdex", position_id="mm:BTC",
                        strategy_type="MM", phase="fill", fee_paid=0.0,
                        rebate_received=0.0, slippage_cost=0.0, pnl_delta=0.5,
                        note="pytest", force_log=True,
                    )
                    # 一笔零成本成交：必须留痕但不改余额
                    svc.record_paper_leg_fill(
                        db, acct, "asterdex", position_id="mm:ETH",
                        strategy_type="MM", phase="fill", fee_paid=0.0,
                        rebate_received=0.0, slippage_cost=0.0, pnl_delta=0.0,
                        note="pytest zero", force_log=True,
                    )
                    db.commit()
            from sqlalchemy import text

            with system_identity():
                with SessionLocal() as db:
                    rows = db.execute(text(
                        "SELECT action, COUNT(*) n, SUM(amount_usd) amt"
                        " FROM arbitrage_paper_ledgers WHERE account_id=:a"
                        " AND strategy_type='MM' GROUP BY action"
                    ), {"a": acct}).mappings().all()
            by_action = {r["action"]: (int(r["n"]), float(r["amt"] or 0.0)) for r in rows}
            assert "paper_pnl" in by_action and by_action["paper_pnl"][1] == pytest.approx(0.5)
            assert "paper_fill" in by_action and by_action["paper_fill"][0] == 1
        finally:
            self._purge(svc, acct)

    def test_unified_view_shape(self):
        from backend.api import trading_routes as T

        res = T.account_unified(days=7.0)
        assert set(res) >= {"account", "exchanges", "strategies", "positions",
                            "exposure", "as_of"}
        assert res["account"]["account_id"] > 0
        assert isinstance(res["strategies"], list)
        assert set(res["positions"]) >= {"mm", "count"}
        for ex in res["exchanges"]:
            assert set(ex) >= {"exchange", "allocated_usd", "available_usd",
                               "strategy_limits", "strategy_budgets"}

    def test_strategy_limits_parse_contract(self):
        """回归：`strategy_limits_json` 必须被解析成 dict（曾因漏 import json 恒为空）。

        2026-09-14 改为**数据无关**契约：直接锁定解析入口 + 视图内配额一致性。
        旧版断言「asterdex 账户里必须存在 legacy 策略配额」——做市专用账户
        (#101) 清理旧 S3/S8/S7 配额后（否则套利中心显示 $5000 幽灵仓位）该前提
        已不成立，但「解析不得恒为空」的回归意图必须保留。
        """
        from backend.api import trading_routes as T

        assert T._parse_strategy_limits('{"S3": 0.5, "MM": 1.0}') == {"S3": 0.5, "MM": 1.0}
        assert T._parse_strategy_limits({"S8": 0.25}) == {"S8": 0.25}
        assert T._parse_strategy_limits(None) == {}
        assert T._parse_strategy_limits("") == {}
        assert T._parse_strategy_limits("not-json") == {}
        assert T._parse_strategy_limits("[1, 2]") == {}
        assert T._parse_strategy_limits('{"bad": "x"}') == {}, "非法比例应跳过而非抛出"

        res = T.account_unified(days=30.0)
        assert [e for e in res["exchanges"]], "视图必须返回交易所行"
        for ex in res["exchanges"]:
            for sid, pct in ex["strategy_limits"].items():
                assert 0.0 < float(pct) <= 1.0, sid
                assert ex["strategy_budgets"][sid] == pytest.approx(
                    ex["allocated_usd"] * float(pct), abs=0.05)

    def test_unified_view_auto_selects_account(self):
        """不传 account_id 时按做市车道绑定自动定位（前端不必硬编码 id）。"""
        from backend.api import trading_routes as T

        res = T.account_unified()
        assert res["account"]["account_id"] > 0
        assert res["account"]["name"]

    def test_unified_view_sees_mm_strategy(self):
        """做市策略必须在统一账户视图里可见，且资金口径非 0。

        2026-09-14：MM 专用账户不再有 legacy `strategy_capital_alloc` 流水，
        `capital_usd` 由后端回落到「账户内已分配额度」（$300 全权益腿），
        否则前端策略表会显示成 0 资金在跑。
        """
        from backend.api import trading_routes as T

        res = T.account_unified(days=30.0)
        kinds = {s["strategy_type"] for s in res["strategies"]}
        # 车道已绑定统一账户后，MM 必须出现（资金分配流水即为证据）
        assert "MM" in kinds, kinds
        mm = next(s for s in res["strategies"] if s["strategy_type"] == "MM")
        assert mm["capital_usd"] > 0
        # 资金口径必须与账户已分配额度一致（不是凭空造数）
        alloc = sum(float(e["allocated_usd"] or 0.0) for e in res["exchanges"])
        assert mm["capital_usd"] <= alloc + 0.05


class TestLaneBinding:
    def test_lane_meta_points_to_unified_account(self):
        from backend.services import lane_registry as reg

        lane = reg.get_lane("mm_asterdex")
        if not lane:
            pytest.skip("车道不存在")
        meta = lane.get("meta") or {}
        assert meta.get("strategy_type") == "MM"
        assert int(meta.get("paper_account_id") or 0) > 0
        assert (meta.get("arbitrage_config") or {}).get("venue") == "asterdex"

    def test_runner_uses_bound_account_and_strategy(self):
        from backend.services.market_maker import runner

        r = runner.get_runner("mm_asterdex")
        if r is None:
            pytest.skip("runner 不可用")
        assert r.strategy_type == "MM"
        assert r.account_id and int(r.account_id) > 0
