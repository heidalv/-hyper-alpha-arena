"""
AI Signal-Prompt Integration Service
Handles the deep integration between smart signal generation and prompt management systems.
"""
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime
from sqlalchemy.orm import Session
import json

from backend.database.models import PromptTemplate, SignalDefinition, SignalPool
from backend.services.smart_signal_generator import smart_signal_generator, GeneratedSignalConfig
from backend.services.ai_prompt_generation_service import generate_prompt_with_ai
from repositories import prompt_repo

logger = logging.getLogger(__name__)


@dataclass
class SignalPromptIntegrationResult:
    """Result of signal-prompt integration"""
    success: bool
    signal_config: Optional[GeneratedSignalConfig] = None
    prompt_template: Optional[PromptTemplate] = None
    error: Optional[str] = None


class AISignalPromptIntegrationService:
    """
    Service for deep integration between AI signals and prompt management systems.
    
    This service enables:
    1. Dynamic generation of executable prompts based on quantified signal data
    2. Integration between signal conditions and prompt templates
    3. Bidirectional linking between signals and prompts
    4. Real-time decision enhancement when signals are triggered
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def generate_quantified_prompt_from_signal(
        self,
        db: Session,
        symbol: str,
        direction: str = "auto",
        risk_level: str = "moderate",
        time_window: str = "5m",
        strategy_type: str = "adaptive",
        lookback_days: int = 14
    ) -> SignalPromptIntegrationResult:
        """
        Generate a quantified, executable prompt based on signal analysis.
        
        Args:
            db: Database session
            symbol: Trading symbol
            direction: "auto", "long", or "short"
            risk_level: Risk level for signal
            time_window: Time window for analysis
            strategy_type: Type of strategy
            lookback_days: Days of historical data to analyze
            
        Returns:
            SignalPromptIntegrationResult with generated signal and prompt
        """
        try:
            # Step 1: Generate optimal signal
            signal_config = smart_signal_generator.generate_optimal_signal(
                db=db,
                symbol=symbol,
                direction=direction,
                risk_level=risk_level,
                time_window=time_window,
                strategy_type=strategy_type,
                lookback_days=lookback_days
            )
            
            # Step 2: Convert signal to executable prompt template
            prompt_template = self._create_executable_prompt_template(
                db, signal_config, lookback_days
            )
            
            return SignalPromptIntegrationResult(
                success=True,
                signal_config=signal_config,
                prompt_template=prompt_template
            )
            
        except Exception as e:
            logger.error(f"Error generating quantified prompt from signal: {str(e)}")
            return SignalPromptIntegrationResult(
                success=False,
                error=f"Error generating prompt from signal: {str(e)}"
            )
    
    def _create_executable_prompt_template(
        self,
        db: Session,
        signal_config: GeneratedSignalConfig,
        lookback_days: int
    ) -> PromptTemplate:
        """
        Create an executable prompt template based on signal configuration.
        
        Args:
            db: Database session
            signal_config: Generated signal configuration
            lookback_days: Days of historical data analyzed
            
        Returns:
            PromptTemplate object with executable prompt
        """
        # Build the prompt based on signal configuration and quantified data
        prompt_parts = [
            f"# {signal_config.signal_name} - AI Trading Decision Prompt",
            "",
            "## Objective",
            f"Analyze real-time market data to determine if the {signal_config.strategy_type} strategy conditions are met for {signal_config.symbol}.",
            "",
            "## Historical Data Context",
            f"- Analysis Period: Past {lookback_days} days of data",
            f"- Market State: {signal_config.market_regime_at_creation}",
            f"- Risk Level: {signal_config.risk_level}",
            "",
            "## Signal Configuration",
            f"- Direction: {signal_config.direction.upper()}",
            f"- Strategy: {signal_config.strategy_type}",
            f"- Time Window: {signal_config.trigger_condition.get('time_window', '5m')}",
        ]
        
        # Add technical indicators from AI prompt template
        if signal_config.ai_prompt_template:
            prompt_parts.extend([
                "",
                "## Trigger Conditions",
                signal_config.ai_prompt_template
            ])
        
        # Add backtest metrics for context
        backtest_metrics = signal_config.backtest_metrics
        prompt_parts.extend([
            "",
            "## Historical Performance Metrics",
            f"- Win Rate: {backtest_metrics.get('win_rate', 0)*100:.1f}%",
            f"- Avg Return: {backtest_metrics.get('avg_return', 0):.4f}",
            f"- Sharpe Ratio: {backtest_metrics.get('sharpe_ratio', 0):.2f}",
            f"- Total Trades: {backtest_metrics.get('total_triggers', 0)}",
            f"- Effectiveness Score: {signal_config.effectiveness_score:.1f}/100",
            "",
            "## Risk Management",
            f"- Recommended Stop Loss: {signal_config.recommended_stop_loss_percent:.2f}%",
            f"- Recommended Take Profit: {signal_config.recommended_take_profit_percent:.2f}%",
            f"- Position Size: {signal_config.recommended_position_size:.2f}%",
        ])
        
        # Add quantified data sections
        prompt_parts.extend([
            "",
            "## Quantified Analysis Requirements",
            "When analyzing market data, consider the following quantified thresholds:",
        ])
        
        # Add specific conditions from trigger condition
        trigger_condition = signal_config.trigger_condition
        if 'conditions' in trigger_condition:
            for i, condition in enumerate(trigger_condition['conditions'], 1):
                prompt_parts.append(f"- Condition {i}: {condition.get('metric', 'Unknown')} {condition.get('operator', '')} {condition.get('threshold', 0)}")
        
        # Add JSON output format
        prompt_parts.extend([
            "",
            "## Output Format",
            "Respond in the following JSON format:",
            "```json",
            "{",
            '  "should_trigger": true/false,',
            '  "confidence": 0.0-1.0,',
            '  "direction": "long"/"short"/"none",',
            '  "entry_price_suggestion": number,',
            '  "stop_loss_price": number,',
            '  "take_profit_price": number,',
            '  "reasoning": "brief explanation",',
            '  "risk_warnings": ["warning1", "warning2"]',
            "}",
            "```"
        ])
        
        full_prompt = "\n".join(prompt_parts)
        
        # Create system template (for AI model context)
        system_template = f"""You are an expert cryptocurrency trading analyst. Your job is to evaluate market conditions against predefined signal criteria and provide clear, actionable trading recommendations based on quantified data.

