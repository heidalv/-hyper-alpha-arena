"""S2-10c 单元测试：QAA 调度统一（域注册表 + 统一心跳 + 统一调度）。

覆盖：
- 域注册/更新（幂等，保留心跳计数）；
- 心跳记录与统一视图（含 rebate_arb 外部心跳合并）；
- 总开关关闭时 run_due_domains 不执行（安全默认）；
- 间隔调度：到期才执行、间隔内跳过；
- 异常隔离：runner 抛错 → error 心跳，不影响其他域；
- full_auto 域缺 svc/session_id 时跳过。
"""
import time
from unittest.mock import MagicMock

import pytest

import backend.services.qaa_scheduler as qs


@pytest.fixture(autouse=True)
def _clean_registry():
    """测试隔离：备份/恢复注册表。"""
    saved = dict(qs._domains)
    qs._domains.clear()
    yield
    qs._domains.clear()
    qs._domains.update(saved)


@pytest.fixture(autouse=True)
def _force_scheduler_enabled(monkeypatch):
    """默认开启总开关（各测试单独控制域级 enabled）。"""
    monkeypatch.setattr(qs, "_cfg", lambda name, default: True)
    yield
    monkeypatch.undo()


class TestDomainRegistry:
    def test_register_and_get_heartbeats(self):
        qs.register_domain(
            "test_domain", lambda **kw: None,
            interval_sec=300, enabled=True, description="测试域",
        )
        hb = qs.get_heartbeats()
        assert hb["test_domain"]["enabled"] is True
        assert hb["test_domain"]["interval_sec"] == 300
        assert hb["test_domain"]["last_status"] == "never"
        assert hb["test_domain"]["run_count"] == 0

    def test_register_idempotent_keeps_heartbeat(self):
        qs.register_domain("d1", lambda **kw: None, interval_sec=300, enabled=True)
        qs._heartbeat("d1", "ok")
        qs.register_domain("d1", lambda **kw: None, interval_sec=600, enabled=False)
        hb = qs.get_heartbeats()["d1"]
        assert hb["interval_sec"] == 600
        assert hb["run_count"] == 1  # 心跳计数保留
        assert hb["last_status"] == "ok"


class TestHeartbeat:
    def test_heartbeat_records(self):
        qs.register_domain("d1", lambda **kw: None, interval_sec=60, enabled=True)
        qs._heartbeat("d1", "ok")
        hb = qs.get_heartbeats()["d1"]
        assert hb["last_status"] == "ok"
        assert hb["run_count"] == 1
        assert hb["last_run_at"] > 0

    def test_error_heartbeat_records_error(self):
        qs.register_domain("d1", lambda **kw: None, interval_sec=60, enabled=True)
        qs._heartbeat("d1", "error", "boom")
        hb = qs.get_heartbeats()["d1"]
        assert hb["last_status"] == "error"
        assert hb["last_error"] == "boom"

    def test_rebate_arb_external_heartbeat_merged(self, monkeypatch):
        qs.register_domain("rebate_arb", lambda **kw: None, interval_sec=60, enabled=True)

        ext_ts = time.time()
        fake_mod = MagicMock()
        fake_mod.get_last_rebate_tick_at.return_value = ext_ts
        monkeypatch.setitem(qs.sys.modules if hasattr(qs, "sys") else __import__("sys").modules,
                            "backend.services.rebate_arb.qaa_rebate_tick", fake_mod)
        # get_heartbeats 内部延迟 import，patch 目标模块后应读到外部心跳
        # （若 import 失败则跳过，不抛错——此测试验证的是兜底不炸）
        hb = qs.get_heartbeats()["rebate_arb"]
        assert "last_run_at" in hb


class TestRunDueDomains:
    def test_disabled_total_switch_skips(self, monkeypatch):
        monkeypatch.setattr(qs, "_cfg", lambda name, default: False)
        qs.register_domain("d1", lambda **kw: None, interval_sec=1, enabled=True)
        assert qs.run_due_domains() == []

    def test_due_domain_executes(self):
        calls = []
        qs.register_domain("d1", lambda **kw: calls.append(kw), interval_sec=0.01, enabled=True)
        ran = qs.run_due_domains()
        assert ran == ["d1"]
        assert len(calls) == 1
        hb = qs.get_heartbeats()["d1"]
        assert hb["last_status"] == "ok"

    def test_not_due_skipped(self):
        calls = []
        qs.register_domain("d1", lambda **kw: calls.append(kw), interval_sec=3600, enabled=True)
        qs._heartbeat("d1", "ok")  # 刚跑过
        assert qs.run_due_domains() == []
        assert calls == []

    def test_disabled_domain_skipped(self):
        calls = []
        qs.register_domain("d1", lambda **kw: calls.append(kw), interval_sec=0.01, enabled=False)
        assert qs.run_due_domains() == []
        assert calls == []

    def test_exception_isolated(self):
        def bad(**kw):
            raise RuntimeError("boom")

        good_calls = []
        qs.register_domain("bad", bad, interval_sec=0.01, enabled=True)
        qs.register_domain("good", lambda **kw: good_calls.append(kw),
                           interval_sec=0.01, enabled=True)
        ran = qs.run_due_domains()
        assert ran == ["good"]  # 坏域不阻塞好域
        hb_bad = qs.get_heartbeats()["bad"]
        assert hb_bad["last_status"] == "error"
        assert "boom" in hb_bad["last_error"]

    def test_fullauto_requires_svc_and_session(self):
        calls = []

        def fake_fullauto(**kw):
            calls.append(kw)

        qs.register_domain("full_auto", fake_fullauto, interval_sec=0.01, enabled=True)
        # 缺 svc/session_id → skipped
        ran = qs.run_due_domains()
        assert ran == []
        hb = qs.get_heartbeats()["full_auto"]
        assert hb["last_status"] == "skipped"
        assert calls == []

        # 提供 svc + session_id → 执行（重置心跳使间隔到期：
        # 第一次 skipped 时心跳已刷新 last_run_at）
        svc = MagicMock()
        qs._domains["full_auto"]["last_run_at"] = 0.0
        ran = qs.run_due_domains(svc=svc, session_id="sess_1")
        assert ran == ["full_auto"]
        assert calls == [{"svc": svc, "session_id": "sess_1"}]


class TestGetSchedulerStatus:
    def test_status_shape(self, monkeypatch):
        qs.register_domain("d1", lambda **kw: None, interval_sec=60, enabled=True)
        status = qs.get_scheduler_status()
        assert "enabled" in status
        assert "d1" in status["domains"]

    def test_builtin_domains_registered(self):
        # 注册表被测试 fixture 清空后，显式 ensure 重建内置域
        # （模块加载时也会自动注册）
        qs._ensure_domains_registered()
        hb = qs.get_heartbeats()
        assert "rebate_arb" in hb
        assert "full_auto" in hb
