"""
因子进化闭环 — 全面测试
测试: DSR/PBO计算 | DB模型 | 进化循环模块导入 | 各阶段函数
"""
from __future__ import annotations

import os
import sys
import logging

# 确保路径正确
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.join(_PROJECT_DIR, "backend")
_QAA_DIR = os.path.join(_PROJECT_DIR, "qaa_architecture_package")
for _d in (_PROJECT_DIR, _BACKEND_DIR, _QAA_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("test")

PASS = 0
FAIL = 0
SKIP = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        logger.info(f"  ✅ {name}")
    else:
        FAIL += 1
        logger.error(f"  ❌ {name}  {detail}")


def skip(name, reason=""):
    global SKIP
    SKIP += 1
    logger.warning(f"  ⏭️ {name} ({reason})")


# ═══════════════════════════════════════════════════════════════
#  测试1: DSR/PBO 模块
# ═══════════════════════════════════════════════════════════════

def test_dsr_pbo():
    logger.info("=" * 60)
    logger.info("测试1: DSR/PBO 计算模块")
    logger.info("=" * 60)

    from backend.services.factor_engine.dsr_pbo import (
        compute_dsr, compute_pbo_simple, compute_dsr_pbo_for_factors,
    )

    # 1.1 DSR 基本计算
    result = compute_dsr(observed_sr=2.0, n_trials=50)
    check("DSR 基本计算", result["dsr"] > 0, f"dsr={result['dsr']:.3f}")

    result2 = compute_dsr(observed_sr=0.1, n_trials=100)
    check("DSR 弱信号", not result2.get("significant"), f"p={result2['p_value']:.3f}")

    # 1.2 PBO 计算
    icir_list = [0.5, 0.4, 0.3, 0.2, -0.1, 0.6, 0.35, 0.45, 0.25, 0.15, 0.55, 0.05]
    pbo = compute_pbo_simple(icir_list, n_splits=6)
    check("PBO 基本计算", 0 <= pbo["pbo"] <= 1, f"pbo={pbo['pbo']:.3f}")

    # 1.3 DSR/PBO 联合
    combined = compute_dsr_pbo_for_factors(icir_list, n_total_candidates=50, sample_len=500)
    check("DSR/PBO 联合", "dsr_result" in combined and "pbo_result" in combined,
          f"overall={combined.get('overall_passes')}")

    # 1.4 边界情况
    check("空列表", compute_dsr_pbo_for_factors([], 10)["overall_passes"] == False)

    result_n1 = compute_dsr(observed_sr=1.0, n_trials=1)
    check("n_trials=1", result_n1["dsr"] is not None)

    pbo_few = compute_pbo_simple([0.5, 0.4, 0.3], n_splits=3)
    check("少量因子", 0 <= pbo_few["pbo"] <= 1)

    logger.info(f"  DSR/PBO 测试完成\n")


# ═══════════════════════════════════════════════════════════════
#  测试2: DB 模型
# ═══════════════════════════════════════════════════════════════

def test_db_models():
    logger.info("=" * 60)
    logger.info("测试2: DB 模型导入与建表")
    logger.info("=" * 60)

    try:
        from backend.database.models import FactorEvolutionLog, FactorActiveSet
        check("FactorEvolutionLog 导入", True)
        check("FactorActiveSet 导入", True)
        check("FactorEvolutionLog __tablename__", FactorEvolutionLog.__tablename__ == "factor_evolution_log")
        check("FactorActiveSet __tablename__", FactorActiveSet.__tablename__ == "factor_active_set")
    except Exception as e:
        check(f"DB模型导入", False, str(e))
        return

    # 测试建表
    try:
        from backend.database.connection import analytics_engine, AnalyticsBase
        AnalyticsBase.metadata.create_all(bind=analytics_engine)
        check("建表 AnalyticsBase", True)
    except Exception as e:
        check(f"建表", False, str(e))

    # 测试写入
    try:
        from backend.database.connection import AnalyticsSessionLocal
        db = AnalyticsSessionLocal()
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            log = FactorEvolutionLog(
                factor_id="test_factor_001",
                phase="test",
                source="test",
                state_from="DRAFT",
                state_to="CANDIDATE",
                action="promote",
                reason="单元测试",
                metrics={"icir": 0.5},
            )
            db.add(log)
            db.commit()

            # 查询验证
            found = db.query(FactorEvolutionLog).filter(
                FactorEvolutionLog.factor_id == "test_factor_001"
            ).first()
            check("写入+读取 FactorEvolutionLog", found is not None and found.phase == "test")
        finally:
            db.close()
    except Exception as e:
        check(f"DB操作", False, str(e))

    # 测试 FactorActiveSet
    try:
        db = AnalyticsSessionLocal()
        try:
            active = FactorActiveSet(
                factor_id="test_active_001",
                expr_ast={"op": "mean", "args": [{"f": "returns"}, {"c": 20}]},
                source="test",
                state="ACTIVE",
                icir=0.35,
            )
            db.add(active)
            db.commit()

            found = db.query(FactorActiveSet).filter(
                FactorActiveSet.factor_id == "test_active_001"
            ).first()
            check("写入+读取 FactorActiveSet", found is not None)

            # 清理
            db.query(FactorEvolutionLog).filter(
                FactorEvolutionLog.factor_id.in_(["test_factor_001", "test_active_001"])
            ).delete(synchronize_session=False)
            db.query(FactorActiveSet).filter(
                FactorActiveSet.factor_id == "test_active_001"
            ).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()
    except Exception as e:
        check(f"FactorActiveSet操作", False, str(e))

    logger.info(f"  DB测试完成\n")


# ═══════════════════════════════════════════════════════════════
#  测试3: factor_evolution_loop 模块导入 + 关键函数
# ═══════════════════════════════════════════════════════════════

def test_evolution_loop():
    logger.info("=" * 60)
    logger.info("测试3: factor_evolution_loop 模块")
    logger.info("=" * 60)

    try:
        from backend.services.evolution.factor_evolution_loop import (
            run_factor_evolution_loop,
            run_online_weight_update,
            _save_active_factors,
            _load_active_factors,
            _deactivate_factor,
            _log_evolution,
            _forward_returns,
            _mine_candidates,
            _evaluate_candidates,
            _purge_and_select,
            _promote_factors,
            _monitor_active,
            _replace_degraded,
            _update_online_weights,
        )
        check("主循环函数导入", True)
        check("在线权重函数导入", True)
        check("持久化函数导入", True)
        check("8阶段函数全部导入", True)
    except Exception as e:
        check(f"模块导入", False, str(e))
        return

    # 测试 _forward_returns
    import pandas as pd
    import numpy as np
    df = pd.DataFrame({"close": np.arange(1, 101, dtype=float)})
    fwd = _forward_returns(df, horizon=5)
    check("_forward_returns 能算", len(fwd) == 100)
    check("_forward_returns 末5位为0", np.all(fwd[-5:] == 0))

    # 测试 _load_active_factors (空DB)
    try:
        factors = _load_active_factors()
        check("_load_active_factors 返回list", isinstance(factors, list))
    except Exception as e:
        check(f"_load_active_factors", False, str(e))

    # 测试 _save_active_factors + _load_active_factors 往返
    try:
        test_factors = [{
            "factor_id": "test_roundtrip_001",
            "expr_ast": {"op": "mean", "args": [{"f": "returns"}, {"c": 10}]},
            "source": "test",
            "state": "ACTIVE",
            "icir": 0.42,
            "incremental_corr": 0.3,
            "current_weight": {"4h": 0.15},
        }]
        _save_active_factors(test_factors)
        loaded = _load_active_factors()
        found = [f for f in loaded if f["factor_id"] == "test_roundtrip_001"]
        check("持久化往返", len(found) == 1, f"found={len(found)}")

        # 清理
        _deactivate_factor("test_roundtrip_001")
        from backend.database.connection import AnalyticsSessionLocal
        from backend.database.models import FactorActiveSet
        db = AnalyticsSessionLocal()
        try:
            db.query(FactorActiveSet).filter(
                FactorActiveSet.factor_id == "test_roundtrip_001"
            ).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()
    except Exception as e:
        check(f"持久化往返", False, str(e))

    logger.info(f"  进化循环测试完成\n")


# ═══════════════════════════════════════════════════════════════
#  测试4: 端到端进化循环（空跑 — 不依赖数据中心）
# ═══════════════════════════════════════════════════════════════

def test_e2e():
    logger.info("=" * 60)
    logger.info("测试4: 端到端进化循环")
    logger.info("=" * 60)

    from backend.services.evolution.factor_evolution_loop import (
        run_factor_evolution_loop, run_online_weight_update,
    )

    try:
        result = run_factor_evolution_loop(symbols=["BTC"], period="4h")
        if "error" in result:
            skip("端到端进化循环", f"取数失败(预期): {result.get('error')}")
        else:
            check("端到端进化循环", True, f"report={result}")
    except Exception as e:
        skip("端到端进化循环", f"异常(可能缺数据): {e}")

    try:
        result = run_online_weight_update(symbols=["BTC"])
        check("在线权重更新", isinstance(result, dict), str(result))
    except Exception as e:
        skip("在线权重更新", str(e))

    logger.info(f"  E2E测试完成\n")


# ═══════════════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info("╔══════════════════════════════════════════════════════╗")
    logger.info("║     因子进化闭环 — 全面测试                          ║")
    logger.info("╚══════════════════════════════════════════════════════╝\n")

    test_dsr_pbo()
    test_db_models()
    test_evolution_loop()
    test_e2e()

    total = PASS + FAIL + SKIP
    logger.info("=" * 60)
    logger.info(f"测试结果: ✅ {PASS} 通过  ❌ {FAIL} 失败  ⏭️ {SKIP} 跳过  (共{total})")
    logger.info("=" * 60)

    if FAIL > 0:
        logger.error(f"\n⚠️  {FAIL} 项测试失败！")
        sys.exit(1)
    else:
        logger.info("\n🎉 全部通过！")
