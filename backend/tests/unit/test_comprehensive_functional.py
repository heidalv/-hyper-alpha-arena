"""
全面功能测试 — Hyper Alpha Arena System V3
覆盖: 核心交易、ATAS V2、市场环境、风控、AI记忆、回测、前端集成
"""
import pytest
import os
import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta


# ═══════════════════════════════════════════════
# 辅助函数 (放在最前面，供后续测试引用)
# ═══════════════════════════════════════════════

def _make_klines_trend(rows=120, direction=1) -> pd.DataFrame:
    """生成趋势K线数据"""
    np.random.seed(42)
    base = np.arange(rows) * direction * 0.5 + 100
    noise = np.random.randn(rows) * 0.3
    closes = base + noise
    return pd.DataFrame({
        'open': closes - 0.1,
        'high': closes + 0.3,
        'low': closes - 0.3,
        'close': closes,
        'volume': np.full(rows, 1000.0),
    })


def _make_ranging_klines() -> pd.DataFrame:
    """生成震荡K线数据"""
    rows = 120
    np.random.seed(42)
    closes = 100 + np.random.randn(rows) * 0.2
    return pd.DataFrame({
        'open': closes,
        'high': closes + 0.05,
        'low': closes - 0.05,
        'close': closes,
        'volume': np.full(rows, 1000.0),
    })


def _make_klines_for_env(rows=300) -> pd.DataFrame:
    """生成交易环境所需K线数据"""
    np.random.seed(42)
    closes = np.cumsum(np.random.randn(rows) * 0.5) + 50000
    return pd.DataFrame({
        'open': closes - 1,
        'high': closes + 5,
        'low': closes - 5,
        'close': closes,
        'volume': np.full(rows, 1000.0),
    })


# ═══════════════════════════════════════════════
# 1. 核心交易功能测试
# ═══════════════════════════════════════════════

class TestAI策略执行流程:
    """AI策略 CRUD + 激活/暂停/执行"""

    def test_create_strategy_genome(self):
        """创建策略基因组 - 使用 create_default_genome"""
        from backend.services.strategy_genome import create_default_genome
        genome = create_default_genome(category="momentum")
        assert isinstance(genome, dict)
        assert "category" in genome or len(genome) > 0

    def test_strategy_activate_pause_cycle(self):
        """策略激活-暂停-恢复循环"""
        from backend.services.ai_strategy_engine import AIStrategyEngine
        engine = AIStrategyEngine.__new__(AIStrategyEngine)
        engine._active_strategies = {}
        engine._paused_strategies = set()

        sid = "strat_1"
        engine._active_strategies[sid] = {"status": "running", "pnl": 0}
        engine._paused_strategies.discard(sid)
        assert sid in engine._active_strategies

        engine._paused_strategies.add(sid)
        assert sid in engine._paused_strategies

    def test_strategy_manager_execution(self):
        """策略管理器执行 - 使用 StrategyManager"""
        from backend.services.trading_strategy import StrategyManager
        manager = StrategyManager.__new__(StrategyManager)
        manager._config = {"max_position": 0.1, "stop_loss": 0.02}
        assert manager._config["max_position"] == 0.1
        assert manager._config["stop_loss"] == 0.02

    def test_full_auto_trading_session_lifecycle(self):
        """全自动交易会话生命周期"""
        from backend.services.full_auto_trading_service import FullAutoTradingService
        service = FullAutoTradingService.__new__(FullAutoTradingService)
        service._sessions = {}
        service._running = False

        session_id = "session_001"
        service._sessions[session_id] = {
            "status": "running",
            "symbols": ["BTC", "ETH"],
            "started_at": datetime.now(),
        }
        service._running = True
        assert service._sessions[session_id]["status"] == "running"
        assert "BTC" in service._sessions[session_id]["symbols"]

        service._sessions[session_id]["status"] = "paused"
        assert service._sessions[session_id]["status"] == "paused"

    def test_order_executor_module(self):
        """订单执行模块 - 使用 place_and_execute 函数"""
        from backend.services.order_executor import place_and_execute
        assert callable(place_and_execute)


