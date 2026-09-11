# -*- coding: utf-8 -*-
"""[§80 / 缺陷 #64] 归因记账链路的**静默失败**必须可见（熔断窗的数据供给）。

背景（§80 核查）：`record_close` 其实是**无条件调用**的（全平 + 部分平各一处），
且实测最近 12 笔全部打标、近 7 天各通道计数与 DB **完全一致** ⇒ 没有"持续漏记"。
但这条链上原本有 **4 处 `logger.debug` 吞异常**，一旦失败：
  * 熔断窗不含本次平仓（`_breaker` 停旧）；
  * 状态不落盘 ⇒ 重启后退化；
  * 打标失败 ⇒ 该仓平仓根本不进归因。
INFO 级生产日志里**一条都看不到**（§41.2/§51.7 同类纪律）。

本测试锁定这四处必须 ≥ WARNING，并防止未来被改回 debug：
  1. `paper_trading_engine` 全平/部分平两处；
  2. `source_attribution` 状态保存 / tag_position 两处。
"""
from __future__ import annotations

import inspect
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

FILES = {
    ROOT / "backend/services/paper_trading_engine.py": (
        "[FusionAttr] 归因记录失败",
        "[FusionAttr] 部分平仓归因失败",
    ),
    ROOT / "backend/services/source_attribution.py": (
        "[SourceAttr] 状态保存失败",
        "[SourceAttr] tag_position 落盘跳过",
    ),
}


def test_attribution_failures_are_not_debug_only():
    for path, markers in FILES.items():
        src = path.read_text(encoding="utf-8", errors="replace")
        for mk in markers:
            hits = [ln.strip() for ln in src.splitlines() if mk in ln]
            assert hits, f"{path.name} 里找不到 {mk!r}（被改名？）"
            for ln in hits:
                assert "logger.debug" not in ln, (
                    f"{path.name}: {mk!r} 仍是 debug 级 —— 归因失败会静默（§80）"
                )
                assert re.search(r"logger\.(warning|error)\(", ln), (
                    f"{path.name}: {mk!r} 必须是 warning/error 级"
                )


def test_source_attribution_emits_warning_on_save_failure(monkeypatch, caplog, tmp_path):
    """行为验证：状态保存失败必须产生 WARNING（而不是静默）。"""
    from backend.services import source_attribution as sa

    a = sa.SourceAttribution()
    a._loaded = True
    a._last_save = 0.0
    monkeypatch.setattr(sa, "_STATE_PATH", str(tmp_path / "nope" / "state.json"))
    # 让写盘必然失败：把目标目录变成文件
    (tmp_path / "nope").write_text("x", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        a._maybe_save(force=True)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("状态保存失败" in m for m in msgs), msgs


def test_record_close_has_two_call_sites_in_paper_engine():
    """数据供给面护栏：全平 + 部分平两处调用必须都在（少一处 ⇒ 窗口少一半样本）。"""
    src = (ROOT / "backend/services/paper_trading_engine.py").read_text(encoding="utf-8")
    assert src.count(".record_close(") >= 2, "paper_trading_engine 的 record_close 调用点少了一处"
    assert "只记 final 腿" in src and "部分平仓腿同样入账" in src
