"""
ATAS V2 回测数据管理器

提供历史数据加载、清洗、预处理功能
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Union
import pandas as pd
import numpy as np
from pathlib import Path


class DataSource(Enum):
    """数据源类型"""
    CSV = "csv"
    DATABASE = "database"
    API = "api"
    BINANCE = "binance"
    HYPERLIQUID = "hyperliquid"


@dataclass
class DataConfig:
    """数据配置"""
    source: DataSource = DataSource.CSV
    symbols: List[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    timeframe: str = "1h"  # 时间框架
    data_dir: Optional[Path] = None
    
    # 数据库配置
    db_host: Optional[str] = None
    db_port: Optional[int] = None
    db_name: Optional[str] = None
    
    # API配置
    api_key: Optional[str] = None
    api_secret: Optional[str] = None


class BacktestDataManager:
    """回测数据管理器"""
    
    def __init__(self, config: Optional[DataConfig] = None):
        self.config = config or DataConfig()
        self._cache: Dict[str, pd.DataFrame] = {}
    
    def load_data(
        self,
        symbol: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> pd.DataFrame:
        """
        加载历史数据
        
        Args:
            symbol: 交易标的
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            pd.DataFrame: OHLCV数据
        """
        # 使用配置中的日期（如果未指定）
        start_date = start_date or self.config.start_date
        end_date = end_date or self.config.end_date
        
        # 检查缓存
        cache_key = f"{symbol}_{start_date}_{end_date}"
        if cache_key in self._cache:
            return self._cache[cache_key].copy()
        
        # 根据数据源加载
        if self.config.source == DataSource.CSV:
            data = self._load_from_csv(symbol, start_date, end_date)
        elif self.config.source == DataSource.DATABASE:
            data = self._load_from_database(symbol, start_date, end_date)
        elif self.config.source in [DataSource.BINANCE, DataSource.HYPERLIQUID]:
            data = self._load_from_exchange(symbol, start_date, end_date)
        else:
            raise ValueError(f"Unsupported data source: {self.config.source}")
        
        # 数据清洗和验证
        data = self._clean_data(data)
        data = self._validate_data(data)
        
        # 缓存数据
        self._cache[cache_key] = data.copy()
        
        return data
    
    def load_multiple(
        self,
        symbols: List[str],
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Dict[str, pd.DataFrame]:
        """
        加载多个标的的历史数据
        
        Args:
            symbols: 交易标的列表
            start_date: 开始日期
            end_date: 结束日期
            
        Returns:
            Dict[str, pd.DataFrame]: 标的->数据映射
        """
        return {
            symbol: self.load_data(symbol, start_date, end_date)
            for symbol in symbols
        }
    
    def _load_from_csv(
        self,
        symbol: str,
        start_date: Optional[datetime],
        end_date: Optional[datetime]
    ) -> pd.DataFrame:
        """从CSV文件加载数据"""
        if not self.config.data_dir:
            raise ValueError("data_dir must be specified for CSV source")
        
        # 构建文件路径
        file_path = self.config.data_dir / f"{symbol}_{self.config.timeframe}.csv"
        
        if not file_path.exists():
            raise FileNotFoundError(f"Data file not found: {file_path}")
        
        # 读取CSV
        data = pd.read_csv(file_path, parse_dates=['timestamp'])
        data.set_index('timestamp', inplace=True)
        
        # 过滤日期范围
        if start_date:
            data = data[data.index >= start_date]
        if end_date:
            data = data[data.index <= end_date]
        
        return data
    
    def _load_from_database(
        self,
        symbol: str,
        start_date: Optional[datetime],
        end_date: Optional[datetime]
    ) -> pd.DataFrame:
        """从数据库加载数据"""
        try:
            from sqlalchemy import create_engine
            
            # 构建数据库连接
            db_url = f"postgresql://{self.config.db_host}:{self.config.db_port}/{self.config.db_name}"
            _engine = create_engine(db_url)
            
            try:
                # 构建查询
                query = f"""
                    SELECT timestamp, open, high, low, close, volume
                    FROM kline_data
                    WHERE symbol = '{symbol}'
                    AND timeframe = '{self.config.timeframe}'
                """
                
                if start_date:
                    query += f" AND timestamp >= '{start_date}'"
                if end_date:
                    query += f" AND timestamp <= '{end_date}'"
                
                query += " ORDER BY timestamp"
                
                # 执行查询
                data = pd.read_sql(query, _engine, parse_dates=['timestamp'])
                data.set_index('timestamp', inplace=True)
                
                return data
            finally:
                _engine.dispose()
            
        except ImportError:
            raise ImportError("sqlalchemy is required for database source")
        except Exception as e:
            raise RuntimeError(f"Failed to load data from database: {e}")
    
    def _load_from_exchange(
        self,
        symbol: str,
        start_date: Optional[datetime],
        end_date: Optional[datetime]
    ) -> pd.DataFrame:
        """从交易所API加载数据"""
        # 这里需要集成实际的交易所API
        # 暂时返回模拟数据
        raise NotImplementedError("Exchange API integration pending")
    
    def _clean_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """清洗数据"""
        # 删除重复行
        data = data[~data.index.duplicated(keep='first')]
        
        # 处理缺失值
        if data.isnull().any().any():
            # 前向填充
            data = data.fillna(method='ffill')
            # 后向填充剩余的NaN
            data = data.fillna(method='bfill')
        
        # 确保数据按时间排序
        data = data.sort_index()
        
        # 删除异常值（价格为0或负数）
        for col in ['open', 'high', 'low', 'close']:
            if col in data.columns:
                data = data[data[col] > 0]
        
        # 修正高低价异常
        if all(col in data.columns for col in ['high', 'low', 'open', 'close']):
            # high应该是最高价
            data['high'] = data[['open', 'high', 'low', 'close']].max(axis=1)
            # low应该是最低价
            data['low'] = data[['open', 'high', 'low', 'close']].min(axis=1)
        
        return data
    
    def _validate_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """验证数据完整性"""
        # 检查必需列
        required_columns = ['open', 'high', 'low', 'close', 'volume']
        missing_columns = [col for col in required_columns if col not in data.columns]
        
        if missing_columns:
            raise ValueError(f"Missing required columns: {missing_columns}")
        
        # 检查数据量
        if len(data) < 2:
            raise ValueError("Insufficient data for backtesting (minimum 2 rows)")
        
        # 检查价格逻辑
        invalid_rows = data[
            (data['high'] < data['low']) |
            (data['high'] < data['open']) |
            (data['high'] < data['close']) |
            (data['low'] > data['open']) |
            (data['low'] > data['close'])
        ]
        
        if len(invalid_rows) > 0:
            print(f"Warning: Found {len(invalid_rows)} rows with invalid OHLC relationships")
        
        return data
    
    def resample(
        self,
        data: pd.DataFrame,
        timeframe: str
    ) -> pd.DataFrame:
        """
        重采样数据到不同时间框架
        
        Args:
            data: 原始数据
            timeframe: 目标时间框架 (如 '1h', '4h', '1d')
            
        Returns:
            pd.DataFrame: 重采样后的数据
        """
        resampled = pd.DataFrame()
        resampled['open'] = data['open'].resample(timeframe).first()
        resampled['high'] = data['high'].resample(timeframe).max()
        resampled['low'] = data['low'].resample(timeframe).min()
        resampled['close'] = data['close'].resample(timeframe).last()
        resampled['volume'] = data['volume'].resample(timeframe).sum()
        
        return resampled.dropna()
    
    def add_features(
        self,
        data: pd.DataFrame,
        features: List[str]
    ) -> pd.DataFrame:
        """
        添加技术指标特征
        
        Args:
            data: 原始数据
            features: 特征列表
            
        Returns:
            pd.DataFrame: 添加特征后的数据
        """
        data = data.copy()
        
        for feature in features:
            if feature == 'returns':
                data['returns'] = data['close'].pct_change()
            
            elif feature == 'log_returns':
                data['log_returns'] = np.log(data['close'] / data['close'].shift(1))
            
            elif feature.startswith('sma_'):
                window = int(feature.split('_')[1])
                data[feature] = data['close'].rolling(window=window).mean()
            
            elif feature.startswith('ema_'):
                window = int(feature.split('_')[1])
                data[feature] = data['close'].ewm(span=window, adjust=False).mean()
            
            elif feature.startswith('volatility_'):
                window = int(feature.split('_')[1])
                data[feature] = data['returns'].rolling(window=window).std()
            
            elif feature == 'volume_ma':
                data['volume_ma'] = data['volume'].rolling(window=20).mean()
            
            else:
                print(f"Warning: Unknown feature '{feature}'")
        
        return data
    
    def split_train_test(
        self,
        data: pd.DataFrame,
        train_ratio: float = 0.8
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        划分训练集和测试集
        
        Args:
            data: 完整数据
            train_ratio: 训练集比例
            
        Returns:
            tuple: (训练集, 测试集)
        """
        split_idx = int(len(data) * train_ratio)
        train_data = data.iloc[:split_idx]
        test_data = data.iloc[split_idx:]
        
        return train_data, test_data
    
    def clear_cache(self):
        """清空缓存"""
        self._cache.clear()


def load_historical_data(
    symbol: str,
    start_date: datetime,
    end_date: datetime,
    source: DataSource = DataSource.CSV,
    **kwargs
) -> pd.DataFrame:
    """
    便捷函数：加载历史数据
    
    Args:
        symbol: 交易标的
        start_date: 开始日期
        end_date: 结束日期
        source: 数据源
        **kwargs: 其他配置参数
        
    Returns:
        pd.DataFrame: OHLCV数据
    """
    config = DataConfig(
        source=source,
        start_date=start_date,
        end_date=end_date,
        **kwargs
    )
    
    manager = BacktestDataManager(config)
    return manager.load_data(symbol)