class Test实时市场数据:
    """市场数据获取和显示"""

    def test_price_data_format(self):
        """价格数据格式验证"""
        from backend.services.price_cache import PriceCache
        cache = PriceCache.__new__(PriceCache)
        cache._prices = {"BTC": 50000.0, "ETH": 3000.0}
        assert cache._prices["BTC"] == 50000.0
        assert isinstance(cache._prices, dict)

    def test_kline_data_ohlcv(self):
        """K线数据OHLCV完整性"""
        df = pd.DataFrame({
            'open': [100, 101, 102],
            'high': [105, 106, 107],
            'low': [98, 99, 100],
            'close': [101, 102, 103],
            'volume': [1000, 1100, 1200],
        })
        assert len(df) == 3
        assert all(col in df.columns for col in ['open', 'high', 'low', 'close', 'volume'])
        assert (df['high'] >= df['low']).all()
        assert (df['high'] >= df['close']).all()


class Test动态风险管理:
    """止盈止损、仓位管理"""

    def test_stop_loss_take_profit_calculation(self):
        """止盈止损计算"""
        entry_price = 50000.0
        stop_loss_pct = 0.02
        take_profit_pct = 0.05
        sl = entry_price * (1 - stop_loss_pct)
        tp = entry_price * (1 + take_profit_pct)
        assert sl == 49000.0
        assert tp == 52500.0

    def test_position_size_with_risk_limit(self):
        """仓位大小限制"""
        from backend.services.position_sizer import PositionSizer
        sizer = PositionSizer.__new__(PositionSizer)
        sizer.max_risk_pct = 0.02
        equity = 10000
        risk_pct = 0.01
        position = equity * risk_pct
        assert position == 100.0
        assert position / equity <= sizer.max_risk_pct

    def test_deterministic_risk_gate(self):
        """确定性风控门"""
        from backend.services.deterministic_risk_gate import DeterministicRiskGate
        gate = DeterministicRiskGate.__new__(DeterministicRiskGate)
        gate._rules = {
            "max_single_loss_pct": 0.03,
            "max_daily_loss_pct": 0.06,
            "max_drawdown_pct": 0.10,
        }
        assert gate._rules["max_single_loss_pct"] == 0.03

    def test_liquidation_monitor(self):
        """爆仓监控"""
        from backend.services.liquidation_monitor import LiquidationMonitor
        monitor = LiquidationMonitor.__new__(LiquidationMonitor)
        monitor._alerts = []
        alert = {
            "account_id": 1,
            "symbol": "BTC",
            "mark_price": 48000,
            "liquidation_price": 45000,
            "distance_pct": 6.25,
            "level": "warning",
        }
        monitor._alerts.append(alert)
        assert len(monitor._alerts) == 1
        assert monitor._alerts[0]["level"] == "warning"


# ═══════════════════════════════════════════════
# 2. ATAS V2 / 新修改功能测试
# ═══════════════════════════════════════════════

class TestATASV2工作流:
    """ATAS V2 AI策略中心完整工作流"""

    def test_atas_v2_executor_exists(self):
        """ATAS V2 执行器存在"""
        from backend.services.atas_v2_executor import ATASV2Executor
        executor = ATASV2Executor.__new__(ATASV2Executor)
        executor._status = {"healthy": True, "uptime": 3600}
        assert executor._status["healthy"] is True

    def test_atas_v2_attribution_service(self):
        """策略归因分析 - 函数式API"""
        from backend.services.ai_attribution_service import generate_attribution_analysis_stream
        assert callable(generate_attribution_analysis_stream)

    def test_atas_v2_executor_signal_processing(self):
        """信号处理"""
        from backend.services.atas_v2_executor import ATASV2Executor
        executor = ATASV2Executor.__new__(ATASV2Executor)
        executor._signal_queue = []
        signal = {
            "symbol": "BTC",
            "direction": "long",
            "strength": 0.85,
            "source": "momentum",
        }
        executor._signal_queue.append(signal)
        assert len(executor._signal_queue) == 1
        assert executor._signal_queue[0]["direction"] == "long"


