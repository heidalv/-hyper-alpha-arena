# -*- coding: utf-8 -*-
"""深度分析输出契约的回归测试（2026-09-04）。

背景：`signal_review` / `anomaly` 两个 agent 调 dual_call 时用的 task 名从未注册进
TASK_SCHEMAS，于是 output_contract 走兜底分支，把所有字段渲染成字符串 "..." 发给模型
当契约 —— 模型越照抄类型错得越彻底，实测 DeepSeek 与本地 qwen3 同时四个字段全错，
consensus 恒为 skipped，这两个 agent 的 LLM 层开启即 100% 产不出结论。

这里锁三件事：
  1. 代码里实际调用的 task 名都已注册（防止再加 agent 时漏注册）
  2. 每个 task 的 template 字段类型与 required 声明自洽（防止契约示例本身不合法）
  3. 兜底模板同样类型自洽（未注册任务也不能给出误导性契约）
"""
from __future__ import annotations

import re
from pathlib import Path

from backend.services.analysis import schemas

_BACKEND = Path(__file__).resolve().parents[2]


def _kind_of(value):
    """把 template 里的示例值映射成 schemas 的类型标记。"""
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "weights"  # dict 型字段目前只有 bucket_weights
    return "str"


def _declared_kind(kind: str) -> str:
    head = kind.split(":")[0]
    # enum 的示例值写成 "a|b|c" 字符串，与 str 同形
    return "str" if head == "enum" else head


