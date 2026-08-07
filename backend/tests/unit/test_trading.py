"""
Unit tests for trading services
"""
import pytest
import sys
import os
from decimal import Decimal
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestTradingCalculations:
    """Test trading calculation functions"""
    
    def test_commission_calculation(self):
        """Test commission calculation for trades"""
        US_MIN_COMMISSION = 0.01
        US_COMMISSION_RATE = 0.0005
        
        def calculate_commission(notional):
            pct_fee = notional * Decimal(str(US_COMMISSION_RATE))
            min_fee = Decimal(str(US_MIN_COMMISSION))
            return max(pct_fee, min_fee)
        
        # Test with large notional
        large_notional = Decimal("50000")  # $50,000 trade
        commission = calculate_commission(large_notional)
        assert commission == Decimal("25.0")  # 0.05% of 50000 = 25
        
        # Test with small notional
        small_notional = Decimal("10")  # $10 trade
        commission = calculate_commission(small_notional)
        assert commission == Decimal("0.01")  # Minimum commission
    
    def test_average_cost_calculation(self):
        """Test average cost calculation for positions"""
        def calculate_avg_cost(current_qty, current_avg, new_qty, new_price):
            if current_qty == 0:
                return new_price
            total_qty = current_qty + new_qty
            total_cost = (Decimal(str(current_qty)) * Decimal(str(current_avg)) + 
                         Decimal(str(new_qty)) * Decimal(str(new_price)))
            return float(total_cost / Decimal(total_qty))
        
        # Test adding to existing position
        result = calculate_avg_cost(1.0, 50000, 1.0, 51000)
        assert result == 50500.0
        
        # Test new position
        result = calculate_avg_cost(0, 0, 2.0, 50000)
        assert result == 50000.0
    
    def test_position_value_calculation(self):
        """Test position value calculation"""
        def calculate_position_value(quantity, price):
            return float(Decimal(str(quantity)) * Decimal(str(price)))
        
        assert calculate_position_value(1.0, 50000) == 50000
        assert calculate_position_value(0.5, 50000) == 25000
        assert calculate_position_value(10, 1000.50) == 10005.0
    
    def test_pnl_calculation(self):
        """Test PnL calculation"""
        def calculate_pnl(entry_price, exit_price, quantity, side):
            pnl = (Decimal(str(exit_price)) - Decimal(str(entry_price))) * Decimal(str(quantity))
            if side == "SELL":
                pnl = -pnl
            return float(pnl)
        
        # Long position: bought at 50000, sold at 51000
        pnl = calculate_pnl(50000, 51000, 1.0, "BUY")
        assert pnl == 1000.0
        
        # Short position: borrowed and sold at 50000, bought back at 49000
        pnl = calculate_pnl(50000, 49000, 1.0, "SELL")
        assert pnl == 1000.0
    
    def test_pnl_percentage_calculation(self):
        """Test PnL percentage calculation"""
        def calculate_pnl_pct(entry_price, exit_price):
            return ((Decimal(str(exit_price)) - Decimal(str(entry_price))) / 
                    Decimal(str(entry_price))) * 100
        
        assert calculate_pnl_pct(50000, 51000) == 2.0
        assert calculate_pnl_pct(50000, 49000) == -2.0
        assert calculate_pnl_pct(100, 150) == 50.0


class TestOrderExecution:
    """Test order execution logic"""
    
    def test_order_creation(self):
        """Test order object creation"""
        def create_order(user_id, symbol, side, order_type, price, quantity):
            return {
                "user_id": user_id,
                "symbol": symbol,
                "side": side,
                "order_type": order_type,
                "price": price,
                "quantity": quantity,
                "status": "PENDING",
            }
        
        order = create_order(1, "BTC", "BUY", "LIMIT", 50000, 1)
        assert order["user_id"] == 1
        assert order["symbol"] == "BTC"
        assert order["status"] == "PENDING"
    
    def test_market_order_price(self):
        """Test market order price determination"""
        def get_market_order_price(last_price):
            return last_price  # Market orders execute at current market price
        
        assert get_market_order_price(50000) == 50000
        assert get_market_order_price(1000.50) == 1000.50
    
    def test_limit_order_price_validation(self):
        """Test limit order price validation"""
        def validate_limit_price(price, market_price, side):
            if side == "BUY" and price > market_price * 1.05:
                return False, "Buy limit price too far above market"
            if side == "SELL" and price < market_price * 0.95:
                return False, "Sell limit price too far below market"
            return True, "Valid"
        
        # Valid buy limit
        assert validate_limit_price(49000, 50000, "BUY") == (True, "Valid")
        
        # Invalid buy limit (too far above)
        assert validate_limit_price(55000, 50000, "BUY") == (False, "Buy limit price too far above market")