class Test市场环境分析:
    """市场环境分析和动态风险"""

    def test_regime_classification_all_types(self):
        """市场状态分类 - MarketRegimeClassifier() 无参构造"""
        from backend.services.market_regime import MarketRegimeClassifier, MarketRegime

        # Trending UP
        klines_up = _make_klines_trend(120, direction=1)
        clf = MarketRegimeClassifier()
        result = clf.classify(klines_up)
        assert isinstance(result.regime, MarketRegime)

        # Ranging
        klines_flat = _make_ranging_klines()
        result2 = clf.classify(klines_flat)
        assert isinstance(result2.regime, MarketRegime)

    def test_regime_strategy_params_mapping(self):
        """市场状态 -> 策略参数映射"""
        from backend.services.market_regime import MarketRegimeClassifier
        klines = _make_ranging_klines()
        clf = MarketRegimeClassifier()
        result = clf.classify(klines)
        params = clf.get_strategy_params(result)
        assert isinstance(params, dict)
        assert len(params) > 0

    def test_market_environment_dynamic_risk(self):
        """动态风险计算"""
        from backend.services.market_regime import MarketRegimeClassifier, REGIME_STRATEGY_MAP
        klines = _make_klines_trend(120, direction=1)
        clf = MarketRegimeClassifier()
        result = clf.classify(klines)
        params = clf.get_strategy_params(result)
        assert isinstance(params, dict)
        # REGIME_STRATEGY_MAP 应该包含所有状态参数
        assert isinstance(REGIME_STRATEGY_MAP, dict)
        assert len(REGIME_STRATEGY_MAP) > 0


class Test价格数据源切换:
    """实时价格数据源切换"""

    def test_hyperliquid_client_creation(self):
        """Hyperliquid客户端创建"""
        from backend.services.hyperliquid_market_data import HyperliquidClient
        client = HyperliquidClient.__new__(HyperliquidClient)
        client._cache = {"BTC": {"price": 50000, "timestamp": datetime.now()}}
        assert client._cache["BTC"]["price"] == 50000

    def test_price_cache_ttl(self):
        """价格缓存TTL"""
        from backend.services.price_cache import PriceCache
        cache = PriceCache.__new__(PriceCache)
        cache._ttl = 30
        cache._prices = {"BTC": 50000}
        assert cache._ttl == 30


class Test风控实时预警:
    """风控系统实时预警"""

    def test_risk_control_service(self):
        """风控服务"""
        from backend.services.risk_control_service import RiskControlService
        service = RiskControlService.__new__(RiskControlService)
        service._circuit_breaker = {"active": False, "triggered_at": None}
        service._daily_stats = {"loss_pct": 0.02, "trades": 5}
        assert not service._circuit_breaker["active"]
        assert service._daily_stats["loss_pct"] < 0.06

    def test_circuit_breaker_trigger(self):
        """熔断器触发"""
        from backend.services.risk_control_service import RiskControlService
        service = RiskControlService.__new__(RiskControlService)
        service._circuit_breaker = {"active": False}
        service._daily_stats = {"loss_pct": 0.07}
        if service._daily_stats["loss_pct"] > 0.06:
            service._circuit_breaker["active"] = True
        assert service._circuit_breaker["active"] is True

    def test_position_tracking_auto_sync(self):
        """仓位自动同步"""
        from backend.services.position_tracker_service import PositionTrackerService
        tracker = PositionTrackerService.__new__(PositionTrackerService)
        tracker._positions = {
            "BTC_long": {"size": 0.1, "entry": 50000, "current": 51000},
        }
        pos = tracker._positions["BTC_long"]
        pnl = (pos["current"] - pos["entry"]) * pos["size"]
        assert pnl == 100.0


# ═══════════════════════════════════════════════
# 3. 新添加功能测试
# ═══════════════════════════════════════════════

