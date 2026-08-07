"""Paper 交易所配额与曝光测试"""

from backend.services.rebate_arb.arbitrage_paper_account_service import ArbitragePaperAccountService


def test_compute_max_open_size_respects_strategy_sub_cap():
    svc = ArbitragePaperAccountService()

    class _Bal:
        allocated_usd = 135.0
        available_usd = 134.0
        strategy_limits = {"S8": 0.45}

    class _Q:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return _Bal()

    class _Db:
        def query(self, model):
            return _Q()

    svc.get_active_paper_exposure = lambda ex, sid: 45.0  # type: ignore

    info = svc.compute_max_open_size(_Db(), 1, "asterdex", "S8", 45.0)  # type: ignore
    assert info["strategy_cap_usd"] == 60.75
    assert info["exposure_usd"] == 45.0
    assert info["allowed_usd"] == 15.75
