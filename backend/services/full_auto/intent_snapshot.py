"""
Loop 开/平仓意图快照工具（C2 特征化测试网）。

将各 full_auto loop 产出的交易意图规范化为可 JSON 序列化、可逐字节对拍的结构，
供拆分 #8 / 对拍 #9 前钉住现状行为。零风险：纯数据转换，不接入实盘路径。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class TradeIntent:
    """单条开/平仓意图（特征化测试用，非下单载荷）。"""

    loop: str
    action: str          # open | close | skip
    symbol: str = ""
    side: str = ""       # long | short | buy | sell
    tier: str = ""       # short | mid | long
    reason: str = ""


@dataclass
class LoopTickSnapshot:
    """单次 loop tick 的特征化快照。"""

    scenario: str
    loop: str
    intents: List[TradeIntent] = field(default_factory=list)
    observed_calls: Dict[str, bool] = field(default_factory=dict)

    def to_canonical_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "loop": self.loop,
            "intents": [asdict(i) for i in self.intents],
            "observed_calls": dict(sorted(self.observed_calls.items())),
        }

    def to_canonical_json(self) -> str:
        return canonical_json(self.to_canonical_dict())


def canonical_json(obj: Any) -> str:
    """稳定 JSON 序列化（golden 对拍用）。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def parse_golden_file(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def assert_golden_match(actual: LoopTickSnapshot, golden_path: str) -> None:
    """实际快照与 golden 文件逐字节一致。"""
    expected = parse_golden_file(golden_path)
    actual_dict = actual.to_canonical_dict()
    assert canonical_json(actual_dict) == canonical_json(expected), (
        f"golden mismatch\nexpected={canonical_json(expected)}\nactual={canonical_json(actual_dict)}"
    )