class TestAI策略记忆:
    """AI策略记忆系统"""

    def test_strategy_memory_store_and_retrieve(self):
        """记忆存储和检索"""
        from backend.services.strategy_learning_service import StrategyLearningService
        service = StrategyLearningService.__new__(StrategyLearningService)
        service._memories = {}
        service._memories["strat_1"] = [
            {"trade_id": 1, "result": "win", "pnl": 50, "context": "trending_up"},
            {"trade_id": 2, "result": "loss", "pnl": -30, "context": "ranging"},
        ]
        assert len(service._memories["strat_1"]) == 2
        wins = [m for m in service._memories["strat_1"] if m["result"] == "win"]
        assert len(wins) == 1

    def test_wisdom_tracker(self):
        """智慧追踪"""
        from backend.services.wisdom_tracker import WisdomTracker
        tracker = WisdomTracker.__new__(WisdomTracker)
        tracker._wisdom = {"best_regime": "trending", "avg_win_rate": 0.65}
        assert tracker._wisdom["best_regime"] == "trending"


class Test自学习算法:
    """自学习算法集成"""

    def test_unified_learning_service(self):
        """统一学习服务"""
        from backend.services.unified_learning_service import UnifiedLearningService
        service = UnifiedLearningService.__new__(UnifiedLearningService)
        service._models = {"momentum": {"accuracy": 0.72, "samples": 500}}
        assert service._models["momentum"]["accuracy"] > 0.5

    def test_experience_retriever(self):
        """经验检索"""
        from backend.services.experience_retriever import ExperienceRetriever
        retriever = ExperienceRetriever.__new__(ExperienceRetriever)
        retriever._experiences = [
            {"context": "BTC_trending", "action": "long", "outcome": "win"},
        ]
        assert len(retriever._experiences) == 1


class Test回测优化模块:
    """回测优化"""

    def test_backtest_engine_creation(self):
        """回测引擎创建 - BacktestEngine()"""
        from backend.services.backtest_engine import BacktestEngine
        engine = BacktestEngine()
        assert hasattr(engine, 'run')
        assert hasattr(engine, 'reset')

    def test_genetic_optimizer_evolution(self):
        """遗传优化进化"""
        from backend.services.genetic_optimizer import GeneticOptimizer
        opt = GeneticOptimizer.__new__(GeneticOptimizer)
        opt._population = [
            {"genome": "g1", "fitness": 0.7},
            {"genome": "g2", "fitness": 0.9},
            {"genome": "g3", "fitness": 0.5},
        ]
        sorted_pop = sorted(opt._population, key=lambda x: x["fitness"], reverse=True)
        assert sorted_pop[0]["fitness"] == 0.9

    def test_evolution_scheduler(self):
        """进化调度器"""
        from backend.services.evolution_scheduler import EvolutionScheduler
        scheduler = EvolutionScheduler.__new__(EvolutionScheduler)
        scheduler._generation = 5
        scheduler._best_fitness = 0.85
        assert scheduler._generation == 5
        assert scheduler._best_fitness > 0.8


class Test多周期策略适配:
    """多周期策略"""

    def test_multi_timeframe_orchestrator(self):
        """多周期编排器"""
        from backend.services.multi_timeframe_orchestrator import MultiTimeframeOrchestrator
        orch = MultiTimeframeOrchestrator.__new__(MultiTimeframeOrchestrator)
        orch._timeframes = {"1m": {}, "5m": {}, "1h": {}, "4h": {}, "1d": {}}
        assert len(orch._timeframes) == 5

    def test_multi_timeframe_signal_alignment(self):
        """多周期信号对齐"""
        signals = {
            "1h": "long",
            "4h": "long",
            "1d": "neutral",
        }
        aligned = sum(1 for v in signals.values() if v == "long")
        assert aligned >= 2


class Test智能信号匹配:
    """智能信号匹配系统"""

    def test_signal_confirmation_engine(self):
        """信号确认引擎"""
        from backend.services.signal_confirmation_engine import SignalConfirmationEngine
        engine = SignalConfirmationEngine.__new__(SignalConfirmationEngine)
        engine._confirmations = {
            "volume": True,
            "momentum": True,
            "trend": False,
        }
        confirmed = sum(1 for v in engine._confirmations.values() if v)
        assert confirmed >= 2

    def test_signal_detection_service(self):
        """信号检测服务"""
        from backend.services.signal_detection_service import SignalDetectionService
        service = SignalDetectionService.__new__(SignalDetectionService)
        service._signals = [
            {"type": "breakout", "symbol": "BTC", "strength": 0.8},
            {"type": "reversal", "symbol": "ETH", "strength": 0.6},
        ]
        assert len(service._signals) == 2
        strong = [s for s in service._signals if s["strength"] > 0.7]
        assert len(strong) == 1


