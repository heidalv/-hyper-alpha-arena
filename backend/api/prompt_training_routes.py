"""Prompt Training API Routes

提供提示词训练相关的REST接口：
- 发起训练任务
- 查询训练状态
- 获取训练结果
- 创建优化版本
- A/B测试管理
"""
import logging
from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database.connection import get_db
from backend.database.models import PromptTrainingRecord
from backend.services.prompt_training_system import PromptTrainingSystem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/prompt-training", tags=["Prompt Training"])


# ===== Request/Response Models =====

class TrainingStartRequest(BaseModel):
    """开始训练请求"""
    strategy_id: str
    base_prompt_id: int
    start_date: str  # ISO format
    end_date: str    # ISO format
    optimization_target: str = "sharpe"  # sharpe, win_rate, profit


class TrainingResponse(BaseModel):
    """训练响应"""
    training_id: int
    status: str
    success: bool
    error_message: Optional[str] = None
    recommendations: Optional[list] = None


class OptimizePromptRequest(BaseModel):
    """优化提示词请求"""
    training_id: int
    optimization_instructions: str


class ABTestStartRequest(BaseModel):
    """A/B测试启动请求"""
    strategy_id: str
    prompt_a_id: int
    prompt_b_id: int
    test_duration_days: int = 7


# ===== API Endpoints =====

@router.post("/train", response_model=TrainingResponse)
def start_training(
    request: TrainingStartRequest,
    db: Session = Depends(get_db),
):
    """发起提示词训练任务"""
    try:
        # 解析日期
        start_date = datetime.fromisoformat(request.start_date.replace('Z', '+00:00'))
        end_date = datetime.fromisoformat(request.end_date.replace('Z', '+00:00'))
        
        # 执行训练
        training_system = PromptTrainingSystem(db)
        result = training_system.train_prompt_from_history(
            strategy_id=request.strategy_id,
            base_prompt_id=request.base_prompt_id,
            start_date=start_date,
            end_date=end_date,
            optimization_target=request.optimization_target,
        )
        
        if result.success:
            return TrainingResponse(
                training_id=result.training_id,
                status="completed",
                success=True,
                recommendations=result.recommendations,
            )
        else:
            return TrainingResponse(
                training_id=0,
                status="failed",
                success=False,
                error_message=result.error_message,
            )
            
    except Exception as e:
        logger.error(f"Training start error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{training_id}/status")
def get_training_status(
    training_id: int,
    db: Session = Depends(get_db),
):
    """查询训练状态"""
    training_record = db.query(PromptTrainingRecord).filter(
        PromptTrainingRecord.id == training_id
    ).first()
    
    if not training_record:
        raise HTTPException(status_code=404, detail="Training record not found")
    
    return {
        "training_id": training_record.id,
        "strategy_id": training_record.strategy_id,
        "status": (training_record.training_metrics or {}).get("status", "unknown"),
        "original_prompt_id": training_record.base_prompt_id,
        "optimized_prompt_id": training_record.optimized_prompt_id,
        "created_at": training_record.created_at.isoformat() if training_record.created_at else None,
    }


@router.get("/{training_id}/result")
def get_training_result(
    training_id: int,
    db: Session = Depends(get_db),
):
    """获取训练结果"""
    training_record = db.query(PromptTrainingRecord).filter(
        PromptTrainingRecord.id == training_id
    ).first()
    
    if not training_record:
        raise HTTPException(status_code=404, detail="Training record not found")
    
    m = training_record.training_metrics or {}
    return {
        "training_id": training_record.id,
        "strategy_id": training_record.strategy_id,
        "status": m.get("status", "unknown"),
        "sample_count": m.get("sample_count"),
        "optimization_target": m.get("optimization_target"),
        "baseline_metrics": m.get("baseline_metrics"),
        "recommendations": m.get("optimization_suggestions"),
        "optimized_prompt_id": training_record.optimized_prompt_id,
    }


@router.post("/optimize-prompt")
def create_optimized_prompt(
    request: OptimizePromptRequest,
    db: Session = Depends(get_db),
):
    """基于训练结果创建优化的提示词版本"""
    try:
        training_system = PromptTrainingSystem(db)
        new_prompt_id = training_system.create_optimized_prompt_version(
            training_id=request.training_id,
            optimization_instructions=request.optimization_instructions,
        )
        
        if new_prompt_id:
            return {
                "success": True,
                "optimized_prompt_id": new_prompt_id,
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to create optimized prompt")
            
    except Exception as e:
        logger.error(f"Optimize prompt error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ab-test/start")
def start_ab_test(
    request: ABTestStartRequest,
    db: Session = Depends(get_db),
):
    """启动 prompt 对照（Paper 默认 B 版直接生效）。"""
    try:
        training_system = PromptTrainingSystem(db)
        result = training_system.start_ab_test(
            strategy_id=request.strategy_id,
            prompt_a_id=request.prompt_a_id,
            prompt_b_id=request.prompt_b_id,
            test_duration_days=request.test_duration_days,
        )

        if result.get("ok"):
            return {
                "success": True,
                "training_id": result.get("training_id"),
                "mode": result.get("mode"),
                "message": result.get("message"),
            }
        raise HTTPException(status_code=500, detail=result.get("error") or "Failed to start")
            
    except Exception as e:
        logger.error(f"A/B test start error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ab-test/{training_id}/results")
def get_ab_test_results(
    training_id: int,
    db: Session = Depends(get_db),
):
    """获取A/B测试结果"""
    try:
        training_system = PromptTrainingSystem(db)
        results = training_system.get_ab_test_results(training_id)
        
        if results:
            return results
        else:
            raise HTTPException(status_code=404, detail="A/B test results not found")
            
    except Exception as e:
        logger.error(f"Get A/B test results error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/history")
def get_training_history(
    strategy_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """获取训练历史记录"""
    query = db.query(PromptTrainingRecord)
    
    if strategy_id:
        query = query.filter(PromptTrainingRecord.strategy_id == strategy_id)
    
    records = query.order_by(PromptTrainingRecord.created_at.desc()).limit(50).all()
    
    return [
        {
            "training_id": r.id,
            "strategy_id": r.strategy_id,
            "status": (r.training_metrics or {}).get("status", "unknown"),
            "optimization_target": (r.training_metrics or {}).get("optimization_target"),
            "sample_count": (r.training_metrics or {}).get("sample_count"),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]


@router.post("/recover-stuck")
def recover_stuck_ab_tests(db: Session = Depends(get_db)):
    """修复卡在 ab_testing 但未绑定 B 版 prompt 的训练记录。"""
    training_system = PromptTrainingSystem(db)
    return training_system.recover_stuck_ab_tests()
