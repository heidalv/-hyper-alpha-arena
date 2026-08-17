"""
Factor Sync API Routes — 云端因子库同步管理接口

提供:
- 同步配置 CRUD
- 触发同步
- 云端因子列表/详情/本地化
- 统一因子值查询（合并新旧系统）
"""

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database.connection import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/factors", tags=["factors"])

# 云端因子加载状态标记
_cloud_factors_loaded = False


def _ensure_cloud_factors_loaded():
    """确保已激活的云端因子被加载到 FactorRegistry 中"""
    global _cloud_factors_loaded
    if _cloud_factors_loaded:
        return

    try:
        from backend.database.models import CloudFactorDefinition
        from backend.services.factor_engine.factor_registry import registry
        from backend.services.factor_engine.factor_sync_service import factor_sync_service

        with SessionLocal() as db:
            active_factors = db.query(CloudFactorDefinition).filter(
                CloudFactorDefinition.status == "active",
                CloudFactorDefinition.localized == True,
            ).all()

            for f in active_factors:
                if f.factor_id not in registry._factors and f.localized_path:
                    try:
                        defn = {
                            "factor_id": f.factor_id,
                            "name": f.name,
                        }
                        factor_sync_service._register_localized_factor(f.localized_path, defn)
                    except Exception as e:
                        logger.warning(f"加载云端因子 {f.factor_id} 失败: {e}")

        _cloud_factors_loaded = True
        logger.info(f"[FactorSync] 云端因子加载完成，registry 共 {len(registry._factors)} 个因子")
    except Exception as e:
        logger.error(f"[FactorSync] 云端因子加载异常: {e}")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════
#  请求/响应模型
# ══════════════════════════════════════════════════════════════

class SyncConfigCreate(BaseModel):
    name: str
    repo_url: str
    branch: str = "main"
    sync_path: Optional[str] = None
    enabled: bool = True
    auto_sync: bool = False
    sync_interval_hours: int = 24


class SyncConfigUpdate(BaseModel):
    name: Optional[str] = None
    repo_url: Optional[str] = None
    branch: Optional[str] = None
    sync_path: Optional[str] = None
    enabled: Optional[bool] = None
    auto_sync: Optional[bool] = None
    sync_interval_hours: Optional[int] = None


# ══════════════════════════════════════════════════════════════
#  同步配置管理
# ══════════════════════════════════════════════════════════════

@router.get("/sync/configs")
def list_sync_configs():
    """列出所有同步配置"""
    from backend.services.factor_engine.factor_sync_service import factor_sync_service
    return {"configs": factor_sync_service.get_sync_status()}


@router.post("/sync/configs")
def create_sync_config(config: SyncConfigCreate, db: Session = Depends(get_db)):
    """创建同步配置"""
    from backend.database.models import FactorSyncConfig

    new_config = FactorSyncConfig(
        name=config.name,
        repo_url=config.repo_url,
        branch=config.branch,
        sync_path=config.sync_path,
        enabled=config.enabled,
        auto_sync=config.auto_sync,
        sync_interval_hours=config.sync_interval_hours,
    )
    db.add(new_config)
    db.commit()
    db.refresh(new_config)
    return {"id": new_config.id, "status": "created"}