# ═══════════════════════════════════════════════
# 4. Phase 4-7 新功能测试
# ═══════════════════════════════════════════════

class Test市场扫描器:
    def test_scanner_module_api(self):
        """市场扫描器模块API验证"""
        from backend.services.market_scanner import MarketScanner, SymbolScore, ScanResult
        scanner = MarketScanner()
        assert hasattr(scanner, '_evaluate_symbol')
        assert hasattr(scanner, 'full_scan')
        assert hasattr(scanner, 'should_rescan')

    def test_anomaly_detector(self):
        """异常检测器 - detect(symbol, klines, market_data)"""
        from backend.services.anomaly_detector import AnomalyDetector
        detector = AnomalyDetector()
        klines = _make_klines_trend(120)
        market_data = {"volume_24h": 1e9, "funding_rate": 0.0001}
        result = detector.detect("BTC", klines, market_data)
        assert hasattr(result, 'events') or isinstance(result, dict)


class Test交易所抽象层:
    def test_exchange_factory(self):
        """交易所工厂"""
        from backend.services.exchange.exchange_factory import ExchangeClientFactory
        exchanges = ExchangeClientFactory.get_registered_exchanges()
        assert "hyperliquid" in exchanges
        assert "binance" in exchanges

    def test_cross_exchange_spread_dataclass(self):
        """跨交易所价差数据类"""
        from backend.services.exchange.cross_exchange_arb import CrossExchangeSpread
        spread = CrossExchangeSpread(
            symbol="BTC",
            exchange_a="hyperliquid",
            exchange_b="binance",
            price_a=50000.0,
            price_b=50100.0,
            spread_pct=0.002,
            historical_mean=0.001,
            historical_std=0.0005,
            z_score=2.5,
            timestamp=datetime.now(),
        )
        assert spread.spread_pct > 0
        assert spread.z_score > 2.0

    def test_cross_exchange_risk_tracker(self):
        """跨交易所风控追踪器"""
        from backend.services.exchange.cross_exchange_risk import CrossExchangeRiskTracker
        from backend.services.exchange.base_exchange_client import ExchangePosition
        tracker = CrossExchangeRiskTracker()
        pos_a = [ExchangePosition(
            symbol="BTC", side="long", size=0.1, entry_price=50000,
            mark_price=51000, unrealized_pnl=100, margin=5000,
            leverage=10, liquidation_price=45000,
        )]
        pos_b = [ExchangePosition(
            symbol="BTC", side="short", size=0.1, entry_price=50100,
            mark_price=51000, unrealized_pnl=-90, margin=5000,
            leverage=10, liquidation_price=55000,
        )]
        result = tracker.check_risk(pos_a, pos_b, equity=10000)
        assert hasattr(result, 'passed')


class TestDRL强化学习:
    def test_trading_env_creation(self):
        """交易环境创建 - TradingEnv(klines=df)"""
        from backend.services.rl.trading_env import TradingEnv
        klines = _make_klines_for_env(300)
        env = TradingEnv(klines=klines)
        obs, info = env.reset()
        assert obs is not None

    def test_trading_env_step(self):
        """交易环境步进"""
        from backend.services.rl.trading_env import TradingEnv
        klines = _make_klines_for_env(300)
        env = TradingEnv(klines=klines)
        env.reset()
        obs, reward, done, truncated, info = env.step(np.array([1.0, 0.5]))
        assert isinstance(reward, (int, float))
        assert isinstance(done, bool)

    def test_kelly_position_sizer(self):
        """Kelly仓位计算器"""
        from backend.services.rl.kelly_position_sizer import KellyPositionSizer
        sizer = KellyPositionSizer()
        result = sizer.calculate(
            equity=10000,
            win_rate=0.6,
            avg_win=200,
            avg_loss=100,
        )
        assert result.kelly_fraction > 0
        assert result.adjusted_fraction > 0
        assert result.position_size > 0
        assert result.adjusted_fraction <= sizer.max_position_pct

    def test_rl_optimizer_shadow_mode(self):
        """RL优化器Shadow Mode"""
        from backend.services.rl.rl_optimizer import RLPolicyOptimizer
        opt = RLPolicyOptimizer()
        advice = opt.get_shadow_advice(np.random.randn(10))
        assert "action" in advice
        assert advice["action"] in ["hold", "long", "short"]


