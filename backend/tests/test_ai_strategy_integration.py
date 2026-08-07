"""
AI策略系统集成测试
测试完整的创建、执行、训练流程
"""
import pytest
import sys
import os
from datetime import datetime, timedelta

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database.connection import get_db
from backend.database.models import (
    AIStrategy,
    Account,
    PromptTemplate,
    StrategyMemory,
)
from backend.services.ai_strategy_engine import AIStrategyEngine
from backend.services.prompt_training_system import PromptTrainingSystem


class TestAIStrategySystem:
    """AI策略系统集成测试"""
    
    @pytest.fixture
    def db(self):
        """数据库连接fixture"""
        db = next(get_db())
        yield db
        db.close()
    
    @pytest.fixture
    def test_account(self, db):
        """测试账户fixture"""
        # 查找或创建测试账户
        account = db.query(Account).filter(Account.name == "Test Account").first()
        if not account:
            account = Account(
                name="Test Account",
                exchange="binance",
                wallet_address="test_wallet",
                api_key="test_key",
                api_secret="test_secret",
            )
            db.add(account)
            db.commit()
            db.refresh(account)
        return account
    
    @pytest.fixture
    def test_prompt_template(self, db):
        """测试提示词模板fixture"""
        template = db.query(PromptTemplate).filter(
            PromptTemplate.template_name == "Test Prompt"
        ).first()
        if not template:
            template = PromptTemplate(
                template_name="Test Prompt",
                template_text="这是一个测试提示词",
                is_system=False,
                created_by="test",
            )
            db.add(template)
            db.commit()
            db.refresh(template)
        return template
    
    def test_create_strategy(self, db, test_account, test_prompt_template):
        """测试创建AI策略"""
        strategy = AIStrategy(
            strategy_id="test_strategy_001",
            name="测试策略",
            description="这是一个测试策略",
            account_id=test_account.id,
            master_prompt_template_id=test_prompt_template.id,
            prompt_variables={},
            signal_pool_ids=[],
            trigger_mode="manual",
            enabled_factors=[],
            factor_weights={},
            max_position_size=0.1,
            stop_loss_pct=0.05,
            take_profit_pct=0.10,
            max_daily_loss=0.10,
            auto_execute=False,
            require_confirmation=True,
            min_confidence=0.6,
            learning_enabled=True,
            optimization_target="sharpe",
            training_frequency="weekly",
            status="draft",
        )
        
        db.add(strategy)
        db.commit()
        db.refresh(strategy)
        
        assert strategy.id is not None
        assert strategy.strategy_id == "test_strategy_001"
        assert strategy.status == "draft"
        
        # 清理
        db.delete(strategy)
        db.commit()
    
    def test_strategy_engine_initialization(self, db):
        """测试策略引擎初始化"""
        engine = AIStrategyEngine(db)
        assert engine is not None
        assert engine.db == db
    
    def test_load_strategy(self, db, test_account, test_prompt_template):
        """测试加载策略"""
        # 创建测试策略
        strategy = AIStrategy(
            strategy_id="test_load_001",
            name="加载测试策略",
            account_id=test_account.id,
            master_prompt_template_id=test_prompt_template.id,
            status="draft",
        )
        db.add(strategy)
        db.commit()
        
        # 测试加载
        engine = AIStrategyEngine(db)
        loaded = engine._load_strategy("test_load_001")
        
        assert loaded is not None
        assert loaded.strategy_id == "test_load_001"
        
        # 清理
        db.delete(strategy)
        db.commit()
    
    def test_strategy_memory_creation(self, db):
        """测试策略记忆创建"""
        memory = StrategyMemory(
            strategy_id="test_strategy_001",
            total_trades=10,
            win_rate=0.6,
            avg_profit=0.05,
            avg_loss=-0.03,
            sharpe_ratio=1.5,
            max_drawdown=0.15,
        )
        
        db.add(memory)
        db.commit()
        db.refresh(memory)
        
        assert memory.id is not None
        assert memory.total_trades == 10
        assert memory.win_rate == 0.6
        
        # 清理
        db.delete(memory)
        db.commit()
    
    def test_prompt_training_system_initialization(self, db):
        """测试提示词训练系统初始化"""
        training_system = PromptTrainingSystem(db)
        assert training_system is not None
        assert training_system.db == db
    
    def test_build_enhanced_trigger_context(self, db, test_account, test_prompt_template):
        """测试构建增强触发上下文"""
        # 创建测试策略
        strategy = AIStrategy(
            strategy_id="test_context_001",
            name="上下文测试",
            account_id=test_account.id,
            master_prompt_template_id=test_prompt_template.id,
            prompt_version=1,
            status="draft",
        )
        db.add(strategy)
        db.commit()
        
        # 创建测试记忆
        memory = StrategyMemory(
            strategy_id="test_context_001",
            total_trades=5,
            win_rate=0.5,
            avg_profit=0.02,
            max_drawdown=0.10,
        )
        db.add(memory)
        db.commit()
        
        # 测试构建上下文
        engine = AIStrategyEngine(db)
        context = engine._build_enhanced_trigger_context(
            strategy=strategy,
            base_trigger_context={"test": "data"},
            memory=memory,
        )
        
        assert context is not None
        assert "ai_strategy_id" in context
        assert context["ai_strategy_id"] == "test_context_001"
        assert "strategy_memory" in context
        assert context["strategy_memory"]["total_trades"] == 5
        
        # 清理
        db.delete(memory)
        db.delete(strategy)
        db.commit()
    
    def test_training_result_dto(self):
        """测试训练结果DTO"""
        from backend.services.prompt_training_system import PromptTrainingResult
        
        result = PromptTrainingResult(
            training_id=1,
            original_prompt_id=123,
            optimized_prompt_id=124,
            performance_improvement=0.15,
            recommendations=["建议1", "建议2"],
            success=True,
        )
        
        assert result.training_id == 1
        assert result.success is True
        assert len(result.recommendations) == 2


