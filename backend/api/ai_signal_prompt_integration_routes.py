"""
AI Signal-Prompt Integration API Routes
Provides endpoints for deep integration between smart signals and prompt management systems.
"""
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from backend.database.connection import get_db
from backend.services.ai_signal_prompt_integration_service import ai_signal_prompt_integration_service, SignalPromptIntegrationResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/signal-prompt-integration", tags=["AI Signal-Prompt Integration"])


# ============================================================================
# Request/Response Models
# ============================================================================

class GenerateQuantifiedPromptRequest(BaseModel):
    """Request for generating quantified prompt from signal analysis"""
    symbol: str = Field(..., description="Trading symbol (e.g., BTC)")
    direction: str = Field("auto", description="Direction: auto, long, short")
    risk_level: str = Field("moderate", description="Risk level: conservative, moderate, aggressive")
    time_window: str = Field("5m", description="Time window: 1m, 5m, 15m, 1h")
    strategy_type: str = Field("adaptive", description="Strategy: trend, reversal, breakout, scalping, adaptive")
    lookback_days: int = Field(14, ge=1, le=90, description="Number of days of historical data to analyze (1-90)")


class IntegrateSignalWithPromptRequest(BaseModel):
    """Request for integrating existing signal with prompt template"""
    signal_id: int = Field(..., description="ID of existing signal to integrate")
    prompt_template_id: Optional[int] = Field(None, description="ID of existing prompt template to link (optional)")
    create_new_prompt: bool = Field(True, description="Whether to create a new prompt based on signal")


class SignalPromptMappingRequest(BaseModel):
    """Request for getting signal-prompt mappings"""
    signal_id: Optional[int] = Field(None, description="Optional signal ID to filter")
    prompt_template_id: Optional[int] = Field(None, description="Optional prompt template ID to filter")


# ============================================================================
# Integration Endpoints
# ============================================================================

@router.post("/generate-quantified-prompt")
def generate_quantified_prompt_from_signal(
    request: GenerateQuantifiedPromptRequest,
    db: Session = Depends(get_db)
):
    """
    Generate a quantified, executable prompt based on signal analysis.
    
    This endpoint creates an AI prompt that is directly tied to signal conditions
    and backed by quantified historical data analysis.
    """
    try:
        result: SignalPromptIntegrationResult = ai_signal_prompt_integration_service.generate_quantified_prompt_from_signal(
            db=db,
            symbol=request.symbol,
            direction=request.direction,
            risk_level=request.risk_level,
            time_window=request.time_window,
            strategy_type=request.strategy_type,
            lookback_days=request.lookback_days
        )
        
        if not result.success:
            raise HTTPException(status_code=500, detail=result.error)
        
        response_data = {
            "success": True,
            "signal": None,
            "prompt_template": None
        }
        
        # Convert signal config to dict if available
        if result.signal_config:
            from dataclasses import asdict
            response_data["signal"] = asdict(result.signal_config)
        
        # Convert prompt template to dict if available
        if result.prompt_template:
            response_data["prompt_template"] = {
                "id": result.prompt_template.id,
                "key": result.prompt_template.key,
                "name": result.prompt_template.name,
                "description": result.prompt_template.description,
                "template_text": result.prompt_template.template_text,
                "system_template_text": result.prompt_template.system_template_text,
                "created_at": result.prompt_template.created_at.isoformat() if result.prompt_template.created_at else None
            }
        
        return response_data
        
    except Exception as e:
        logger.error(f"Error in generate_quantified_prompt_from_signal: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/integrate-existing-signal")
def integrate_existing_signal_with_prompt(
    request: IntegrateSignalWithPromptRequest,
    db: Session = Depends(get_db)
):
    """
    Integrate an existing signal with a prompt template.
    
    This endpoint allows connecting existing signals to new or existing prompt templates,
    creating a bridge between the signal system and prompt management.
    """
    try:
        result: SignalPromptIntegrationResult = ai_signal_prompt_integration_service.integrate_existing_signal_with_prompt(
            db=db,
            signal_id=request.signal_id,
            prompt_template_id=request.prompt_template_id,
            create_new_prompt=request.create_new_prompt
        )
        
        if not result.success:
            raise HTTPException(status_code=500, detail=result.error)
        
        response_data = {
            "success": True,
            "prompt_template": None
        }
        
        # Convert prompt template to dict if available
        if result.prompt_template:
            response_data["prompt_template"] = {
                "id": result.prompt_template.id,
                "key": result.prompt_template.key,
                "name": result.prompt_template.name,
                "description": result.prompt_template.description,
                "template_text": result.prompt_template.template_text,
                "system_template_text": result.prompt_template.system_template_text,
                "created_at": result.prompt_template.created_at.isoformat() if result.prompt_template.created_at else None
            }
        
        return response_data
        
    except Exception as e:
        logger.error(f"Error in integrate_existing_signal_with_prompt: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/get-signal-prompt-mapping")
