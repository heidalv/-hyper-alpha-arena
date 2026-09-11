"""套利引擎包 — 资金费率/基差套利（Phase 2 起，V3 统计套利）。

[2026-09-02] 本文件此前为空（自初始提交即空）。test_phase2_arbitrage.py 的
TestPackageImports 三个用例一直红：包级 __all__、__version__、以及
`from backend.services.arbitrage import opportunity_scanner, hedge_risk_assessor,
arb_monitor` 全部取不到。属于"规格已写、实现未补"，现按测试表达的设计意图补齐。

对外只暴露稳定入口：数据模型、三个模块级单例及其类。现有代码用完整子模块路径
（如 arbitrage.risk_assessor）导入的写法不受影响，两种方式指向同一对象。
"""

from backend.services.arbitrage.models import (
    ArbitrageOpportunity,
    ArbitrageStatus,
    FundingRateSnapshot,
    HedgePosition,
    HedgeRiskCheckResult,
)
from backend.services.arbitrage.opportunity_scanner import (
    OpportunityScanner,
    opportunity_scanner,
)
from backend.services.arbitrage.position_monitor import (
    ArbitragePositionMonitor,
    arb_monitor,
)
from backend.services.arbitrage.risk_assessor import (
    HedgeRiskAssessor,
    hedge_risk_assessor,
)

__version__ = "3.0.0"

__all__ = [
    # 数据模型
    "ArbitrageOpportunity",
    "ArbitrageStatus",
    "FundingRateSnapshot",
    "HedgePosition",
    "HedgeRiskCheckResult",
    # 机会扫描
    "OpportunityScanner",
    "opportunity_scanner",
    # 对冲风控
    "HedgeRiskAssessor",
    "hedge_risk_assessor",
    # 生命周期监控
    "ArbitragePositionMonitor",
    "arb_monitor",
]