@router.put("/sync/configs/{config_id}")
def update_sync_config(config_id: int, update: SyncConfigUpdate, db: Session = Depends(get_db)):
    """更新同步配置"""
    from backend.database.models import FactorSyncConfig

    config = db.query(FactorSyncConfig).get(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")

    if update.name is not None:
        config.name = update.name
    if update.repo_url is not None:
        config.repo_url = update.repo_url
    if update.branch is not None:
        config.branch = update.branch
    if update.sync_path is not None:
        config.sync_path = update.sync_path
    if update.enabled is not None:
        config.enabled = update.enabled
    if update.auto_sync is not None:
        config.auto_sync = update.auto_sync
    if update.sync_interval_hours is not None:
        config.sync_interval_hours = update.sync_interval_hours

    db.commit()
    return {"status": "updated"}


@router.delete("/sync/configs/{config_id}")
def delete_sync_config(config_id: int, db: Session = Depends(get_db)):
    """删除同步配置"""
    from backend.database.models import FactorSyncConfig

    config = db.query(FactorSyncConfig).get(config_id)
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")

    db.delete(config)
    db.commit()
    return {"status": "deleted"}


# ══════════════════════════════════════════════════════════════
#  同步操作
# ══════════════════════════════════════════════════════════════

@router.post("/sync/run")
def run_sync(config_id: Optional[int] = Query(None)):
    """触发因子同步（指定 config_id 或同步全部）"""
    from backend.services.factor_engine.factor_sync_service import factor_sync_service
    try:
        result = factor_sync_service.sync_from_repo(config_id=config_id)
        return result
    except Exception as e:
        logger.error(f"[FactorSync] 同步失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])


# ══════════════════════════════════════════════════════════════
#  云端因子管理
# ══════════════════════════════════════════════════════════════

@router.get("/cloud")
def list_cloud_factors(status: Optional[str] = Query(None)):
    """列出云端因子定义"""
    from backend.services.factor_engine.factor_sync_service import factor_sync_service
    return {"factors": factor_sync_service.list_cloud_factors(status=status)}


@router.get("/cloud/{factor_id}")
def get_cloud_factor_detail(factor_id: str, db: Session = Depends(get_db)):
    """获取云端因子详情"""
    from backend.database.models import CloudFactorDefinition

    factor = db.query(CloudFactorDefinition).filter(
        CloudFactorDefinition.factor_id == factor_id
    ).first()

    if not factor:
        raise HTTPException(status_code=404, detail="Factor not found")

    return {
        "factor_id": factor.factor_id,
        "name": factor.name,
        "display_name": factor.display_name,
        "description": factor.description,
        "category": factor.category,
        "subcategory": factor.subcategory,
        "calculation_code": factor.calculation_code,
        "parameters": factor.parameters,
        "required_data_fields": factor.required_data_fields,
        "dependencies": factor.dependencies,
        "version": factor.version,
        "author": factor.author,
        "tags": factor.tags,
        "status": factor.status,
        "localized": factor.localized,
        "localized_path": factor.localized_path,
        "source_repo": factor.source_repo,
        "downloaded_at": str(factor.downloaded_at) if factor.downloaded_at else None,
        "localized_at": str(factor.localized_at) if factor.localized_at else None,
        "error_message": factor.error_message,
    }


@router.post("/cloud/{factor_id}/localize")
def localize_cloud_factor(factor_id: str):
    """手动触发云端因子本地化"""
    from backend.services.factor_engine.factor_sync_service import factor_sync_service
    result = factor_sync_service.localize_single_factor(factor_id)
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result.get("reason", "localization failed"))
    return result


# ══════════════════════════════════════════════════════════════
#  统一因子值查询（合并新旧系统）
# ══════════════════════════════════════════════════════════════

@router.get("/values/{symbol}")
def get_unified_factor_values(
    symbol: str,
    include_new: bool = Query(True),
    timeframe: str = Query("15m"),
):
    """
    获取统一因子值（统一走 FactorService 单一入口，Registry 路径）。

    新注册表已包含 legacy_compat 下的 21 个短名因子（rsi/macd/adx...），
    下游按短名查因子的行为保持不变；同时额外提供 124+ 个新规范名因子。
    """
    from backend.services.factor_engine.factor_service import factor_service
    return factor_service.compute_as_list(symbol, timeframe)


# ══════════════════════════════════════════════════════════════
#  因子信号查询
# ══════════════════════════════════════════════════════════════

@router.get("/signals/{symbol}")
def get_factor_signals(
    symbol: str,
    timeframe: str = Query("15m"),
):
    """获取因子信号合成结果（统一走 FactorService，与 /values 同一计算路径）。"""
    from backend.services.factor_engine.factor_service import factor_service
    return factor_service.signals(symbol, timeframe)


# ══════════════════════════════════════════════════════════════
#  因子 IC 有效性检验（统一入口，供全模块一致调用）
# ══════════════════════════════════════════════════════════════

@router.get("/ic/{symbol}")
def get_factor_ic(
    symbol: str,
    timeframe: str = Query("15m"),
    top_n: int = Query(30, ge=1, le=200),
):
    """对该标的所有已注册因子做 IC/ICIR/衰减/评级批量评估。"""
    from backend.services.factor_engine.factor_service import factor_service
    return factor_service.evaluate_ic(symbol, timeframe, top_n=top_n)


# ══════════════════════════════════════════════════════════════
#  发现因子目录 + 单因子回测打分闸门（阶段二 2.1 / 2.2）
# ══════════════════════════════════════════════════════════════

@router.get("/discovered")
def list_discovered_factors(
    request: Request,
    status: Optional[str] = Query(None),
):
    """列出**当前账户**挖掘的因子（不含平台基础因子；基础因子在代码库共享）。"""
    from backend.core.request_identity import require_user_tenant
    from backend.services.factor_engine.custom_factor_store import custom_factor_store

    _uid, tid = require_user_tenant(request)
    items = custom_factor_store.list(status=status, tenant_id=tid)
    return {
        "scope": "tenant",
        "tenant_id": tid,
        "note": "仅本账户挖掘/训练因子；平台基础因子请走因子引擎内置列表",
        "total": len(items),
        "by_status": {
            "candidate": len(custom_factor_store.list_candidates(tenant_id=tid)),
            "active": len(custom_factor_store.list_active(tenant_id=tid)),
            "rejected": len(custom_factor_store.list(status="rejected", tenant_id=tid)),
        },
        "factors": items,
    }


@router.post("/validate/{factor_id}")
def validate_factor(factor_id: str, request: Request):
    """对单个候选因子做样本外回测打分，A/B 级晋升为 active。"""
    # [2026-08-14 P1-H3 修复] 补租户鉴权：此前该端点无鉴权且内部固定操作管理员
    # 租户目录，任意登录用户（或无租户上下文）可触发管理员因子打分晋升。
    from backend.core.request_identity import require_user_tenant
    require_user_tenant(request)
    from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer
    result = factor_backtest_scorer.validate_and_promote(factor_id)
    return {
        "factor_id": result.factor_id,
        "grade": result.grade,
        "admitted": result.admitted,
        "ic_mean": result.ic_mean,
        "icir": result.icir,
        "ic_decay_halflife": result.ic_decay_halflife,
        "monotonicity": result.monotonicity,
        "oos_net_return": result.oos_net_return,
        "oos_sharpe": result.oos_sharpe,
        "oos_win_rate": result.oos_win_rate,
        "oos_trades": result.oos_trades,
        "redundant_with": result.redundant_with,
        "reason": result.reason,
        "per_symbol": result.per_symbol,
    }


@router.post("/validate")
def validate_all_candidates(
    request: Request,
    limit: int = Query(20, ge=1, le=200),
    wait: bool = Query(False, description="true=同步阻塞返回结果（旧行为）；默认 false=后台异步"),
):
    """批量给候选因子打分晋升（供定时任务/手动触发）。

    默认**后台异步**：立即返回 `job_id`，用 `GET /api/factors/jobs/{job_id}` 轮询进度/结果。
    同类任务单飞——重复触发会复用正在跑的任务。传 `wait=true` 走旧的同步阻塞行为。
    """
    # [2026-08-14 P1-H3 修复] 补租户鉴权（同 /validate/{factor_id}）。
    from backend.core.request_identity import require_user_tenant
    require_user_tenant(request)
    if wait:
        from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer
        return factor_backtest_scorer.validate_all_candidates(limit=limit)

    from backend.services.factor_engine.factor_jobs import run_validate_candidates
    job = run_validate_candidates(limit=limit)
    return {"async": True, **job.to_dict()}


@router.get("/scalp-health")
def scalp_health(
    lookback_days: int = Query(14, ge=1, le=90),
    account_id: Optional[int] = Query(None),
):
    """短线因子健康视图（阶段三 3.3 可观测性）：

    汇总——
    - 滚动胜率 / 净期望 / 笔数（`scalp_health_report`）；
    - EV 闸门放行率、校准器状态；
    - 活跃因子集：数量 / 平均 IC / Top 因子及运行时权重；
    - 发现闸门通过率（active / (active+rejected)）。
    """
    out = {}
    try:
        from backend.services.scalp.scalp_health_report import build_scalp_health
        out = build_scalp_health(lookback_days=lookback_days, account_id=account_id)
    except Exception as e:
        out = {"error": str(e)}

    try:
        from backend.services.factor_engine.scalp_active_factor_set import (
            scalp_active_factor_set,
        )
        snapshot = scalp_active_factor_set.get_health_snapshot()
        out["active_factor_set"] = snapshot
        active = snapshot.get("active") or 0
        rejected = snapshot.get("rejected") or 0
        denom = active + rejected
        out["factor_gate"] = {
            "active": active,
            "rejected": rejected,
            "candidate": snapshot.get("candidate") or 0,
            "pass_rate": round(active / denom, 4) if denom else None,
        }
    except Exception as e:
        out["active_factor_set"] = {"error": str(e)}

    return out


@router.get("/midlong-health")
def midlong_health(
    lookback_days: int = Query(14, ge=1, le=90),
    account_id: Optional[int] = Query(None),
):
    """中长线健康视图（阶段三 C1）：

    每 tier（mid=中线 / long=长线）滚动胜率/净期望/笔数与开仓活跃度、长线周开单 vs
    上限、各层预算利用率、当前生效的开仓门槛、以及中长线激活开关状态。

    [2026-08-17] midlong_health_report 已删除（24h 零活动）：本端点改为从
    trade_facts 实时汇总，不再依赖已删除的报告模块。
    """
    from backend.services.full_auto.midlong_helpers import build_midlong_health_from_facts
    return build_midlong_health_from_facts(lookback_days=lookback_days, account_id=account_id)


@router.post("/alpha101/seed")
def alpha101_seed(request: Request, timeframes: Optional[str] = Query(None, description="逗号分隔，如 4h,1d")):
    """把 Alpha101 风格公式因子库灌为中长线候选（幂等）。"""
    # [2026-08-14 P1-H3 修复] 补租户鉴权（同 /validate/{factor_id}）。
    from backend.core.request_identity import require_user_tenant
    require_user_tenant(request)
    from backend.services.factor_engine.alpha101_factors import seed_alpha101
    tfs = [t.strip() for t in timeframes.split(",")] if timeframes else None
    return seed_alpha101(timeframes=tfs)


@router.post("/alpha101/validate")
def alpha101_validate(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    wait: bool = Query(False, description="true=同步阻塞返回结果（旧行为）；默认 false=后台异步"),
):
    """对已登记的 Alpha101 中长线候选逐个样本外打分晋升（A/B→active）。

    默认**后台异步**：立即返回 `job_id`，用 `GET /api/factors/jobs/{job_id}` 轮询进度/结果。
    同类任务单飞——重复触发会复用正在跑的任务。传 `wait=true` 走旧的同步阻塞行为。
    """
    # [2026-08-14 P1-H3 修复] 补租户鉴权（同 /validate/{factor_id}）。
    from backend.core.request_identity import require_user_tenant
    require_user_tenant(request)
    if wait:
        from backend.services.factor_engine.alpha101_factors import validate_alpha101
        return validate_alpha101(limit=limit)

    from backend.services.factor_engine.factor_jobs import run_validate_alpha101
    job = run_validate_alpha101(limit=limit)
    return {"async": True, **job.to_dict()}


# ══════════════════════════════════════════════════════════════
#  后台任务状态（因子重活异步化，轮询用）
# ══════════════════════════════════════════════════════════════

@router.get("/jobs")
def list_factor_jobs(limit: int = Query(20, ge=1, le=100)):
    """列出最近的因子后台任务（打分/验证等重活）。"""
    from backend.services.factor_engine.factor_jobs import factor_job_manager
    return {"jobs": factor_job_manager.list(limit=limit)}


@router.get("/jobs/{job_id}")
def get_factor_job(job_id: str):
    """查询单个因子后台任务的状态/进度/结果。"""
    from backend.services.factor_engine.factor_jobs import factor_job_manager
    job = factor_job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.get("/midlong-factor-set")
def midlong_factor_set():
    """中长线活跃因子集健康快照（数量/平均IC/按时间框架/Top 因子）。"""
    from backend.services.factor_engine.midlong_active_factor_set import (
        midlong_active_factor_set,
    )
    return midlong_active_factor_set.get_health_snapshot()


# ══════════════════════════════════════════════════════════════
#  短线元标签（真假信号过滤器）：训练报告 + 实时采集进度 + 手动训练
# ══════════════════════════════════════════════════════════════

@router.get("/scalp-meta-report")
def scalp_meta_report():
    """短线元标签模型：最近一次训练报告 + 实时"独立样本采集进度"。

    - `progress`：去重后的独立样本数 / 门槛（进度条），实时查库。
    - `report`：最近一次训练/验证结果（样本外 AUC、过滤后胜率/净收益、因子重要性、
      是否 usable）。样本不足时 report.status=insufficient。
    """
    from backend.services import scalp_meta_trainer
    return {
        "progress": scalp_meta_trainer.sample_progress(),
        "report": scalp_meta_trainer.get_report(),
    }


@router.post("/scalp-meta/train")
def scalp_meta_train():
    """手动触发短线元标签训练+验证（后台异步，单飞）。

    立即返回 `job_id`，用 `GET /api/factors/jobs/{job_id}` 轮询。样本不足会优雅跳过。
    """
    from backend.services.factor_engine.factor_jobs import run_train_scalp_meta
    job = run_train_scalp_meta()
    return {"async": True, **job.to_dict()}


@router.get("/tp-sl/status")
def tp_sl_learned_status():
    """止盈止损训练结果：是否启用、各周期最优 (tp,sl)、更新时间。"""
    from backend.services.risk.tp_sl_grid_trainer import get_status

    return get_status()


@router.post("/tp-sl/auto")
def tp_sl_train_auto(body: dict = Body(default_factory=dict)):
    """开启/关闭 TP/SL 自动训练（默认开；每日 05:00 + 启动补训）。"""
    from backend.services.compute.compute_config import update
    from backend.services.risk.tp_sl_grid_trainer import get_status

    enabled = bool((body or {}).get("enabled", True))
    upd = update({"RISK_TP_SL_TRAIN_AUTO": enabled})
    if not upd.get("ok"):
        raise HTTPException(status_code=400, detail=upd.get("errors"))
    st = get_status()
    return {
        "ok": True,
        "auto_train": enabled,
        "status": st,
        "message": (
            "已开启：每天凌晨 5 点自动训练；缺结果或超过 36 小时会在启动后补训"
            if enabled
            else "已关闭自动训练（仍可手动点「训练 TP/SL」）"
        ),
    }


@router.post("/tp-sl/train")
def tp_sl_train(tiers: Optional[str] = Query(None, description="逗号分隔: short,mid,long")):
    """手动触发 TP/SL 网格训练（后台异步，单飞）。

    立即返回 `job_id`，用 `GET /api/factors/jobs/{job_id}` 轮询。
    结果写入 ``backend/data/tp_sl_learned/latest.json``，开仓时自动覆盖静态表。
    """
    from backend.services.factor_engine.factor_jobs import run_train_tp_sl

    tier_list = None
    if tiers:
        tier_list = [t.strip().lower() for t in tiers.split(",") if t.strip()]
    job = run_train_tp_sl(tiers=tier_list)
    return {"async": True, **job.to_dict()}
