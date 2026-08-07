"""
M1 数据中心收口检查（设计文档 §1.2）

业务/API/策略代码不得直接读取 crypto_klines；只允许以下白名单：
  1. backend/services/data_center.py             （数据中心本体）
  2. backend/services/kline_data_service.py       （存储层实现）
  3. backend/services/kline_realtime_collector.py（写入/计数）
  4. 明确标注的分析/回测/RL/维护工具（读取市场库作为存储）：
     cycle_direction_probability / history_loader / parity_score /
     real_factor_backtest / exchange_data_profile / kline_freshness_inspector /
     kline_quality_repair / db_maintenance / universe_manager /
     kline_history_sync / kline_sync_meta / strategy_evolver /
     strategy_optimizer_service / drl_train_job / rl/system_coordinator /
     unified_data_pool / market_data_shadow_compare

用法：python scripts/check_kline_ingress.py
退出码 0 = 无新增直读点；1 = 发现未白名单直读。
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "backend" / "services"

# 允许直读 crypto_klines 的白名单（相对 backend/services 的路径片段）
ALLOWED = (
    "data_center.py",
    "kline_data_service.py",
    "kline_realtime_collector.py",
    "cycle_direction_probability.py",
    "data/history_loader.py",
    "backtest_engine/parity_score.py",
    "data/real_factor_backtest.py",
    "exchange_data_profile.py",
    "kline_freshness_inspector.py",
    "kline_quality_repair_service.py",
    "db_maintenance.py",
    "alpha/universe_manager.py",
    "kline_history_sync.py",
    "kline_sync_meta.py",
    "strategy_evolver.py",
    "strategy_optimizer_service.py",
    "rl/drl_train_job.py",
    "rl/system_coordinator.py",
    "unified_data_pool.py",
    "market_data_shadow_compare.py",
)


def main() -> int:
    result = subprocess.run(
        [
            "rg", "-l",
            r"FROM crypto_klines|crypto_klines WHERE|query\(CryptoKline\)",
            str(SERVICES), "-g", "*.py",
        ],
        capture_output=True,
        text=True,
    )
    files = [f for f in result.stdout.splitlines() if f.strip()]
    bad = [
        f for f in files
        if not any(a in f.replace("\\", "/") for a in ALLOWED)
    ]
    print(f"直读点共 {len(files)} 个，白名单外 {len(bad)} 个")
    for f in files:
        mark = "OK " if f not in bad else "BAD"
        print(f"  [{mark}] {f}")
    if bad:
        print("\n发现白名单外直读点，请改走 data_center / kline_data_service")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
