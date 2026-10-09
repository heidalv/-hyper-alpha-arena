# -*- coding: utf-8 -*-
"""[F327 2026-09-21 · P2] 注册表权威模式回归测试。

# 为什么需要这个模式

P2（LLM 监控调参）要求**改参数不重启就生效**。但有 7 个键被 F280 的 env 白名单锁住：

    spread_mult / spread_mult_reduce / min_edge_frac / spread_cross_margin
    w_base_bp / min_width_bp / k_inv

`apply_env_param_overrides` 读 `os.getenv`（进程启动时冻结），并在**每次热采用**
（`_maybe_reload_meta`，60s 一次）之后重新覆盖 ⇒ **写注册表完全不生效**。

实测分叉（H187，2026-09-21 15:0x）：

    k_inv                注册表 0.5   实盘 1.0     <-- 注册表是**假的**
    w_base_bp            注册表 1.5   实盘 5.0     <-- 同上
    spread_mult          0.5          0.5         一致
    spread_mult_reduce   0.4          0.4         一致

⇒ 任何"读注册表做归因"的下游（LLM 诊断、守卫链、审计脚本）都在用**错误前提**。
这比"改了没生效"更危险：它让归因结论**方向相反**。

# 本测试固定什么

1. **默认关闭** ⇒ 既有部署行为零变更（F298 的 7 项契约原样通过）
2. 开启后**注册表赢**（env 不再执行覆盖）
3. 开启后**分叉仍然告警**（换成 F327 文案）—— 不许把可观测性一起删掉
4. `status()` 报 `param_authority`，让"谁在权威"从推断变成读数
"""
from __future__ import annotations

import logging

import pytest

from backend.services.market_maker.core import QuoteParams
from backend.services.market_maker.runner import apply_env_param_overrides

AUTH_ENV = "MM_REGISTRY_AUTHORITATIVE"