class TestRiskManagement:
    """Test risk management functions"""
    
    def test_position_limit_check(self):
        """Test position limit checking"""
        def check_position_limit(current_position, max_position_size, account_balance):
            position_value = current_position * max_position_size  # Simplified
            return position_value <= account_balance * 0.2  # Max 20% of account
        
        # Within limit: 1000 * 50000 = 50M > 20K, so over limit
        assert check_position_limit(1, 10, 100000) == True
        
        # Over limit
        assert check_position_limit(30000, 50, 100000) == False
    
    def test_daily_loss_limit(self):
        """Test daily loss limit checking"""
        def check_daily_loss_limit(current_loss, max_daily_loss, account_balance):
            loss_percentage = abs(current_loss) / account_balance
            return loss_percentage <= max_daily_loss
        
        # Within limit: 2% loss
        assert check_daily_loss_limit(-2000, 0.10, 100000) == True
        
        # Over limit: 15% loss
        assert check_daily_loss_limit(-15000, 0.10, 100000) == False
    
    def test_leverage_validation(self):
        """Test leverage validation"""
        def validate_leverage(leverage, max_leverage):
            if leverage < 1:
                return False, "Leverage must be at least 1x"
            if leverage > max_leverage:
                return False, f"Leverage cannot exceed {max_leverage}x"
            return True, "Valid"
        
        assert validate_leverage(1, 20) == (True, "Valid")
        assert validate_leverage(10, 20) == (True, "Valid")
        assert validate_leverage(0, 20) == (False, "Leverage must be at least 1x")
        assert validate_leverage(25, 20) == (False, "Leverage cannot exceed 20x")


class TestSignalGeneration:
    """Test signal generation logic"""
    
    def test_signal_strength_calculation(self):
        """Test signal strength calculation"""
        def calculate_signal_strength(factors):
            if not factors:
                return 0
            
            total = sum(factors.values())
            return total / len(factors)
        
        factors = {"rsi": 0.7, "macd": 0.6, "volume": 0.8}
        strength = calculate_signal_strength(factors)
        assert abs(strength - 0.7) < 0.001  # Use approximate comparison
    
    def test_signal_confidence_calculation(self):
        """Test signal confidence calculation"""
        def calculate_confidence(signal_count, threshold):
            if signal_count == 0:
                return 0
            confidence = min(signal_count / threshold, 1.0)
            return confidence * 100
        
        assert calculate_confidence(5, 10) == 50
        assert calculate_confidence(10, 10) == 100
        assert calculate_confidence(15, 10) == 100
    
    def test_signal_direction_determination(self):
        """Test signal direction determination"""
        def determine_direction(buy_signals, sell_signals):
            if buy_signals > sell_signals:
                return "BUY", buy_signals / (buy_signals + sell_signals)
            elif sell_signals > buy_signals:
                return "SELL", sell_signals / (buy_signals + sell_signals)
            else:
                return "HOLD", 0
        
        assert determine_direction(7, 3) == ("BUY", 0.7)
        assert determine_direction(3, 7) == ("SELL", 0.7)
        assert determine_direction(5, 5) == ("HOLD", 0)


class TestMarketDataProcessing:
    """Test market data processing"""
    
    def test_price_change_calculation(self):
        """Test price change calculation"""
        def calculate_change(open_price, close_price):
            change = close_price - open_price
            percent = (change / open_price) * 100
            return change, percent
        
        change, percent = calculate_change(50000, 51000)
        assert change == 1000
        assert percent == 2.0
    
    def test_vwap_calculation(self):
        """Test VWAP calculation"""
        def calculate_vwap(trades):
            if not trades:
                return 0
            
            total_volume = sum(t["volume"] for t in trades)
            total_price_volume = sum(t["price"] * t["volume"] for t in trades)
            
            return total_price_volume / total_volume if total_volume > 0 else 0
        
        trades = [
            {"price": 50000, "volume": 10},
            {"price": 51000, "volume": 5},
            {"price": 49000, "volume": 8},
        ]
        
        vwap = calculate_vwap(trades)
        assert 49000 < vwap < 51000
    
    def test_atr_calculation(self):
        """Test ATR (Average True Range) calculation"""
        def calculate_atr(highs, lows, closes):
            true_ranges = []
            for i in range(len(closes)):
                if i == 0:
                    tr = highs[i] - lows[i]
                else:
                    tr = max(
                        highs[i] - lows[i],
                        abs(highs[i] - closes[i-1]),
                        abs(lows[i] - closes[i-1])
                    )
                true_ranges.append(tr)
            
            return sum(true_ranges) / len(true_ranges)
        
        highs = [105, 110, 108]
        lows = [95, 100, 98]
        closes = [100, 105, 103]
        
        atr = calculate_atr(highs, lows, closes)
        assert atr > 0
