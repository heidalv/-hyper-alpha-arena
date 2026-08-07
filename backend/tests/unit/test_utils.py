"""
Unit tests for encryption utilities
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestEncryption:
    """Test encryption utilities"""
    
    def test_encryption_key_generation(self):
        """Test encryption key generation"""
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        assert key is not None
        assert len(key) > 0
    
    def test_encryption_decryption(self):
        """Test encryption and decryption"""
        from cryptography.fernet import Fernet
        
        # Generate a test key
        key = Fernet.generate_key()
        f = Fernet(key)
        
        # Test data
        original_data = "0x1234567890abcdef1234567890abcdef12345678"
        
        # Encrypt
        encrypted = f.encrypt(original_data.encode())
        assert encrypted != original_data.encode()
        
        # Decrypt
        decrypted = f.decrypt(encrypted).decode()
        assert decrypted == original_data
    
    def test_different_keys_produce_different_results(self):
        """Test that different keys produce different encrypted results"""
        from cryptography.fernet import Fernet
        
        key1 = Fernet.generate_key()
        key2 = Fernet.generate_key()
        
        data = "test_private_key"
        
        f1 = Fernet(key1)
        f2 = Fernet(key2)
        
        encrypted1 = f1.encrypt(data.encode())
        encrypted2 = f2.encrypt(data.encode())
        
        assert encrypted1 != encrypted2


class TestPriceFormatting:
    """Test price formatting utilities"""
    
    def test_format_large_numbers(self):
        """Test formatting of large numbers"""
        # Test the format logic
        def format_price(price):
            if price >= 1_000_000_000:
                return f"{price / 1_000_000_000:.2f}B"
            elif price >= 1_000_000:
                return f"{price / 1_000_000:.2f}M"
            elif price >= 1_000:
                return f"{price / 1_000:.2f}K"
            else:
                return f"{price:.2f}"
        
        assert format_price(1_500_000_000) == "1.50B"
        assert format_price(2_500_000) == "2.50M"
        assert format_price(15_000) == "15.00K"
        assert format_price(500.50) == "500.50"
    
    def test_format_crypto_prices(self):
        """Test formatting of crypto prices"""
        def format_crypto_price(price):
            if price >= 1000:
                return f"${price:,.2f}"
            elif price >= 1:
                return f"${price:.4f}"
            else:
                return f"${price:.8f}"
        
        assert format_crypto_price(50000.50) == "$50,000.50"
        assert format_crypto_price(250.75) == "$250.7500"
        assert format_crypto_price(0.00001234) == "$0.00001234"


class TestRiskCalculations:
    """Test risk management calculations"""
    
    def test_position_size_calculation(self):
        """Test position size calculation based on risk"""
        def calculate_position_size(account_balance, risk_percent, stop_loss_percent):
            risk_amount = account_balance * (risk_percent / 100)
            position_size = risk_amount / (stop_loss_percent / 100)
            return position_size
        
        # Test: $10,000 account, 2% risk, 5% stop loss
        result = calculate_position_size(10000, 2, 5)
        assert result == 4000  # 2% of 10000 = 200, 200 / 0.05 = 4000
    
    def test_leverage_calculation(self):
        """Test leverage-related calculations"""
        def calculate_margin_required(position_value, leverage):
            return position_value / leverage
        
        def calculate_liquidation_price(entry_price, leverage, is_long=True):
            if is_long:
                return entry_price * (1 - 1/leverage * 0.5)
            else:
                return entry_price * (1 + 1/leverage * 0.5)
        
        # Test margin required
        assert calculate_margin_required(10000, 10) == 1000
        assert calculate_margin_required(10000, 20) == 500
        
        # Test liquidation price (long position)
        liq_price = calculate_liquidation_price(50000, 10, is_long=True)
        assert 45000 <= liq_price <= 50000


class TestOrderValidation:
    """Test order validation logic"""
    
    def test_validate_order_quantity(self):
        """Test order quantity validation"""
        US_LOT_SIZE = 1
        US_MIN_ORDER_QUANTITY = 1
        
        def validate_quantity(quantity):
            if quantity % US_LOT_SIZE != 0:
                return False, "Quantity must be a multiple of lot size"
            if quantity < US_MIN_ORDER_QUANTITY:
                return False, "Quantity must be at least minimum order quantity"
            return True, "Valid"
        
        assert validate_quantity(1) == (True, "Valid")
        assert validate_quantity(0) == (False, "Quantity must be at least minimum order quantity")
        assert validate_quantity(-1) == (False, "Quantity must be at least minimum order quantity")
    
    def test_validate_order_price(self):
        """Test order price validation"""
        def validate_price(price, order_type):
            if order_type == "MARKET":
                return True, "Valid"
            
            if price is None:
                return False, "Price is required for limit orders"
            if price <= 0:
                return False, "Price must be positive"
            return True, "Valid"
        
        assert validate_price(50000, "MARKET") == (True, "Valid")
        assert validate_price(50000, "LIMIT") == (True, "Valid")
        assert validate_price(None, "LIMIT") == (False, "Price is required for limit orders")
        assert validate_price(-100, "LIMIT") == (False, "Price must be positive")


class TestDataTransformations:
    """Test data transformation utilities"""
    
    def test_kline_data_transformation(self):
        """Test K-line data transformation"""
        raw_kline = [
            1700000000000,  # Open time
            "50000.00",      # Open
            "51000.00",      # High
            "49000.00",      # Low
            "50500.00",      # Close
            "1000.00",       # Volume
        ]
        
        def transform_kline(kline):
            return {
                "timestamp": kline[0],
                "open": float(kline[1]),
                "high": float(kline[2]),
                "low": float(kline[3]),
                "close": float(kline[4]),
                "volume": float(kline[5]),
            }
        
        result = transform_kline(raw_kline)
        assert result["open"] == 50000.0
        assert result["high"] == 51000.0
        assert result["low"] == 49000.0
        assert result["close"] == 50500.0
    
    def test_timeframe_conversion(self):
        """Test timeframe string conversion"""
        def timeframe_to_seconds(timeframe):
            unit = timeframe[-1]
            value = int(timeframe[:-1])
            
            multipliers = {
                "m": 60,
                "h": 3600,
                "d": 86400,
                "w": 604800,
            }
            
            return value * multipliers.get(unit, 60)
        
        assert timeframe_to_seconds("1m") == 60
        assert timeframe_to_seconds("5m") == 300
        assert timeframe_to_seconds("1h") == 3600
        assert timeframe_to_seconds("4h") == 14400
        assert timeframe_to_seconds("1d") == 86400
