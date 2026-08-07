"""ConstitutionalProfile — Paper/Live 风控分层配置。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ConstitutionalProfile:
    mode: Literal["paper", "live"]
    enforce_layers: frozenset
    override_allowed: bool
    probe_enabled: bool
    constitutional_risk_enabled: bool

    @classmethod
    def for_mode(cls, mode: str) -> "ConstitutionalProfile":
        m = (mode or "paper").strip().lower()
        try:
            from backend.config.settings import LIVE_CONSTITUTIONAL_RISK_ENABLED
            live_const = bool(LIVE_CONSTITUTIONAL_RISK_ENABLED)
        except Exception:
            live_const = True

        if m == "live":
            return cls(
                mode="live",
                enforce_layers=frozenset({0, 1}),
                override_allowed=False,
                probe_enabled=False,
                constitutional_risk_enabled=live_const,
            )
        return cls(
            mode="paper",
            enforce_layers=frozenset({1, 2}),
            override_allowed=True,
            probe_enabled=True,
            constitutional_risk_enabled=False,
        )

    def allows_paper_probe(self) -> bool:
        return self.mode == "paper" and self.probe_enabled

    def requires_constitutional_check(self) -> bool:
        return self.mode == "live" and self.constitutional_risk_enabled


def get_profile(mode: str) -> ConstitutionalProfile:
    return ConstitutionalProfile.for_mode(mode)
