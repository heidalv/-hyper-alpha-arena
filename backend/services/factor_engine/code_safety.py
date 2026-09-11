"""code_safety — 因子代码安全校验（阶段4，2026-08-14）。

供 factor_sync_service（云端因子）与 ai_factor_discovery_service（LLM 生成因子）
共用：把黑名单字面量匹配升级为 **AST 白名单**。

[2026-09-02 因子闭环修复 E13] 重写为四层规则。修复两个可实际利用的穿透：

1. **``islower()`` 放行任意外来名**（F30 为救 LLM 中间变量写法而引入）：
   ``os.system('calc')`` 的链根 ``os`` 是小写标识符，旧规则直接返回 True，
   连黑名单都没有第二道 —— AST 校验对最经典的注入完全失效。
2. **属性链只查根、不查中段**：``pd.io.common.os.system('calc')`` 的根是白名单
   里的 ``pd``，旧逻辑放行；而 ``pd.io.common.os`` 就是真正的 os 模块，
   ``pd.core.computation.eval.eval('1')`` 同理。pandas/numpy 是因子模板必然注入
   的对象，因此这条是能直接落地的 RCE，不是理论风险。

四层规则：

- **L1 链段黑名单**：属性链的每一段都查危险名（挡模块走私与危险可调用）；
- **L2 链根白名单 ∪ 局部绑定**：用「本代码块内被绑定过的名字」替代 ``islower()``，
  既保住 LLM 的中间变量方法链写法，又挡住外来名；
- **L3 下划线收紧**：属性一律禁下划线开头（``np.core._multiarray_umath`` 这类
  私有入口），裸名字仅在非本地绑定时禁；
- **L4 调用白名单**：裸函数调用限于安全内建 ∪ 本代码块内定义/绑定的函数。

保留的原有防线：禁止一切 import、禁止 dunder。
"""
from __future__ import annotations

import ast
import logging
from typing import Optional, Set, Tuple

logger = logging.getLogger(__name__)

# 属性链根白名单：因子代码里允许访问的对象名
_SAFE_ATTR_ROOTS = frozenset({
    "np", "pd", "df", "data", "self", "series", "result",
    "close", "high", "low", "volume", "open", "values", "index",
})

# 全局函数调用白名单（裸名调用）
_SAFE_BUILTINS = frozenset({
    "len", "abs", "min", "max", "round", "sum", "float", "int", "str",
    "bool", "list", "dict", "tuple", "enumerate", "zip", "range", "sorted",
    "isinstance", "any", "all",
})

# L1 链段黑名单：属性链任意一段命中即拒。
#
# 选名原则 —— 只收「模块名」与「真正危险的可调用名」，刻意不收业务列名，
# 否则会误杀 pandas 的属性式列访问：
#   - ``open`` 不在此列：``data.open`` 是合法列访问；危险的裸 ``open()`` 由 L4
#     的内建白名单拦下；
#   - ``values`` / ``index`` / ``columns`` 等 pandas 常用属性同样不收；
#   - ``io`` 在此列：``pd.io`` 是模块走私的跳板（``pd.io.common.os``），而
#     DataFrame 本身没有 ``.io`` 属性，因子代码不会用到；
#   - ``load`` / ``loads`` 在此列：``np.load`` 可反序列化 pickle 直接 RCE，
#     且因子代码没有读文件的正当理由。
_DANGEROUS_CHAIN_SEGMENTS = frozenset({
    # 模块名
    "os", "sys", "io", "subprocess", "socket", "shutil", "pathlib",
    "importlib", "imp", "builtins", "__builtin__", "ctypes", "cffi",
    "pickle", "cPickle", "marshal", "shelve", "dbm", "sqlite3",
    "code", "codeop", "runpy", "inspect", "gc", "traceback", "pty", "tty",
    "platform", "tempfile", "glob", "fileinput", "linecache",
    "threading", "multiprocessing", "asyncio", "concurrent",
    "signal", "atexit", "sysconfig", "site", "distutils", "setuptools",
    "urllib", "urllib2", "httplib", "http", "requests", "ftplib",
    "telnetlib", "smtplib", "webbrowser", "xmlrpc", "ssl", "select",
    "resource", "pwd", "grp", "crypt", "termios", "fcntl", "mmap",
    # 危险可调用/属性名
    "system", "popen", "popen2", "popen3", "popen4", "startfile",
    "spawn", "spawnl", "spawnle", "spawnv", "spawnve", "spawnlp", "spawnvp",
    "execv", "execve", "execl", "execle", "execlp", "execvp", "fork", "forkpty",
    "kill", "killpg", "abort", "waitpid",
    "remove", "unlink", "rmtree", "rmdir", "removedirs", "mkdir", "makedirs",
    "chmod", "chown", "chdir", "chroot", "rename", "renames", "replace",
    "link", "symlink", "truncate", "ftruncate", "utime",
    "eval", "exec", "compile", "execfile", "reload",
    "getattr", "setattr", "delattr", "hasattr",
    "globals", "locals", "vars", "dir",
    "input", "raw_input", "breakpoint", "exit", "quit",
    "urlopen", "urlretrieve", "connect", "bind", "listen", "accept",
    "recv", "recvfrom", "send", "sendall", "sendto",
    "load", "loads", "dump", "dumps", "save", "savez", "tofile", "fromfile",
    "memoryview", "frombuffer", "ctypeslib", "getbuffer",
    "environ", "putenv", "unsetenv", "getenv",
    "modules", "meta_path", "path_hooks", "argv", "executable",
    "func_globals", "gi_frame", "cr_frame", "tb_frame", "f_globals",
    "f_builtins", "f_locals", "im_func", "im_self",
})


