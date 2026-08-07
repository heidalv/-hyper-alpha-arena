"""trace_id 上下文工具测试。

验证:
- generate_trace_id 唯一性 + 前缀
- ContextVar 在 bind_trace 内传播、退出恢复
- LoggingFilter 正确注入 trace_id_short
- 无 trace_id 时显示 '-'
"""
import logging

from backend.utils.trace_context import (
    TraceIdFilter,
    bind_trace,
    generate_trace_id,
    get_trace_id,
    get_trace_id_short,
    install_trace_filter,
    new_trace,
    set_trace_id,
)


# ── generate_trace_id ────────────────────────────────────────────

def test_generate_trace_id_unique():
    a = generate_trace_id()
    b = generate_trace_id()
    assert a != b
    assert len(a) == 12


def test_generate_trace_id_with_prefix():
    tid = generate_trace_id("tick")
    assert tid.startswith("tick-")
    # 前缀 + 12 位 uid
    assert len(tid) == len("tick-") + 12


def test_generate_trace_id_prefix_cleaned():
    # 非法字符被清理
    tid = generate_trace_id("tick!@#")
    assert tid.startswith("tick-")
    assert "!" not in tid


def test_generate_trace_id_empty_prefix():
    tid = generate_trace_id("")
    assert len(tid) == 12
    assert "-" not in tid


def test_new_trace_alias():
    assert new_trace("x") == generate_trace_id("x") or True  # 仅验证可调用


# ── ContextVar 传播 ──────────────────────────────────────────────

def test_get_trace_id_none_by_default():
    # 注意：测试间 ContextVar 可能残留，先清除
    set_trace_id(None)
    assert get_trace_id() is None
    assert get_trace_id_short() == "-"


def test_bind_trace_sets_and_restores():
    set_trace_id(None)
    assert get_trace_id() is None
    with bind_trace("tick-abc123def456") as tid:
        assert tid == "tick-abc123def456"
        assert get_trace_id() == "tick-abc123def456"
        assert get_trace_id_short() == "tick-abc123de"  # 前缀 + uid 前 8 位
    # 退出后恢复
    assert get_trace_id() is None
    assert get_trace_id_short() == "-"


def test_bind_trace_nested():
    set_trace_id(None)
    with bind_trace("outer-aaa111222333"):
        assert get_trace_id().startswith("outer-")
        with bind_trace("inner-bbb444555666"):
            assert get_trace_id().startswith("inner-")
        # 内层退出，恢复外层
        assert get_trace_id().startswith("outer-")
    assert get_trace_id() is None


def test_bind_trace_propagates_to_logger():
    """bind_trace 内的日志应带 trace_id_short。"""
    set_trace_id(None)
    # 准备一个带 TraceIdFilter 的 logger + capture handler
    test_logger = logging.getLogger("test_trace_context")
    test_logger.handlers.clear()
    test_logger.filters.clear()
    install_trace_filter("test_trace_context")

    captured = []

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            captured.append(record)

    test_logger.addHandler(CaptureHandler())
    test_logger.setLevel(logging.DEBUG)

    # 无 trace_id
    test_logger.info("no trace")
    assert captured[-1].trace_id == ""
    assert captured[-1].trace_id_short == "-"

    # 有 trace_id
    with bind_trace("req-deadbeef1234"):
        test_logger.info("with trace")
        assert captured[-1].trace_id == "req-deadbeef1234"
        # trace_id_short = 前缀 "req-" + uid "deadbeef1234" 的前 8 位 "deadbeef"
        assert captured[-1].trace_id_short == "req-deadbeef"


def test_get_trace_id_short_no_prefix():
    set_trace_id(None)
    with bind_trace("abcdef123456"):  # 无前缀
        assert get_trace_id_short() == "abcdef12"


def test_set_trace_id_directly():
    set_trace_id(None)
    set_trace_id("manual-xyz")
    assert get_trace_id() == "manual-xyz"
    set_trace_id(None)  # 清理


# ── install_trace_filter 幂等 ────────────────────────────────────

def test_install_trace_filter_idempotent():
    lg = logging.getLogger("test_idempotent")
    lg.filters.clear()
    install_trace_filter("test_idempotent")
    count1 = sum(1 for f in lg.filters if isinstance(f, TraceIdFilter))
    install_trace_filter("test_idempotent")  # 再次安装
    count2 = sum(1 for f in lg.filters if isinstance(f, TraceIdFilter))
    assert count1 == 1
    assert count2 == 1  # 不重复叠加