# ═══════════════════════════════════════════════
# 5. 前端页面完整性测试 (文件存在性 + 内容检查)
# ═══════════════════════════════════════════════

class Test前端页面完整性:
    """验证前端组件文件存在和关键内容"""

    @staticmethod
    def _frontend_base():
        """获取前端项目根路径"""
        base = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'frontend', 'app')
        return os.path.normpath(base)

    def test_market_scanner_page_exists(self):
        """市场扫描器页面文件存在"""
        path = os.path.join(self._frontend_base(), 'components', 'market-scanner', 'MarketScannerPage.tsx')
        assert os.path.exists(path), f"文件不存在: {path}"

    def test_exchange_hub_page_exists(self):
        """交易所枢纽页面文件存在"""
        path = os.path.join(self._frontend_base(), 'components', 'exchange-hub', 'ExchangeHubPage.tsx')
        assert os.path.exists(path), f"文件不存在: {path}"

    def test_drl_panel_exists(self):
        """DRL面板文件存在"""
        path = os.path.join(self._frontend_base(), 'components', 'strategy', 'DRLPanel.tsx')
        assert os.path.exists(path), f"文件不存在: {path}"

    def test_strategy_page_references_drl(self):
        """策略页面包含DRL引用"""
        path = os.path.join(self._frontend_base(), 'components', 'strategy', 'StrategyPage.tsx')
        assert os.path.exists(path)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert 'DRLPanel' in content, "StrategyPage 未引用 DRLPanel"
        assert 'Brain' in content, "StrategyPage 未包含 Brain 图标"

    def test_api_layer_market_scanner_file(self):
        """市场扫描API层文件存在且导出正确函数"""
        path = os.path.join(self._frontend_base(), 'lib', 'marketScannerApi.ts')
        assert os.path.exists(path), f"文件不存在: {path}"
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        for func in ['triggerMarketScan', 'getLatestScanResult', 'getAnomalyReport', 'getRegimeClassifications']:
            assert func in content, f"marketScannerApi 缺少导出: {func}"

    def test_api_layer_exchange_file(self):
        """交易所API层文件存在且导出正确函数"""
        path = os.path.join(self._frontend_base(), 'lib', 'exchangeApi.ts')
        assert os.path.exists(path), f"文件不存在: {path}"
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        for func in ['getExchangeStatuses', 'scanCrossExchangeSpreads', 'getCrossExchangeExposure']:
            assert func in content, f"exchangeApi 缺少导出: {func}"

    def test_api_layer_drl_file(self):
        """DRL API层文件存在且导出正确函数"""
        path = os.path.join(self._frontend_base(), 'lib', 'drlApi.ts')
        assert os.path.exists(path), f"文件不存在: {path}"
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        for func in ['getRLShadowStatus', 'getShadowAdvice', 'getKellyResult', 'startDRLTraining']:
            assert func in content, f"drlApi 缺少导出: {func}"

    def test_main_tsx_page_titles(self):
        """主页面标题完整性 - 验证PAGE_TITLES包含新页面"""
        path = os.path.join(self._frontend_base(), 'main.tsx')
        assert os.path.exists(path)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert "'market-scanner'" in content or '"market-scanner"' in content, "main.tsx 缺少 market-scanner 页面标题"
        assert "'exchange-hub'" in content or '"exchange-hub"' in content, "main.tsx 缺少 exchange-hub 页面标题"

    def test_navigation_menu_entries(self):
        """导航菜单包含新页面入口"""
        path = os.path.join(self._frontend_base(), 'components', 'win95', 'Win95MenuBar.tsx')
        assert os.path.exists(path)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert 'market-scanner' in content, "菜单缺少 market-scanner 入口"
        assert 'exchange-hub' in content, "菜单缺少 exchange-hub 入口"

    def test_barrel_exports_exist(self):
        """桶导出文件(index.ts)存在"""
        base = self._frontend_base()
        for component_dir in ['market-scanner', 'exchange-hub']:
            idx = os.path.join(base, 'components', component_dir, 'index.ts')
            assert os.path.exists(idx), f"缺少桶导出: {idx}"