# 字符串层快速拦截名单（AST 之前的第一道防线）。
#
# [2026-09-02 E13] 三个消费者原本只有两个带字符串黑名单：
# ai_factor_discovery_service（自带 9 项名单）、factor_sync_service
# （_FORBIDDEN_PATTERNS + compile），而 llm_proposal 只有 AST 单层 —— 偏偏它的
# _trial_eval 会用真 eval() 执行 LLM 返回的公式，是唯一「单层把关 + 立即执行」
# 的路径。此处提供共享名单补齐纵深。
_FORBIDDEN_LITERALS = (
    "os.system", "os.popen", "os.remove", "os.environ", "os.spawn",
    "subprocess", "importlib", "__import__", "shutil", "socket",
    "requests", "urllib", "pickle", "marshal", "ctypes", "webbrowser",
    "eval(", "exec(", "compile(", "open(", "input(",
    "getattr(", "setattr(", "delattr(", "globals(", "locals(", "vars(",
    "__class__", "__globals__", "__builtins__", "__subclasses__",
    "__loader__", "__code__", "__mro__", "__dict__",
    "np.load", "npyio", "fromfile", "tofile", ".io.", "sys.modules",
)


def forbidden_literal_scan(code: str) -> Tuple[bool, str]:
    """字面量黑名单快速拦截。返回 ``(ok, reason)``。

    定位是 AST 校验之前的**第一道**廉价防线，不替代 ``ast_whitelist_check``：
    字面量匹配天生可绕（拼接、别名），真正的把关在 AST 四层规则。

    Args:
        code: 待检查的因子代码或公式字符串。

    Returns:
        (True, "") 通过；(False, 命中的模式) 拒绝。
    """
    low = (code or "").lower()
    for pat in _FORBIDDEN_LITERALS:
        if pat in low:
            return False, f"命中禁止模式: {pat}"
    return True, ""


def _target_names(node) -> Set[str]:
    """从赋值/循环目标里提取被绑定的裸名字。

    ``df['x'] = ...`` / ``obj.attr = ...`` 不产生新名字，故 Subscript/Attribute
    目标返回空集。
    """
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        out: Set[str] = set()
        for elt in node.elts:
            out |= _target_names(elt)
        return out
    if isinstance(node, ast.Starred):
        return _target_names(node.value)
    return set()


def _arg_names(args: ast.arguments) -> Set[str]:
    """函数/lambda 的全部形参名。"""
    out: Set[str] = set()
    for arg in (
        list(getattr(args, "posonlyargs", []) or [])
        + list(args.args or [])
        + list(args.kwonlyargs or [])
    ):
        out.add(arg.arg)
    if args.vararg:
        out.add(args.vararg.arg)
    if args.kwarg:
        out.add(args.kwarg.arg)
    return out


