# -*- coding: utf-8 -*-
"""[F361 2026-09-18] 复查修复**金丝雀**：把 `scripts/verify_review_findings.py` 的全部检查
搬进测试套件。

为什么需要：本次复查的 23 处修复横跨 20 个文件，而**同一仓库有另一个 agent 在并发编辑**
（14:58 已实际发生过一次覆盖：`backend-watchdog.ps1` 与 `lane_routes.py` 的能力被抹掉）。
于是"我修过"与"现在还在"是两件事 —— 本用例把 24 项检查变成 CI 保护：
**任何一处修复被回退/覆盖，测试立刻红**，而不是等到某天有人发现读数又变回原样。

（另：`verify_review_findings.py` 首版有 3 条检查是**假阳性**——它把"我解释缺陷的注释"
当成了"缺陷本身"。修法是排除注释 + 优先用运行态断言。这段经历本身也说明：
**源码文本匹配类检查必须能被证伪**，否则会给出虚假的安全感。）
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

_SCRIPT = ROOT / "scripts" / "verify_review_findings.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location("_verify_review_findings", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_all_review_fixes_still_present():
    """24 项检查必须全绿；失败即说明某处修复被回退（或文档需同步）。"""
    mod = _load_verifier()
    assert mod.RESULTS, "复验脚本未注册任何检查"
    failures = []
    for fid, title, fn in mod.RESULTS:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"抛异常 {type(exc).__name__}: {str(exc)[:120]}"
        if not ok:
            failures.append(f"[{fid}] {title} → {detail}")
    assert not failures, "复查修复被回退或需同步文档:\n  " + "\n  ".join(failures)


def test_verifier_declares_what_it_does_not_cover():
    """必须显式列出"未覆盖项"，否则读者会以为全绿=整个复查都验证过了。"""
    mod = _load_verifier()
    assert len(mod.NOT_COVERED) >= 4
    joined = " ".join(mod.NOT_COVERED)
    for kw in ("开仓准确率", "实盘行为"):
        assert kw in joined, f"未覆盖项缺少关键声明: {kw}"


def test_verifier_is_offline_and_read_only():
    """复验脚本必须离线、且**跑完不留下环境副作用**。

    原来我用"数 env 赋值出现次数"来判成对恢复 —— 太脆（一处改成 pop/restore 就误报）。
    改为**行为断言**：记录跑前后的环境，跑完必须一致。这同时覆盖"临时改 env 做隔离"
    这个合法用法：允许改，但必须复原。
    """
    import os
    code = _SCRIPT.read_text(encoding="utf-8")
    for bad in ("os.remove(", "shutil.rmtree(", "unlink("):
        assert bad not in code, f"复验脚本不应包含 {bad}"

    mod = _load_verifier()
    watch = ("V7_MEMORY_DB_PATH", "V7_CODEGEN_FULL_POOL", "FACTOR_LIVE_ALLOWLIST_ONLY",
             "LEARNING_READBACK_ENABLED")
    before = {k: os.environ.get(k) for k in watch}
    for _fid, _title, fn in mod.RESULTS:
        try:
            fn()
        except Exception:  # noqa: BLE001
            pass
    after = {k: os.environ.get(k) for k in watch}
    assert before == after, f"复验脚本留下了环境副作用: {before} → {after}"


def test_verifier_prefers_code_over_comments():
    """注释剥离工具必须真的生效（防止未来又出现"被自己的注释判失败"）。

    注意断言的**精确性**：代码里其它变量上的 `or 0`（如 `float(x, 0) or 0`）是合法写法，
    本缺陷专指 **`trade_pnl*` 上的 `or 0`**（把 NULL 当 PnL=0）。断言必须只针对后者，
    否则就会像我第一次那样写出"过于宽松/过于严格"的检查。
    """
    import re
    mod = _load_verifier()
    raw = mod._src("backend/services/signal_feedback_tracker.py")
    code = mod._code("backend/services/signal_feedback_tracker.py")
    pat = re.compile(r"trade_pnl(_pct)?\s*or\s*0")
    assert pat.search(raw), "原始文本应包含解释该缺陷的注释（证明剥离不是没内容可剥）"
    assert not pat.search(code), "剥离注释后不应再出现 trade_pnl 上的 `or 0`"
    assert len(code) < len(raw), "剥离文本应变短"


def test_documentation_counts_match_reality():
    """[纪律 25/26] 文档里的**计数类声明**必须与文件系统事实一致。

    动机：三份文档写着"30 项 / 19 文件 / 183 用例 / 71 个归档文件"，
    但脚本、测试、归档目录都会变 —— 数字漂移了没人知道，文档就开始骗人
    （这与本会话反复出现的"声明≠实际"是同一族：`days=7` 其实 10.8 天、
    "47 个变量"其实 6 个）。本用例把 `scripts/check_doc_claims_20260918.py`
    接进 CI：**计数漂移即红**。
    """
    spec = importlib.util.spec_from_file_location(
        "_check_doc_claims", ROOT / "scripts" / "check_doc_claims_20260918.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rc = mod.main()
    assert rc == 0, "文档计数与事实漂移（详见 scripts/check_doc_claims_20260918.py 输出）"


def test_doc_claims_checker_refuses_to_edit():
    """自检脚本只读：不得出现写文件/改 env 的调用（防止"自动改文档"这种危险升级）。"""
    code = (ROOT / "scripts" / "check_doc_claims_20260918.py").read_text(encoding="utf-8")
    for bad in ("open(", "write(", "os.remove(", "shutil.rmtree(", "os.environ["):
        if bad == "open(":
            # 只允许以读取模式 open(..., encoding="utf-8")
            import re
            for m in re.finditer(r"open\((.*?)\)", code):
                assert '"w"' not in m.group(1) and "'w'" not in m.group(1), \
                    f"自检脚本不应写文件: open({m.group(1)})"
            continue
        assert bad not in code, f"自检脚本不应包含 {bad}"