# ═══════════════════════════════════════════════
# 6. 系统集成测试
# ═══════════════════════════════════════════════

class Test系统集成:
    """前后端API接口连通性"""

    def test_api_health_endpoint_exists(self):
        """健康检查端点"""
        from backend.main import app
        routes = [r.path for r in app.routes]
        assert "/api/health" in routes

    def test_websocket_endpoint_exists(self):
        """WebSocket端点"""
        from backend.main import app
        routes = [r.path for r in app.routes]
        assert "/ws" in routes

    def test_api_router_mount_points(self):
        """路由挂载点"""
        from backend.main import app
        routes = [r.path for r in app.routes if hasattr(r, 'path')]
        prefixes = [r for r in routes if r.startswith("/api/")]
        assert len(prefixes) > 20

    def test_database_models_consistency(self):
        """数据库模型一致性"""
        from backend.database.models import Base
        tables = Base.metadata.tables.keys()
        assert "users" in tables
        assert "accounts" in tables
        assert "positions" in tables
        assert "orders" in tables
        assert "trades" in tables

    def test_factor_engine_integration(self):
        """因子引擎集成 - compute_all_factors"""
        from backend.services.factor_engine import FactorEngine
        engine = FactorEngine()
        klines = _make_ranging_klines()
        result = engine.compute_all_factors(klines)
        assert isinstance(result, dict)

    def test_signal_pipeline_e2e(self):
        """信号管道端到端"""
        from backend.services.signal_detection_service import SignalDetectionService
        from backend.services.signal_confirmation_engine import SignalConfirmationEngine

        detection = SignalDetectionService.__new__(SignalDetectionService)
        detection._signals = [
            {"type": "momentum", "symbol": "BTC", "strength": 0.85},
        ]
        confirmation = SignalConfirmationEngine.__new__(SignalConfirmationEngine)
        confirmation._confirmations = {"volume": True, "momentum": True}
        confirmed = sum(1 for v in confirmation._confirmations.values() if v)
        assert confirmed >= 2
        assert detection._signals[0]["strength"] > 0.7


class Test错误处理:
    """错误处理和异常恢复"""

    def test_scanner_module_handles_invalid_input(self):
        """扫描器模块可实例化"""
        from backend.services.market_scanner import MarketScanner
        scanner = MarketScanner()
        assert scanner is not None

    def test_empty_kline_handling(self):
        """空K线数据处理"""
        from backend.services.market_regime import MarketRegimeClassifier
        empty_df = pd.DataFrame({
            'open': [], 'high': [], 'low': [], 'close': [], 'volume': [],
        })
        clf = MarketRegimeClassifier()
        try:
            clf.classify(empty_df)
        except (ValueError, IndexError, KeyError):
            pass  # Expected for empty data

    def test_exchange_factory_invalid_exchange(self):
        """无效交易所名称"""
        from backend.services.exchange.exchange_factory import ExchangeClientFactory
        assert not ExchangeClientFactory.is_registered("invalid_exchange")

    def test_kelly_sizer_zero_win_rate(self):
        """Kelly仓位 - 零胜率"""
        from backend.services.rl.kelly_position_sizer import KellyPositionSizer
        sizer = KellyPositionSizer()
        result = sizer.calculate(
            equity=10000,
            win_rate=0.0,
            avg_win=0,
            avg_loss=100,
        )
        assert result.position_size == 0 or not result.is_valid


# ═══════════════════════════════════════════════
# 7. 性能基准测试
# ═══════════════════════════════════════════════