ALL_WHITELIST_ENVS = (
    "MM_SPREAD_MULT", "MM_SPREAD_MULT_REDUCE", "MM_MIN_EDGE_FRAC",
    "MM_SPREAD_CROSS_MARGIN", "MM_W_BASE_BP", "MM_MIN_WIDTH_BP", "MM_K_INV",
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """每条测试都从"注册表权威模式关闭"出发，避免相互污染。

    ⚠️ 必须显式删除 `MM_REGISTRY_AUTHORITATIVE`：一旦生产 `.env` 打开这个开关，
    它会被 `load_dotenv` 灌进 `os.environ` 并**泄漏进本测试进程**
    ⇒ `test_default_is_env_wins` 会假失败。这正是 F298 踩过的同一类坑。
    """
    monkeypatch.delenv(AUTH_ENV, raising=False)
    for e in ALL_WHITELIST_ENVS:
        monkeypatch.delenv(e, raising=False)
    yield


@pytest.mark.unit
def test_default_is_env_wins(monkeypatch):
    """默认（未设 MM_REGISTRY_AUTHORITATIVE）⇒ env 仍然赢，既有行为不变。"""
    monkeypatch.setenv("MM_SPREAD_MULT", "0.5")
    p = QuoteParams(spread_mult=0.9)
    assert apply_env_param_overrides(p).spread_mult == pytest.approx(0.5)


@pytest.mark.unit
def test_authoritative_makes_registry_win(monkeypatch):
    """**核心**：开启后 env 不再覆盖 ⇒ 注册表就是实盘值。"""
    monkeypatch.setenv(AUTH_ENV, "1")
    monkeypatch.setenv("MM_SPREAD_MULT", "0.5")
    p = QuoteParams(spread_mult=0.9)
    assert apply_env_param_overrides(p).spread_mult == pytest.approx(0.9)


@pytest.mark.unit
def test_authoritative_still_warns_on_divergence(monkeypatch, caplog):
    """开启后分叉**仍须告警**（文案换成 F327）——可观测性不许一起删掉。"""
    monkeypatch.setenv(AUTH_ENV, "1")
    monkeypatch.setenv("MM_K_INV", "1.0")
    p = QuoteParams(k_inv=0.5)
    with caplog.at_level(logging.WARNING):
        apply_env_param_overrides(p)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("F327" in m and "k_inv" in m for m in msgs), f"未告警；实际：{msgs}"
    assert p.k_inv == pytest.approx(0.5), "注册表应赢"


@pytest.mark.unit
def test_authoritative_no_warn_when_in_sync(monkeypatch, caplog):
    """注册表与 env 一致时不告警（否则每 60s 刷屏，warning 失去意义）。"""
    monkeypatch.setenv(AUTH_ENV, "1")
    monkeypatch.setenv("MM_SPREAD_MULT", "0.5")
    p = QuoteParams(spread_mult=0.5)
    with caplog.at_level(logging.WARNING):
        apply_env_param_overrides(p)
    msgs = [r.getMessage() for r in caplog.records]
    assert not [m for m in msgs if "F327" in m], f"一致时不应告警；实际：{msgs}"


@pytest.mark.unit
def test_authoritative_covers_all_seven_keys(monkeypatch):
    """7 个键**全部**交还注册表（防止漏掉某键而静默失效）。"""
    monkeypatch.setenv(AUTH_ENV, "1")
    cases = {
        "MM_SPREAD_MULT": ("spread_mult", 0.11, 0.91),
        "MM_SPREAD_MULT_REDUCE": ("spread_mult_reduce", 0.12, 0.92),
        "MM_MIN_EDGE_FRAC": ("min_edge_frac", 0.13, 0.93),
        "MM_SPREAD_CROSS_MARGIN": ("spread_cross_margin", 0.14, 0.94),
        "MM_W_BASE_BP": ("w_base_bp", 0.15, 0.95),
        "MM_MIN_WIDTH_BP": ("min_width_bp", 0.16, 0.96),
        "MM_K_INV": ("k_inv", 0.17, 0.97),
    }
    for env, (_, env_val, _reg) in cases.items():
        monkeypatch.setenv(env, str(env_val))
    p = QuoteParams(**{field: reg for _, (field, _, reg) in cases.items()})
    out = apply_env_param_overrides(p)
    for env, (field, env_val, reg_val) in cases.items():
        got = getattr(out, field)
        assert got == pytest.approx(reg_val), f"{field} 应为注册表 {reg_val}，实际 {got}"
        assert got != pytest.approx(env_val), f"{field} 不应被 env {env_val} 覆盖"


@pytest.mark.unit
def test_authoritative_accepts_truthy_spellings(monkeypatch):
    """1/true/yes/on 都算开启；0/false/no/空 都算关闭。

    防的是"部署写法不一致导致开关静默失效"——那会让 P2 的整条闭环白跑。
    """
    for raw in ("1", "true", "TRUE", "yes", "On"):
        monkeypatch.setenv(AUTH_ENV, raw)
        monkeypatch.setenv("MM_SPREAD_MULT", "0.5")
        p = QuoteParams(spread_mult=0.9)
        assert apply_env_param_overrides(p).spread_mult == pytest.approx(0.9), raw
    for raw in ("0", "false", "no", "off", ""):
        monkeypatch.setenv(AUTH_ENV, raw)
        monkeypatch.setenv("MM_SPREAD_MULT", "0.5")
        p = QuoteParams(spread_mult=0.9)
        assert apply_env_param_overrides(p).spread_mult == pytest.approx(0.5), raw


@pytest.mark.unit
def test_status_reports_authority(monkeypatch):
    """`status()` 必须报出谁在权威 —— 不许只存在于日志里。

    归因脚本/LLM 读的是**心跳**，不是日志。若心跳不报这一项，
    "注册表 0.5 / 实盘 1.0"这类分歧就只能靠人肉比对发现。
    """
    import inspect

    from backend.services.market_maker import runner as R

    src = inspect.getsource(R.ShadowRunner.status)
    assert "param_authority" in src, "status 必须暴露 param_authority"
    assert 'MM_REGISTRY_AUTHORITATIVE' in src, "必须读同一个 env 名（拼错即静默失效）"
