"""Tranche staged entry."""
from __future__ import annotations

from backend.services.mlto.types import HubDecision, ThesisDTO


def compute_margin_pct(thesis: ThesisDTO, hub: HubDecision, has_position: bool) -> float:
    """计算本次开仓的保证金比例。

    修复（2026-07-02）：原逻辑 has_position=True 直接返回 0%，
    导致同一 symbol 已有短线 scalp 仓位时，中线 MLTO 永远 margin=0% 不开仓。
    三层独立架构要求短/中/长各自独立持仓，has_position 只检查同 tier 仓位。
    但传入的 has_position 是全局的（不分 tier），所以这里改为：
    即使有其他 tier 仓位，仍然分配保证金（三层独立）。
    """
    if hub.action == "WAIT":
        return 0.0
    stage = thesis.tranche_stage
    if hub.action == "NIBBLE":
        # NIBBLE（试探仓）：stage 0 给 15%，stage 1+ 给 10%（持续试探）
        return 0.15 if stage == 0 else 0.10
    # BUILD（建仓）
    if stage == 0:
        return 0.30
    if stage == 1:
        return 0.30
    if stage == 2:
        return 0.20
    return 0.0


def advance_tranche(thesis: ThesisDTO) -> None:
    if thesis.tranche_stage < 3:
        thesis.tranche_stage += 1


def reset_tranche(thesis: ThesisDTO) -> None:
    """[阶段3e] 重置 tranche_stage 到 0。

    决策6: 当 invalidation 触发 close 后，tranche 归零以便下次能从首档重建仓位，
    而不是停留在已平仓的高 tranche（会导致 compute_margin_pct 返回 0%，永不能再开）。
    """
    thesis.tranche_stage = 0