def get_signal_prompt_mapping(
    request: SignalPromptMappingRequest,
    db: Session = Depends(get_db)
):
    """
    Get mapping information between signals and prompt templates.
    
    Provides bidirectional lookup between signals and prompts to enable
    cross-referencing and enhanced workflow.
    """
    try:
        mapping_info = ai_signal_prompt_integration_service.get_signal_prompt_mapping(
            db=db,
            signal_id=request.signal_id,
            prompt_template_id=request.prompt_template_id
        )
        
        return {
            "success": True,
            "mapping": mapping_info
        }
        
    except Exception as e:
        logger.error(f"Error in get_signal_prompt_mapping: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/create-signal-from-prompt")
def create_signal_from_prompt(
    prompt_id: int,
    db: Session = Depends(get_db)
):
    """
    Create a signal definition based on an existing prompt template.
    
    This endpoint enables reverse mapping - creating signals from prompts
    to support bidirectional workflow between the two systems.
    """
    try:
        # This is a placeholder implementation
        # In a full implementation, we would analyze the prompt template
        # to extract signal conditions and create a corresponding signal
        from database.models import PromptTemplate
        prompt = db.query(PromptTemplate).filter(PromptTemplate.id == prompt_id).first()
        
        if not prompt:
            raise HTTPException(status_code=404, detail=f"Prompt template with ID {prompt_id} not found")
        
        # In a real implementation, we would parse the prompt to extract:
        # - Trigger conditions
        # - Direction indicators
        # - Risk parameters
        # - Time windows
        # And convert these into signal definitions
        
        return {
            "success": True,
            "message": "Placeholder endpoint - would create signal from prompt in full implementation",
            "prompt_id": prompt_id,
            "prompt_name": prompt.name
        }
        
    except Exception as e:
        logger.error(f"Error in create_signal_from_prompt: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Enhanced Signal Generation with Prompt Output
# ============================================================================

@router.post("/enhanced-generate-signal-with-prompt")
def enhanced_generate_signal_with_prompt(
    request: GenerateQuantifiedPromptRequest,
    db: Session = Depends(get_db)
):
    """
    Enhanced signal generation that returns both signal and AI-ready prompt.
    
    This endpoint combines signal generation with prompt generation in a single call,
    ensuring perfect alignment between signal conditions and AI decision-making prompts.
    """
    try:
        result: SignalPromptIntegrationResult = ai_signal_prompt_integration_service.generate_quantified_prompt_from_signal(
            db=db,
            symbol=request.symbol,
            direction=request.direction,
            risk_level=request.risk_level,
            time_window=request.time_window,
            strategy_type=request.strategy_type,
            lookback_days=request.lookback_days
        )
        
        if not result.success:
            raise HTTPException(status_code=500, detail=result.error)
        
        response_data = {
            "success": True,
            "signal_and_prompt": {
                "signal": None,
                "prompt_template": None
            }
        }
        
        # Convert signal config to dict if available
        if result.signal_config:
            from dataclasses import asdict
            response_data["signal_and_prompt"]["signal"] = asdict(result.signal_config)
        
        # Convert prompt template to dict if available
        if result.prompt_template:
            response_data["signal_and_prompt"]["prompt_template"] = {
                "id": result.prompt_template.id,
                "key": result.prompt_template.key,
                "name": result.prompt_template.name,
                "description": result.prompt_template.description,
                "template_text": result.prompt_template.template_text,
                "system_template_text": result.prompt_template.system_template_text,
                "created_at": result.prompt_template.created_at.isoformat() if result.prompt_template.created_at else None
            }
        
        return response_data
        
    except Exception as e:
        logger.error(f"Error in enhanced_generate_signal_with_prompt: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))