def test_all_called_tasks_are_registered():
    """扫描源码里 dual_call("<task>") 的字面量，逐个确认已注册。"""
    called = set()
    pat = re.compile(r"dual_call\(\s*[\"'](\w+)[\"']", re.S)
    # 只扫 services/api：dual_call 的调用方只可能在这两处，全量 rglob 会拖到超时
    for root in (_BACKEND / "services", _BACKEND / "api"):
        for py in root.rglob("*.py"):
            try:
                called |= set(pat.findall(py.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                continue

    assert called, "未扫描到任何 dual_call 调用，正则可能失效"
    missing = sorted(t for t in called if t not in schemas.TASK_SCHEMAS)
    assert not missing, (
        f"这些 task 被 dual_call 调用但未注册进 TASK_SCHEMAS: {missing}。"
        "未注册会退到兜底模板，模型输出必然 schema 失败、产不出任何结论。"
    )


def test_每个task的template与required类型自洽():
    """契约示例本身必须是合法类型，否则等于教模型犯错。"""
    problems = []
    for task, spec in schemas.TASK_SCHEMAS.items():
        tpl = spec.get("template") or {}
        for field, kind in (spec.get("required") or {}).items():
            if field not in tpl:
                problems.append(f"{task}.{field}: required 声明了但 template 没给示例")
                continue
            want, got = _declared_kind(kind), _kind_of(tpl[field])
            if want != got:
                problems.append(
                    f"{task}.{field}: 声明 {kind} 但 template 示例是 {got}（{tpl[field]!r}）"
                )
    assert not problems, "契约示例与声明类型不符:\n  " + "\n  ".join(problems)


def test_兜底模板类型自洽():
    """未注册 task 走 COMMON_TEMPLATE，它也必须类型正确。"""
    for field, kind in schemas.COMMON_REQUIRED.items():
        assert field in schemas.COMMON_TEMPLATE, f"兜底模板缺字段 {field}"
        want = _declared_kind(kind)
        got = _kind_of(schemas.COMMON_TEMPLATE[field])
        assert want == got, (
            f"兜底模板 {field}: 声明 {kind} 但示例是 {got}"
            f"（{schemas.COMMON_TEMPLATE[field]!r}）—— 这正是导致模型全字段类型错的原因"
        )


def test_合法输出能通过校验():
    """按 template 造一份合法输出，validate 必须放行。"""
    for task in ("signal_review", "anomaly", "timing"):
        spec = schemas.TASK_SCHEMAS[task]
        obj = {}
        for field, kind in spec["required"].items():
            head = kind.split(":")[0]
            if head == "enum":
                allowed = {
                    "direction": schemas.DIRECTIONS,
                    "regime": schemas.REGIMES,
                    "arbiter": schemas.ARBITER_VERDICTS,
                }[kind.split(":")[1]]
                obj[field] = allowed[0]
            elif head == "number":
                obj[field] = float(kind.split(":")[1])
            elif head == "list":
                obj[field] = [{} if kind.split(":")[1] == "dict" else "x"]
            elif head == "weights":
                keys = kind.split(":")[1].split(",")
                obj[field] = {k: round(1.0 / len(keys), 4) for k in keys}
            else:
                obj[field] = "x"
        ok, errs = schemas.validate(task, obj)
        assert ok, f"{task} 合法输出被拒: {errs}"


def test_契约必须写明取值范围():
    """契约只给示例值时，模型无从得知 strength 的上限是 10。

    实测 qwen2.5:7b 因此输出超范围值被判 schema 失败，交叉验证悄悄退化成单票
    （consensus 0.35 且 comparison=single_vote），表面像"两模型分歧"，实则一票没进来。
    """
    for task, spec in schemas.TASK_SCHEMAS.items():
        text = schemas.output_contract(task)
        for key, kind in spec["required"].items():
            assert key in text, f"{task}: 契约未提及字段 {key}"
            if kind.split(":")[0] == "number":
                _, lo, hi = kind.split(":")
                assert lo in text and hi in text, (
                    f"{task}.{key}: 契约未写明取值范围 [{lo}, {hi}]"
                )
            if kind.split(":")[0] == "enum":
                allowed = {"direction": schemas.DIRECTIONS,
                           "regime": schemas.REGIMES,
                           "arbiter": schemas.ARBITER_VERDICTS}[kind.split(":")[1]]
                for a in allowed:
                    assert a in text, f"{task}.{key}: 契约未列出枚举值 {a}"


def test_契约兜底注入(monkeypatch):
    """契约原本要各调用方自己拼进 system，三个 agent 全漏了 → 模型压根没收到
    字段要求，自由发挥出的 JSON 必然 schema 失败。网关须统一兜底。"""
    from backend.services.analysis import model_gateway as mg

    seen = {}

    class _T(mg.Transport):
        name = "t"

        def configured(self):
            return True, ""

        def model_name(self):
            return "m"

        def complete(self, system, user, **kw):
            seen["system"] = system
            return mg.RawCompletion(text='{"direction":"neutral","strength":0,'
                                         '"confidence":0.0,"summary":"x"}',
                                    input_tokens=1, output_tokens=1, model="m")

    monkeypatch.setattr(mg.ModelGateway, "_record", lambda self, *a, **k: "r")
    monkeypatch.setattr(mg.QuotaGuard, "check", lambda self, *a, **k: mg.Decision("allow", ""))
    monkeypatch.setattr(mg.QuotaGuard, "record", lambda self, *a, **k: None)
    g = mg.ModelGateway(transports={"t": _T()})

    # 调用方没拼契约 → 网关补
    g.call("gateway_test", "你是分析员。", "u", transport="t")
    assert schemas.CONTRACT_MARK in seen["system"], "网关应自动补上输出契约"

    # 调用方已拼契约 → 不重复注入
    manual = "你是分析员。\n" + schemas.output_contract("gateway_test")
    g.call("gateway_test", manual, "u", transport="t")
    assert seen["system"].count(schemas.CONTRACT_MARK) == 1, (
        "调用方已自行拼接时不得重复注入"
    )


def test_原兜底写法会被本测试抓住():
    """反向验证：旧的 {k: '...'} 兜底确实过不了类型自洽检查。"""
    legacy = {k: "..." for k in schemas.COMMON_REQUIRED}
    bad = [
        f
        for f, kind in schemas.COMMON_REQUIRED.items()
        if _declared_kind(kind) != _kind_of(legacy[f])
    ]
    assert bad, "旧兜底写法应当被判为类型不符，否则本测试失去意义"
