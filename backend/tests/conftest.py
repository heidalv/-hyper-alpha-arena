"""
Test fixtures for Hyper Alpha Arena
"""
import os
import sys
import pytest
from unittest.mock import MagicMock, patch

# Add backend to path
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)
# [2026-09-02] 仓库根也入 path：测试统一用 `from backend.xxx import ...` 绝对导入，
# 只插 backend/ 会让 `import backend` 失败（ModuleNotFoundError: No module named
# 'backend'）。此前各测试文件自行 sys.path.insert 到仓库根来绕过，漏插的文件
# （如 test_admin_bootstrap.py）就永久收集失败、被误判为"引用已删除模块"。
_REPO_ROOT = os.path.dirname(_BACKEND_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# [2026-09-02 全量测试连锁 ERROR 根治] 子进程输出强制 UTF-8。
# 现象：test_gp_miner 的 loky 并行用例之后，字母序其后的 140 个用例在 setup/
# teardown 一律 ``UnicodeDecodeError: 'utf-8' codec can't decode byte 0xca in
# position 17``，栈顶是 contextlib.py:144（pytest 自己的 item_capture 上下文管理器，
# 源码显示 ???）。机制：Windows 中文系统上 loky/joblib 派生的 worker 子进程 stdout
# 非 tty，按区域编码 cp936 写中文日志 → 字节进入 pytest 的 fd 级捕获缓冲 →
# 之后每个用例读取缓冲都 utf-8 解码失败（position 恒为 17：同一段字节一直卡着）。
# 0xca 正是 GBK 常见首字节（"是/时/失…"）。这不是 140 个 bug，是 1 个编码污染。
# 修法：主进程在收集阶段就把两项环境变量放进 os.environ，所有派生子进程继承，
# 输出改为 UTF-8。用 setdefault 不覆盖用户显式设置。
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
# [2026-09-03 审查修正 C] 跨轮假设登记簿（DSR 分母）默认落在 <repo>/data/，
# 测试里跑 _promote_factors 会往生产登记簿里写测试假设、抬高线上 n_trials。
# 统一指到测试进程私有的临时文件（用户显式设置时不覆盖）。
import tempfile as _tempfile  # noqa: E402
os.environ.setdefault(
    "FACTOR_TRIALS_REGISTRY_PATH",
    os.path.join(_tempfile.gettempdir(), f"haa_trials_registry_test_{os.getpid()}.json"),
)


@pytest.fixture(autouse=True)
def _no_real_llm_calls(monkeypatch):
    """禁止单元测试发真实 LLM 请求（自动应用于所有用例）。

    [2026-09-02 A2 挂死根治] 测试套件此前跑到 53% 就永久挂死，两次复现同一位置。
    pytest-timeout 的线程栈转储把真凶抓了出来：
    ``test_mcts_miner`` → ``_mine_candidates`` → ``CodegenCritic.generate_and_audit``
    → ``call_llm_api_sync`` → 真实 HTTPS 请求，阻塞在 SSL recv 上永不返回。

    起因是测试进程会加载仓库 ``.env``，而生产配置里 ``FACTOR_GP_LLM_WARM_START=1``
    （代码默认是 "0"）—— 单元测试就这么继承生产开关跑起了 LLM 热启动。同类问题
    在 WFO 门禁开关上也出现过。

    这里选择**抛异常**而不是返回假响应：让意外的出网立即暴露在调用栈上，而不是
    静默喂给被测代码一份编造的数据。业务侧已有降级路径的（如上述热启动）会捕获
    它并走 fallback，行为与"LLM 不可用"完全一致；确实要测 LLM 交互的用例，在自己
    的用例内 patch 即可覆盖本 fixture（用例内的 patch 后应用、优先级更高）。
    """
    def _refuse(*args, **kwargs):
        raise RuntimeError(
            "单元测试禁止真实 LLM 网络调用（conftest._no_real_llm_calls）；"
            "请在用例内 patch 掉相关调用"
        )

    # 替身保留原函数签名/文档（functools.wraps 设置 __wrapped__），否则
    # inspect.signature(call_llm_api_sync) 只看到 (*args, **kwargs)，
    # 契约类用例（如 test_timeout_param_passed_to_client）会被替身误伤。
    try:
        import functools
        from backend.services import llm_config_service as _llm_mod
        _orig = getattr(_llm_mod, "call_llm_api_sync", None)
        if callable(_orig):
            _refuse = functools.wraps(_orig)(_refuse)
    except Exception:
        pass

    monkeypatch.setattr(
        "backend.services.llm_config_service.call_llm_api_sync",
        _refuse,
        raising=False,
    )


@pytest.fixture(autouse=True)
def _isolate_request_identity():
    """每个用例结束后把租户/管理员 ContextVar 恢复到用例开始前的值。

    [2026-09-03] 第 9 轮全量单测里 test_e2e_learning_chain 5 例集体 ForeignKeyViolation
    （account_id=1 不存在），单跑全过。追出来是**身份跨用例泄漏**：
      - test_background_loops_rls 里 ``tenant_id_var.set(42)`` 后不恢复；
      - test_place_order_gated / test_scalp_gated 调到 PaperTradingEngine，其
        ``_set_tenant_from_account`` 会把 ``tenant_id_var`` 永久设成账户属主（生产里是
        后台线程的既定设计），测试账户属主是 1 → 主线程此后一直是租户 1。
    于是后面的 e2e 用例以租户 1 查 ai_strategies，只看得到 0020 迁移前泄漏成租户 1 的
    605 行；迁移把它们改回真实属主后，租户 1 什么都看不到 → 退回 account_id=1 兜底 → FK 炸。
    症状随数据状态漂移，属典型的用例间隐性耦合；这里在 conftest 统一兜底，比逐个用例
    补 finally 可靠。ContextVar 在 pytest 主线程内 set/restore 语义直接可用。
    """
    from backend.core.tenant import tenant_id_var, is_admin_var
    prev_tid, prev_admin = tenant_id_var.get(), is_admin_var.get()
    try:
        yield
    finally:
        tenant_id_var.set(prev_tid)
        is_admin_var.set(prev_admin)


@pytest.fixture
def mock_db_session():
    """Mock database session"""
    session = MagicMock()
    return session


@pytest.fixture
def mock_user():
    """Mock user object"""
    user = MagicMock()
    user.id = 1
    user.username = "test_user"
    user.email = "test@example.com"
    user.is_active = "true"
    return user


@pytest.fixture
def mock_account():
    """Mock account object"""
    account = MagicMock()
    account.id = 1
    account.user_id = 1
    account.name = "Test Account"
    account.account_type = "AI"
    account.trading_mode = "paper"
    account.current_cash = 10000.00
    account.initial_capital = 10000.00
    return account


@pytest.fixture
def sample_trade_data():
    """Sample trade data for testing"""
    return {
        "symbol": "BTC",
        "name": "Bitcoin",
        "side": "BUY",
        "price": 50000.00,
        "quantity": 0.01,
        "commission": 0.5,
    }


@pytest.fixture
def sample_order_data():
    """Sample order data for testing"""
    return {
        "symbol": "BTC",
        "name": "Bitcoin",
        "side": "BUY",
        "order_type": "MARKET",
        "price": 50000.00,
        "quantity": 1,
    }


@pytest.fixture(autouse=True)
def mock_env_variables():
    """Mock environment variables for testing"""
    with patch.dict(os.environ, {
        "DATABASE_URL": "sqlite:///./test.db",
        "SNAPSHOT_DATABASE_URL": "sqlite:///./test_snapshots.db",
        "BINANCE_ENCRYPTION_KEY": "test_key_for_testing_purposes_only",
    }):
        yield