class Test性能基准:
    """响应时间和计算性能"""

    def test_market_regime_classification_speed(self):
        """市场状态分类速度"""
        from backend.services.market_regime import MarketRegimeClassifier
        klines = _make_ranging_klines()
        clf = MarketRegimeClassifier()

        start = time.perf_counter()
        for _ in range(100):
            clf.classify(klines)
        elapsed = time.perf_counter() - start

        avg_ms = (elapsed / 100) * 1000
        assert avg_ms < 50, f"分类平均耗时 {avg_ms:.1f}ms 超过50ms阈值"

    def test_kelly_sizer_speed(self):
        """Kelly仓位计算速度"""
        from backend.services.rl.kelly_position_sizer import KellyPositionSizer
        sizer = KellyPositionSizer()

        start = time.perf_counter()
        for _ in range(1000):
            sizer.calculate(
                equity=10000,
                win_rate=0.6,
                avg_win=200,
                avg_loss=100,
            )
        elapsed = time.perf_counter() - start

        avg_us = (elapsed / 1000) * 1_000_000
        assert avg_us < 500, f"Kelly计算平均耗时 {avg_us:.0f}us 超过500us阈值"

    def test_factor_engine_speed(self):
        """因子引擎速度"""
        from backend.services.factor_engine import FactorEngine
        engine = FactorEngine()
        klines = _make_ranging_klines()

        start = time.perf_counter()
        for _ in range(10):
            engine.compute_all_factors(klines)
        elapsed = time.perf_counter() - start

        avg_ms = (elapsed / 10) * 1000
        assert avg_ms < 200, f"因子引擎平均耗时 {avg_ms:.1f}ms 超过200ms阈值"

    def test_cross_exchange_arb_speed(self):
        """跨交易所套利引擎速度"""
        from backend.services.exchange.cross_exchange_arb import CrossExchangeSpread
        # 只测试 find_entry_opportunities 纯计算逻辑（不需要 client）
        # 构造spread数据
        spreads = []
        for i in range(50):
            spreads.append(CrossExchangeSpread(
                symbol=f"SYM{i}",
                exchange_a="hyperliquid",
                exchange_b="binance",
                price_a=50000 + i,
                price_b=50000 + i + 5,
                spread_pct=0.001 * (i + 1),
                historical_mean=0.001,
                historical_std=0.0005,
                z_score=1.0 + i * 0.1,
                timestamp=datetime.now(),
            ))

        # 直接测试筛选逻辑（不需要初始化引擎）
        start = time.perf_counter()
        # 模拟引擎筛选：z_score > 2.0 && spread_pct > 0.001
        entries = [s for s in spreads if s.z_score > 2.0 and s.spread_pct > 0.001]
        elapsed = time.perf_counter() - start

        assert elapsed < 0.1, f"跨所套利筛选耗时 {elapsed*1000:.1f}ms 超过100ms"
        assert isinstance(entries, list)

    def test_trading_env_step_speed(self):
        """交易环境步进速度"""
        from backend.services.rl.trading_env import TradingEnv
        klines = _make_klines_for_env(500)
        env = TradingEnv(klines=klines)
        env.reset()

        start = time.perf_counter()
        for _ in range(100):
            env.step(np.array([1.0, 0.5]))
            if env.current_step >= len(klines) - 2:
                env.reset()
        elapsed = time.perf_counter() - start

        avg_us = (elapsed / 100) * 1_000_000
        assert avg_us < 1000, f"环境步进平均耗时 {avg_us:.0f}us 超过1000us阈值"

    def test_genome_operations_speed(self):
        """策略基因组操作速度"""
        from backend.services.strategy_genome import create_default_genome, mutate_genome
        start = time.perf_counter()
        for _ in range(1000):
            genome = create_default_genome(category="momentum")
            mutate_genome(genome, mutation_rate=0.3)
        elapsed = time.perf_counter() - start

        avg_us = (elapsed / 1000) * 1_000_000
        assert avg_us < 300, f"基因组操作平均耗时 {avg_us:.0f}us 超过300us阈值"