Context:
- Trading pair: {signal_config.symbol}
- Strategy type: {signal_config.strategy_type}
- Historical analysis: {lookback_days} days of data
- Risk level: {signal_config.risk_level}

Analyze the provided market data and determine if the signal conditions are met."""
        
        # Create the prompt template in the database
        template = PromptTemplate(
            key=f"signal_generated_{signal_config.symbol.lower()}_{int(datetime.now().timestamp())}",
            name=f"Signal-Based Prompt: {signal_config.signal_name}",
            description=f"Auto-generated prompt based on {signal_config.strategy_type} signal for {signal_config.symbol}. Generated from {lookback_days}-day historical analysis.",
            template_text=full_prompt,
            system_template_text=system_template,
            is_system="false",  # This is a user-generated template
            is_deleted="false",
            created_by="smart_signal_generator",
            updated_by="smart_signal_generator"
        )
        
        db.add(template)
        db.commit()
        db.refresh(template)
        
        return template
    
    def integrate_existing_signal_with_prompt(
        self,
        db: Session,
        signal_id: int,
        prompt_template_id: Optional[int] = None,
        create_new_prompt: bool = True
    ) -> SignalPromptIntegrationResult:
        """
        Integrate an existing signal with a prompt template.
        
        Args:
            db: Database session
            signal_id: ID of existing signal
            prompt_template_id: Optional ID of existing prompt template to link
            create_new_prompt: Whether to create a new prompt based on signal
            
        Returns:
            SignalPromptIntegrationResult
        """
        try:
            # Get the signal definition
            signal = db.query(SignalDefinition).filter(SignalDefinition.id == signal_id).first()
            if not signal:
                return SignalPromptIntegrationResult(
                    success=False,
                    error=f"Signal with ID {signal_id} not found"
                )
            
            # Parse trigger condition from JSON string
            trigger_condition = json.loads(signal.trigger_condition) if isinstance(signal.trigger_condition, str) else signal.trigger_condition
            
            # If creating a new prompt based on the signal
            if create_new_prompt:
                # We need to recreate the signal config to generate the prompt
                # This is a simplified version - in a real scenario we'd need more context
                prompt_parts = [
                    f"# {signal.signal_name} - AI Trading Decision Prompt",
                    "",
                    "## Objective",
                    f"Analyze real-time market data to determine if the signal conditions are met for {signal.signal_name}.",
                    "",
                    "## Trigger Conditions",
                    f"Signal Name: {signal.signal_name}",
                    f"Description: {signal.description or 'No description'}",
                ]
                
                # Add the parsed conditions
                prompt_parts.append("")
                prompt_parts.append("## Specific Conditions:")
                if 'conditions' in trigger_condition:
                    for i, condition in enumerate(trigger_condition['conditions'], 1):
                        prompt_parts.append(f"- Condition {i}: {condition.get('metric', 'Unknown')} {condition.get('operator', '')} {condition.get('threshold', 0)}")
                
                full_prompt = "\n".join(prompt_parts)
                
                # Create system template
                system_template = f"""You are an expert cryptocurrency trading analyst. Evaluate market conditions against the predefined signal criteria for '{signal.signal_name}' and provide clear trading recommendations."""
                
                # Create the prompt template in the database
                template = PromptTemplate(
                    key=f"linked_signal_{signal_id}_{int(datetime.now().timestamp())}",
                    name=f"Linked Prompt: {signal.signal_name}",
                    description=f"Prompt linked to existing signal: {signal.signal_name}",
                    template_text=full_prompt,
                    system_template_text=system_template,
                    is_system="false",
                    is_deleted="false",
                    created_by="signal_prompt_integrator",
                    updated_by="signal_prompt_integrator"
                )
                
                db.add(template)
                db.commit()
                db.refresh(template)
                
                return SignalPromptIntegrationResult(
                    success=True,
                    signal_config=None,  # We don't have the full config for existing signals
                    prompt_template=template
                )
            else:
                # Just link to existing prompt
                if not prompt_template_id:
                    return SignalPromptIntegrationResult(
                        success=False,
                        error="prompt_template_id is required when create_new_prompt is False"
                    )
                
                template = db.query(PromptTemplate).filter(PromptTemplate.id == prompt_template_id).first()
                if not template:
                    return SignalPromptIntegrationResult(
                        success=False,
                        error=f"Prompt template with ID {prompt_template_id} not found"
                    )
                
                return SignalPromptIntegrationResult(
                    success=True,
                    signal_config=None,
                    prompt_template=template
                )
                
        except Exception as e:
            logger.error(f"Error integrating existing signal with prompt: {str(e)}")
            return SignalPromptIntegrationResult(
                success=False,
                error=f"Error integrating signal with prompt: {str(e)}"
            )
    
    def get_signal_prompt_mapping(
        self,
        db: Session,
        signal_id: Optional[int] = None,
        prompt_template_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get mapping information between signals and prompt templates.
        
        Args:
            db: Database session
            signal_id: Optional signal ID to filter
            prompt_template_id: Optional prompt template ID to filter
            
        Returns:
            Dictionary with mapping information
        """
        # This would typically involve looking for specific connections
        # For now, we'll return placeholder information
        result = {
            "signals": [],
            "prompts": [],
            "mappings": []
        }
        
        # If we had specific mappings stored in a table, we would query them here
        # For now, this is a basic implementation
        
        if signal_id:
            signal = db.query(SignalDefinition).filter(SignalDefinition.id == signal_id).first()
            if signal:
                result["signals"].append({
                    "id": signal.id,
                    "name": signal.signal_name,
                    "description": signal.description
                })
        
        if prompt_template_id:
            template = db.query(PromptTemplate).filter(PromptTemplate.id == prompt_template_id).first()
            if template:
                result["prompts"].append({
                    "id": template.id,
                    "name": template.name,
                    "description": template.description
                })
        
        return result


# Singleton instance
ai_signal_prompt_integration_service = AISignalPromptIntegrationService()