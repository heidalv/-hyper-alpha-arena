"""E13 AST 白名单四层规则测试（2026-09-02 因子系统闭环修复）。

修复的两个**可实际利用**的穿透：

1. ``islower()`` 放行任意外来名 —— ``os.system('calc')`` 的链根是小写标识符，
   旧规则直接返回 True；
2. 属性链只查根不查中段 —— ``pd.io.common.os.system('calc')`` 根是白名单里的
   ``pd``，而 ``pd.io.common.os`` 就是真的 os 模块。pandas/numpy 是因子模板必然
   注入的对象，所以这是能直接落地的 RCE。

本测试同时守两侧：攻击样本全拦（零漏放）、合法因子写法全放行（零误杀）。
后者尤其重要 —— F30 当初引入 ``islower()`` 就是因为严白名单把 LLM 候选 100% 拒掉，
如果这次收紧又把合法写法拒了，下一个人还会把口子重新开开。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from backend.services.factor_engine.code_safety import (
    ast_whitelist_check,
    collect_local_bindings,
    forbidden_literal_scan,
)


# ── 1. 攻击样本：必须全部拦下 ─────────────────────────────────────

@pytest.mark.parametrize("payload", [
    # 经典注入
    "import os",
    "from os import system",
    "os.system('calc')",
    "__import__('os')",
    "eval('1+1')",
    "exec('x=1')",
    "compile('x', 'f', 'exec')",
    "open('/etc/passwd')",
    "getattr(data, 'x')",
    "setattr(data, 'x', 1)",
    "subprocess.run('x')",
    "socket.socket()",
    # dunder 逃逸
    "x.__class__",
    "data.__globals__",
    "result = data.__class__.__bases__",
    # 属性链中段走私（旧逻辑只查根 → 全部穿透）
    "pd.io.common.os.system('calc')",
    "pd.core.computation.eval.eval('1')",
    "np.core._multiarray_umath.__loader__",
    "pd.io.pickle.pickle.loads(b'x')",
    "np.load('payload.npy')",
    "np.lib.npyio.load('x')",
    "sys.modules['os'].system('x')",
    "globals()['x']",
    "locals()",
    "vars(data)",
    # 环境/进程
    "os.environ['PATH']",
    "os.popen('ls')",
    "os.remove('f')",
    "shutil.rmtree('/')",
])
def test_attack_payloads_rejected(payload):
    ok, reason = ast_whitelist_check(payload)
    assert ok is False, f"应拒绝但放行了: {payload!r}"
    assert reason, "拒绝必须给出原因（要进审计日志）"


def test_dangerous_name_cannot_be_laundered_by_binding():
    """把危险名绑成局部变量来漂白 → 仍然拒（黑名单优先于局部绑定）。"""
    code = "os = data['close']\nresult = os.system('calc')"
    ok, _ = ast_whitelist_check(code)
    assert ok is False


def test_chain_root_resolved_through_call_and_subscript():
    """链根解析要穿过 Call/Subscript，否则中段走私可借 () 或 [] 藏身。"""
    for payload in (
        "pd.io.common['os'].system('x')",
        "pd.io.common.os.system('x')",
        "np.core.umath.__dict__",
    ):
        ok, _ = ast_whitelist_check(payload)
        assert ok is False, f"应拒绝: {payload!r}"


def test_external_underscore_name_rejected():
    """未绑定就引用的下划线名必定来自代码块之外。"""
    ok, _ = ast_whitelist_check("result = _secret_handle.read()")
    assert ok is False


# ── 2. 合法因子写法：必须全部放行 ─────────────────────────────────

@pytest.mark.parametrize("code", [
    # 模板注入对象的常规用法
    "result = data['close'].rolling(20).mean()",
    "result = np.log(data['close'])",
    "result = pd.Series(data['close']).pct_change().fillna(0)",
    "result = float(abs(min(-1, max(1, len(data)))))",
    # 属性式列访问 —— open 不能因为裸 open() 危险就被连坐
    "result = data.open.mean()",
    "result = df.values.mean()",
    "result = data.close.rolling(5).std()",
    # LLM 的中间变量方法链（F30 当初为救它才开的口子，必须继续放行）
    "x = data['close'].pct_change(5)\nresult = x.rolling(3).mean()",
    "vol = data['volume'].rolling(20).mean()\nrel = data['volume'] / vol\n"
    "result = rel.clip(0, 5)",
    # 下划线局部变量（Python 惯例写法，一律拒会大面积误杀）
    "_close = data['close']\nresult = _close.mean()",
    "_a, _b = data['high'], data['low']\nresult = (_a - _b).mean()",
    # 循环 / 推导式 / with / 异常 绑定
    "acc = 0\nfor i in range(5):\n    acc = acc + i\nresult = acc",
    "vals = [v for v in range(3)]\nresult = sum(vals)",
    "pairs = {k: v for k, v in zip(range(2), range(2))}\nresult = len(pairs)",
    "try:\n    result = data['close'].mean()\nexcept Exception as err:\n"
    "    result = 0.0",
    # 本地函数与 lambda
    "def _norm(s):\n    return (s - s.mean()) / (s.std() + 1e-9)\n"
    "result = _norm(data['close'])",
    "sq = lambda v: v * v\nresult = sq(2.0)",
    # 海象运算符
    "result = (n := len(data)) and float(n)",
    # 函数体形式（因子模板的真实形态）
    "def compute(self, data):\n"
    "    px = data['close']\n"
    "    return px.rolling(14).mean() / (px.std() + 1e-9)",
])
def test_legit_factor_code_accepted(code):
    ok, reason = ast_whitelist_check(code)
    assert ok is True, f"应放行但拒绝了: {code!r} → {reason}"


# ── 3. 局部绑定收集 ──────────────────────────────────────────────

def test_collect_local_bindings_sources():
    """绑定来源要覆盖赋值/海象/循环/推导/with/except/函数名与形参/lambda。"""
    import ast

    code = (
        "a = 1\n"
        "b: int = 2\n"
        "c = 0\n"
        "c += 1\n"
        "for d in range(3):\n"
        "    pass\n"
        "e = [f for f in range(3)]\n"
        "g = (h := 5)\n"
        "try:\n"
        "    pass\n"
        "except Exception as i:\n"
        "    pass\n"
        "def j(k, *l, m=1, **n):\n"
        "    return k\n"
        "o = lambda p: p\n"
        "q, (r, s) = 1, (2, 3)\n"
        "*t, u = [1, 2, 3]\n"
    )
    names = collect_local_bindings(ast.parse(code))

    for expected in "abcdefghijklmnopqrstu":
        assert expected in names, f"漏收绑定名: {expected}"


def test_subscript_and_attribute_targets_bind_nothing():
    """``df['x'] = ..`` / ``obj.attr = ..`` 不产生新名字，不能借此漂白外来名。"""
    import ast

    names = collect_local_bindings(ast.parse("d['os'] = 1\nz.os = 2"))
    assert "os" not in names


# ── 4. 基础防线仍在 ──────────────────────────────────────────────

def test_syntax_error_is_rejected_with_reason():
    ok, reason = ast_whitelist_check("result = (")
    assert ok is False
    assert "语法错误" in reason


def test_empty_code_is_accepted():
    """空代码没有可执行内容，交由上游长度/结构校验处理。"""
    assert ast_whitelist_check("")[0] is True


# ── 5. 字面量兜底（llm_proposal 的第一道防线）────────────────────

@pytest.mark.parametrize("payload", [
    "os.system('x')", "subprocess.run('x')", "__import__('os')",
    "eval('1')", "exec('1')", "open('f')", "getattr(x,'y')",
    "x.__class__", "np.load('p.npy')", "pd.io.common.os",
    "sys.modules['os']", "OS.SYSTEM('X')",  # 大小写不敏感
])
def test_literal_scan_rejects(payload):
    ok, reason = forbidden_literal_scan(payload)
    assert ok is False, f"字面量层应拦下: {payload!r}"
    assert reason


@pytest.mark.parametrize("formula", [
    # llm_proposal 的真实公式形态：FORMULA_OPS + OHLCV 字段 + np
    "ts_mean(close, 20) / (ts_std(close, 20) + 1e-9)",
    "(high - low) / close",
    "np.log(close / delay(close, 1))",
    "rank(volume) - rank(returns)",
    # open 是 OHLCV 字段名，不能被裸 open( 的黑名单连坐
    "(close - open) / (high - low + 1e-9)",
    "ts_mean(open, 5) - ts_mean(close, 5)",
])
def test_literal_scan_accepts_legit_formulas(formula):
    ok, reason = forbidden_literal_scan(formula)
    assert ok is True, f"合法公式被误杀: {formula!r} → {reason}"


def test_llm_proposal_has_literal_fallback():
    """llm_proposal 必须双层把关 —— 它会用真 eval() 执行 LLM 公式。"""
    path = os.path.join(os.path.dirname(__file__), "..", "..",
                        "services", "factor_engine", "llm_proposal.py")
    with open(path, encoding="utf-8") as fh:
        src = "".join(ln for ln in fh if not ln.strip().startswith("#"))
    assert "forbidden_literal_scan" in src, (
        "llm_proposal 缺字面量兜底，是唯一单层把关且立即 eval 的路径"
    )
    assert "ast_whitelist_check" in src


def test_islower_bypass_is_gone():
    """回归哨兵：任意小写外来名不得再被放行。"""
    import backend.services.factor_engine.code_safety as cs

    src_path = cs.__file__
    with open(src_path, encoding="utf-8") as fh:
        live = "".join(
            ln for ln in fh if not ln.strip().startswith("#")
        )
    assert "root.islower()" not in live, (
        "islower() 放行逻辑仍在 —— os.system('calc') 会再次穿透"
    )