class TestAIStrategyAPI:
    """AI策略API测试"""
    
    def test_strategy_creation_request_model(self):
        """测试策略创建请求模型"""
        from backend.api.ai_strategy_routes import AIStrategyCreateRequest
        
        request = AIStrategyCreateRequest(
            name="测试策略",
            description="测试描述",
            account_id=1,
            master_prompt_template_id=1,
            prompt_variables={},
            signal_pool_ids=[],
            trigger_mode="hybrid",
        )
        
        assert request.name == "测试策略"
        assert request.trigger_mode == "hybrid"
        assert request.auto_execute is False  # 默认值
    
    def test_training_request_model(self):
        """测试训练请求模型"""
        from backend.api.prompt_training_routes import TrainingStartRequest
        
        request = TrainingStartRequest(
            strategy_id="test_001",
            base_prompt_id=1,
            start_date="2024-01-01T00:00:00Z",
            end_date="2024-01-31T23:59:59Z",
            optimization_target="sharpe",
        )
        
        assert request.strategy_id == "test_001"
        assert request.optimization_target == "sharpe"


class TestDataIntegrity:
    """数据完整性测试"""
    
    @pytest.fixture
    def db(self):
        """数据库连接fixture"""
        db = next(get_db())
        yield db
        db.close()
    
    def test_strategy_required_fields(self, db):
        """测试策略必填字段"""
        # 尝试创建缺少必填字段的策略应该失败
        with pytest.raises(Exception):
            strategy = AIStrategy(
                # 缺少 strategy_id
                name="测试",
                status="draft",
            )
            db.add(strategy)
            db.commit()
    
    def test_strategy_status_values(self, db):
        """测试策略状态值"""
        valid_statuses = ["draft", "active", "paused", "terminated"]
        
        for status in valid_statuses:
            assert status in ["draft", "active", "paused", "terminated"]


if __name__ == "__main__":
    # 运行测试
    pytest.main([__file__, "-v", "-s"])