def collect_local_bindings(tree: ast.AST) -> Set[str]:
    """收集代码块内被绑定过的名字。

    L2 用它替代原来的 ``islower()``：本地绑定的名字必然指向代码块内自己造出来的
    对象（Series/DataFrame/标量），不可能是走私进来的模块；而外来名（``os``）
    没有任何绑定点，就此被挡在门外。

    收集来源：Assign / AnnAssign / AugAssign / NamedExpr(海象) / For /
    comprehension / withitem / except as / 函数名与形参 / lambda 形参。
    """
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                names |= _target_names(tgt)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            names |= _target_names(node.target)
        elif isinstance(node, ast.NamedExpr):
            names |= _target_names(node.target)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            names |= _target_names(node.target)
        elif isinstance(node, ast.comprehension):
            names |= _target_names(node.target)
        elif isinstance(node, ast.withitem):
            if node.optional_vars is not None:
                names |= _target_names(node.optional_vars)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
            names |= _arg_names(node.args)
        elif isinstance(node, ast.Lambda):
            names |= _arg_names(node.args)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                names.add(node.name)
    return names


def _chain_root_and_segments(node) -> Tuple[Optional[str], list]:
    """解析属性链，返回 (链根名, 各段属性名列表)。

    穿过 Subscript 与 Call 定位真正的根，这样 ``data['close'].rolling(3).mean()``
    能正确解析出根 ``data`` —— 合法的方法链写法必须放行。

    根不是裸名字时（字面量方法调用等）返回 None，此时只做链段黑名单检查。
    """
    segments: list = []
    cur = node
    while True:
        if isinstance(cur, ast.Attribute):
            segments.append(cur.attr)
            cur = cur.value
        elif isinstance(cur, ast.Subscript):
            cur = cur.value
        elif isinstance(cur, ast.Call):
            cur = cur.func
        else:
            break
    root = cur.id if isinstance(cur, ast.Name) else None
    segments.reverse()
    return root, segments


def _check_attribute_chain(
    node: ast.Attribute, local_names: Set[str],
) -> Tuple[bool, str]:
    """校验一条属性链（L1 + L2 + L3）。"""
    root, segments = _chain_root_and_segments(node)

    # L1：每一段都查黑名单 —— 挡 pd.io.common.os.system 这类中段走私
    for seg in segments:
        if seg in _DANGEROUS_CHAIN_SEGMENTS:
            return False, f"属性链含危险名: .{seg}"
        # L3：私有入口（np.core._multiarray_umath / .__globals__ 等）
        if seg.startswith("_"):
            return False, f"禁止下划线属性访问: .{seg}"

    # L2：链根白名单 ∪ 局部绑定。黑名单优先于局部绑定，
    # 避免 `os = data['close']` 这种把危险名绑成变量来漂白的写法。
    if root is None:
        return True, ""
    if root in _DANGEROUS_CHAIN_SEGMENTS:
        return False, f"属性链根是危险名: {root}."
    if root in _SAFE_ATTR_ROOTS or root in local_names:
        return True, ""
    return False, f"属性链根不在白名单且非本地变量: {root}."


def ast_whitelist_check(code: str) -> Tuple[bool, str]:
    """AST 白名单校验。返回 (ok, reason)。

    Args:
        code: 待校验的因子代码（模块或函数体片段均可）。

    Returns:
        (True, "") 表示通过；(False, 原因) 表示拒绝，原因可直接进日志/审计。
    """
    try:
        tree = ast.parse(code or "")
    except SyntaxError as e:
        return False, f"语法错误: {e}"

    local_names = collect_local_bindings(tree)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return False, "禁止 import"

        if isinstance(node, ast.Attribute):
            ok, reason = _check_attribute_chain(node, local_names)
            if not ok:
                return False, reason

        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                # L4：裸调用限于安全内建 ∪ 本代码块内定义/绑定的可调用
                _fname = node.func.id
                if _fname in _DANGEROUS_CHAIN_SEGMENTS:
                    return False, f"禁止调用危险函数: {_fname}()"
                if _fname not in _SAFE_BUILTINS and _fname not in local_names:
                    return False, f"全局函数不在白名单: {_fname}()"
            elif isinstance(node.func, ast.Attribute):
                ok, reason = _check_attribute_chain(node.func, local_names)
                if not ok:
                    return False, reason

        if isinstance(node, ast.Name):
            if node.id.startswith("__"):
                return False, f"禁止 dunder 命名: {node.id}"
            # L3（名字侧）：单下划线开头只在「非本地绑定」时拒。
            # LLM 常写 `_close = data['close']` 这类局部变量，一律拒会大面积误杀；
            # 而未经绑定就引用的 `_secret` 必定来自代码块之外，属于走私。
            if node.id.startswith("_") and node.id not in local_names:
                return False, f"禁止引用外部下划线名: {node.id}"

    return True, ""
