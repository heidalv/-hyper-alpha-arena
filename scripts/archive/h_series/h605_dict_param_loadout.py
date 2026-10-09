"""h605 — ④（逐币单笔规模）**上线前的离线装载演练**（只读生产；R193）。

为什么需要：④ 是**首次**把"字典值"参数写进登记表（`limits.per_symbol_size_mult =
{"BNB":0.5,"NEAR":0.35,"ARB":0.35,"ENA":0.35}`）。本项目的两类历史事故都出在这条缝里：
  · **F189「改了但没生效」**：字段不在运行中的 dataclass 里 ⇒ `(**{k: v for k in
    __dataclass_fields__})` **静默丢弃**该键（h472 的教训：新字段必须先重启 worker）；
  · **热采用路径对值做强转**：一旦哪层写了 `float(v)`，字典会抛 TypeError ⇒ 可能把
    车道 worker 打死（比"没部署"严重得多，因为 ≥60/h 是硬约束）。

本脚本**不写库、不部署**，只做三件事（用 SPEC 里的**真实取值**，不手抄）：
  1. 用 `runner.py` 同款的"过滤 + 关键字构造"把 ④ 的载荷装成 `LaneRiskLimits`；
  2. 用引擎自己的 `symbol_lookup` 验**逐币解析**（BNB 0.5、NEAR/ARB/ENA 0.35、
     其余 1.0；`rollback_to={}` ⇒ 全 1.0 旧行为）；
  3. 验**心跳/指纹所需的 JSON 序列化**（`json.dumps` 整个 params/limits 映射）与
     字段在 `__dataclass_fields__` 里（对应 h481 的"运行态缺键"判据）。

用法：python scripts/h605_dict_param_loadout.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "h425_trial", ROOT / "scripts" / "h425_repair_trial.py")
h425 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h425)  # type: ignore[union-attr]

from backend.services.market_maker.core import (  # noqa: E402
    LaneRiskLimits, QuoteParams, symbol_lookup,
)

LANE_COINS = ["XRP", "BNB", "NEAR", "ARB", "ENA"]


def loadout(stored: dict) -> tuple[QuoteParams, LaneRiskLimits]:
    """与 runner.py 3545-3550 同款：先按 __dataclass_fields__ 过滤，再关键字构造。"""
    p = QuoteParams(**{k: v for k, v in stored.items()
                       if k in QuoteParams.__dataclass_fields__})
    lim = LaneRiskLimits(**{k: v for k, v in stored.items()
                            if k in LaneRiskLimits.__dataclass_fields__})
    return p, lim


def main() -> int:
    print("=" * 88)
    print("h605 — ④ 逐币规模字典参数的离线装载演练（只读）")
    print("=" * 88)
    fails: list[str] = []

    def check(ok: bool, what: str) -> None:
        print(f"  {'✓' if ok else '✗'} {what}")
        if not ok:
            fails.append(what)

    spec = h425.SPECS["h527"]
    to = spec["to"]
    rb = spec["rollback_to"]
    field = spec["field"]                      # limits.per_symbol_size_mult
    key = field.split(".")[-1]
    print(f"  SPEC 取值（真值，非手抄）：{field} = {to}；回滚值 = {rb}")

    # ① 字段必须在**类**里（否则静默丢弃）
    check(key in LaneRiskLimits.__dataclass_fields__,
          f"`{key}` ∈ LaneRiskLimits.__dataclass_fields__（否则会被静默丢弃 ✗）")
    # 运行中的 worker 是否也有该字段 —— 由 h481 的『运行态缺键』判据给出（另跑）

    # ② 装载：不抛异常、值原样保留
    stored = {key: to, "max_one_side_seconds": 90.0, "stop_loss_bp": 40.0}
    try:
        _p, lim = loadout(stored)
        got = getattr(lim, key, "<缺>")
        check(got == to, f"装载后 `{key}` 原样保留（得到 {got!r}）")
    except Exception as exc:  # noqa: BLE001
        check(False, f"装载抛异常：{type(exc).__name__}: {exc}")
        print("-" * 88)
        return 1

    # ③ 逐币解析（引擎自己的 symbol_lookup）
    print("\n  逐币倍数解析（symbol_lookup，缺省 1.0）：")
    expect = {"XRP": 1.0, "BNB": 0.5, "NEAR": 0.35, "ARB": 0.35, "ENA": 0.35}
    for c in LANE_COINS:
        v = symbol_lookup(getattr(lim, key), c, 1.0)
        ok = abs(float(v) - expect[c]) < 1e-12
        print(f"    {c:>5s} ⇒ {v:<5} {'✓' if ok else '✗ 期望 ' + str(expect[c])}")
        if not ok:
            fails.append(f"{c} 倍数解析 = {v}，期望 {expect[c]}")
    # 后缀形式：**契约只承诺分隔符形式**（`XRP-USDT` / `XRP/USDT:USDT`）+ 大小写回退；
    # 拼接形式 `BNBUSDT` **不在契约内**（core.symbol_lookup 只按 `-` `/` `:` 切分、
    # 再把分隔符删掉做候选，不做"剥掉尾部 USDT"的字符串手术）—— 这不是缺陷：
    # 引擎各处传的是**裸币名**（④ 的消费点 `runner.py:980` 传 `state.symbol`），
    # 需要深度表符号的地方有显式转换 `board.to_bare_symbol`。
    # ⚠️ 我第一版在这里断言了 `BNBUSDT`⇒0.5 ⇒ 假警报 ✗（R193）。**假警报也是缺陷**
    # （R84：总在报同一件事的检查，最后没人再看它）⇒ 断言改成契约内的形式。
    for form, exp in (("BNB-USDT", 0.5), ("BNB/USDT:USDT", 0.5), ("bnb", 0.5)):
        v_form = symbol_lookup(getattr(lim, key), form, 1.0)
        check(abs(float(v_form) - exp) < 1e-12,
              f"契约内后缀/大小写形式 {form} ⇒ {v_form}（期望 {exp}）")
    check(abs(float(symbol_lookup(getattr(lim, key), "BNBUSDT", 1.0)) - 1.0) < 1e-12,
          "拼接形式 BNBUSDT ⇒ 1.0（**契约外，按设计不命中**；不要为它加字符串手术 ✓）")
    # 未列出的币必须不受影响
    check(abs(float(symbol_lookup(getattr(lim, key), "XRP", 1.0)) - 1.0) < 1e-12,
          "未列出的币（XRP）保持 1.0 ⇒ 只动指定币 ✓")

    # ④ 回滚值：空字典 ⇒ 全 1.0（旧行为逐字不变）
    _p2, lim2 = loadout({key: rb})
    vals = [symbol_lookup(getattr(lim2, key), c, 1.0) for c in LANE_COINS]
    check(all(abs(float(v) - 1.0) < 1e-12 for v in vals),
          f"回滚值 {rb!r} ⇒ 全币 1.0（{vals}）✓")
    check(not getattr(lim2, key) or getattr(lim2, key) == rb,
          "回滚后值为空/等于回滚值（falsy 安全 ✓）")

    # ⑤ 心跳/指纹所需的 JSON 序列化（runner.py 3512-3514 与 status()）
    try:
        j = json.dumps({k: getattr(lim, k) for k in LaneRiskLimits.__dataclass_fields__},
                       ensure_ascii=False, default=str)
        round_trip = json.loads(j).get(key)
        check(round_trip == to, f"limits 映射可 JSON 往返（得到 {round_trip!r}）")
    except Exception as exc:  # noqa: BLE001
        check(False, f"JSON 序列化失败：{type(exc).__name__}: {exc}")

    # ⑥ 部署脚本侧：_coerce_param 必须放行字典（h543 修的 TypeError）
    try:
        coerced = h425._coerce_param(to)
        check(coerced == to, f"h425._coerce_param 放行字典（{coerced!r}）")
    except AttributeError:
        print("  · h425 无 _coerce_param（跳过该项）")
    except Exception as exc:  # noqa: BLE001
        check(False, f"_coerce_param 抛异常：{type(exc).__name__}: {exc}")

    print("-" * 88)
    if fails:
        print(f"✗ 演练失败 {len(fails)} 项：")
        for f in fails:
            print(f"    · {f}")
        return 1
    print("✓ 演练通过：④ 的字典载荷能装进运行中的类、逐币解析正确、可 JSON 往返、"
          "回滚到空字典即恢复旧行为 ✓")
    print("  ⇒ 部署后仍需读运行态（`h527_chain` 会自动跑 h481 回显验证）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
