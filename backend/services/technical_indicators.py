#!/usr/bin/env python3
"""
技术指标计算服务
使用pandas-ta库计算各种技术指标
"""

import pandas as pd
try:
    import pandas_ta as ta
except ImportError:
    # pandas_ta requires Python 3.12+; provide stub for older Python
    import logging as _log
    _log.getLogger(__name__).warning("pandas_ta not available (requires Python 3.12+); technical indicators disabled")
    ta = None
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


def calculate_indicators(kline_data: List[Dict[str, Any]], indicators: List[str]) -> Dict[str, Any]:
    """
    计算技术指标

    Args:
        kline_data: K线数据列表，包含timestamp, open, high, low, close, volume
        indicators: 需要计算的指标列表，如 ['EMA20', 'EMA50', 'MACD', 'RSI14']

    Returns:
        Dict: 计算结果，格式为 {'EMA20': [...], 'MACD': {...}, ...}
    """
    if ta is None:
        logger.warning("pandas_ta not available, returning empty indicators")
        return {}

    if isinstance(kline_data, pd.DataFrame):
        if kline_data.empty:
            return {}
        df = kline_data
    elif not kline_data:
        return {}
    else:
        df = pd.DataFrame(kline_data)

    try:

        # 确保数据类型正确
        df['open'] = pd.to_numeric(df['open'], errors='coerce')
        df['high'] = pd.to_numeric(df['high'], errors='coerce')
        df['low'] = pd.to_numeric(df['low'], errors='coerce')
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce')

        # 按时间排序
        df = df.sort_values('timestamp')

        results = {}

        for indicator in indicators:
            try:
                if indicator == 'EMA20':
                    results['EMA20'] = _calculate_ema(df, 20)
                elif indicator == 'EMA50':
                    results['EMA50'] = _calculate_ema(df, 50)
                elif indicator == 'EMA100':
                    results['EMA100'] = _calculate_ema(df, 100)
                elif indicator == 'MA5':
                    results['MA5'] = _calculate_sma(df, 5)
                elif indicator == 'MA10':
                    results['MA10'] = _calculate_sma(df, 10)
                elif indicator == 'MA20':
                    results['MA20'] = _calculate_sma(df, 20)
                elif indicator == 'MACD':
                    results['MACD'] = _calculate_macd(df)
                elif indicator == 'RSI14':
                    results['RSI14'] = _calculate_rsi(df, 14)
                elif indicator == 'RSI7':
                    results['RSI7'] = _calculate_rsi(df, 7)
                elif indicator == 'BOLL':
                    results['BOLL'] = _calculate_bollinger_bands(df)
                elif indicator == 'ATR14':
                    results['ATR14'] = _calculate_atr(df, 14)
                elif indicator == 'VWAP':
                    results['VWAP'] = _calculate_vwap(df)
                elif indicator == 'STOCH':
                    results['STOCH'] = _calculate_stochastic(df)
                elif indicator == 'OBV':
                    results['OBV'] = _calculate_obv(df)
                elif indicator == 'ICHIMOKU':
                    results['ICHIMOKU'] = _calculate_ichimoku(df)
                elif indicator == 'ADX':
                    results['ADX'] = _calculate_adx(df)
                elif indicator == 'KELTNER':
                    results['KELTNER'] = _calculate_keltner(df)
                elif indicator == 'WILLIAMS_R':
                    results['WILLIAMS_R'] = _calculate_williams_r(df)
                else:
                    logger.warning(f"Unknown indicator: {indicator}")

            except Exception as e:
                logger.error(f"Error calculating {indicator}: {e}")
                results[indicator] = None

        return results

    except Exception as e:
        logger.error(f"Error in calculate_indicators: {e}")
        return {}


def _calculate_ema(df: pd.DataFrame, period: int) -> List[float]:
    """计算指数移动平均线"""
    _nan = float('nan')
    if len(df) < period:
        # [2026-07-10] 数据不足返回 NaN 而非 0.0（EMA=0 是不可发生的真价），
        # 让下游 data_readiness_gate 与数值检查能识别"指标未算出"。
        return [_nan] * len(df)
    ema = ta.ema(df['close'], length=period)
    if ema is None:
        return [_nan] * len(df)
    return ema.fillna(_nan).tolist()


def _calculate_sma(df: pd.DataFrame, period: int) -> List[float]:
    """计算简单移动平均线"""
    _nan = float('nan')
    if len(df) < period:
        return [_nan] * len(df)
    sma = ta.sma(df['close'], length=period)
    if sma is None:
        return [_nan] * len(df)
    return sma.fillna(_nan).tolist()


def _calculate_macd(df: pd.DataFrame) -> Dict[str, List[float]]:
    """计算MACD指标"""
    _nan = float('nan')
    _n = len(df)
    _empty = {'macd': [_nan] * _n, 'signal': [_nan] * _n, 'histogram': [_nan] * _n}
    if _n < 26:
        # [2026-07-10] 数据不足返回 NaN 而非 0.0，避免被当成"真实 MACD=0"
        return _empty
    macd_data = ta.macd(df['close'])
    if macd_data is None or macd_data.empty:
        return _empty
    return {
        'macd': macd_data['MACD_12_26_9'].fillna(_nan).tolist(),
        'signal': macd_data['MACDs_12_26_9'].fillna(_nan).tolist(),
        'histogram': macd_data['MACDh_12_26_9'].fillna(_nan).tolist()
    }


def _calculate_rsi(df: pd.DataFrame, period: int) -> List[float]:
    """计算相对强弱指数"""
    _nan = float('nan')
    if len(df) < period:
        # [2026-07-10] 数据不足返回 NaN 而非 50.0（RSI=50 是中性占位，无法区分真假），
        # 让 data_readiness_gate.indicators_are_real 识别为"未算出"。
        return [_nan] * len(df)
    rsi = ta.rsi(df['close'], length=period)
    if rsi is None:
        return [_nan] * len(df)
    return rsi.fillna(_nan).tolist()


def _calculate_bollinger_bands(df: pd.DataFrame, period: int = 20, std: float = 2) -> Dict[str, List[float]]:
    """计算布林带"""
    try:
        if len(df) < period:
            # 前端有时只请求 5 根 K线做迷你图，此时 BOLL 不足 20 根是正常情况。
            close = df["close"].fillna(0).tolist()
            return {"upper": close, "middle": close, "lower": close}

        bb = ta.bbands(df['close'], length=period, std=std)

        if bb is None:
            close = df["close"].fillna(0).tolist()
            return {"upper": close, "middle": close, "lower": close}

        if bb.empty:
            close = df["close"].fillna(0).tolist()
            return {"upper": close, "middle": close, "lower": close}

        # 尝试不同的列名格式
        upper_col = None
        middle_col = None
        lower_col = None

        for col in bb.columns:
            if 'BBU' in col or 'upper' in col.lower():
                upper_col = col
            elif 'BBM' in col or 'middle' in col.lower():
                middle_col = col
            elif 'BBL' in col or 'lower' in col.lower():
                lower_col = col

        if not all([upper_col, middle_col, lower_col]):
            logger.warning(
                "Could not find all BOLL columns. Found: upper=%s, middle=%s, lower=%s, columns=%s",
                upper_col,
                middle_col,
                lower_col,
                bb.columns.tolist(),
            )
            close = df["close"].fillna(0).tolist()
            return {"upper": close, "middle": close, "lower": close}

        result = {
            'upper': bb[upper_col].fillna(0).tolist(),
            'middle': bb[middle_col].fillna(0).tolist(),
            'lower': bb[lower_col].fillna(0).tolist()
        }

        return result

    except Exception as e:
        logger.error(f"Error calculating BOLL: {e}", exc_info=True)
        return None


def _calculate_atr(df: pd.DataFrame, period: int) -> List[float]:
    """计算平均真实波幅"""
    _nan = float('nan')
    if len(df) < period:
        # [2026-07-10] 数据不足返回 NaN 而非 0.0。ATR=0 极其危险：
        # 止损价 = 入场价 - ATR*N = 入场价，即开仓瞬间触发止损。
        return [_nan] * len(df)
    atr = ta.atr(df['high'], df['low'], df['close'], length=period)
    if atr is None:
        return [_nan] * len(df)
    return atr.fillna(_nan).tolist()


def _calculate_vwap(df: pd.DataFrame) -> List[float]:
    """计算成交量加权平均价"""
    try:
        # VWAP 需要 DatetimeIndex
        df_copy = df.copy()
        df_copy['datetime'] = pd.to_datetime(df_copy['timestamp'], unit='ms')
        df_copy = df_copy.set_index('datetime')
        vwap = ta.vwap(df_copy['high'], df_copy['low'], df_copy['close'], df_copy['volume'])
        return vwap.fillna(0).tolist()
    except Exception as e:
        logger.error(f"Error calculating VWAP: {e}")
        return None


def _calculate_stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> Dict[str, List[float]]:
    """计算随机震荡指标"""
    stoch = ta.stoch(df['high'], df['low'], df['close'], k=k_period, d=d_period)
    _nan = float('nan')
    return {
        # [2026-07-10] fillna(50) → fillna(NaN)，避免数据不足时伪装成中性
        'k': stoch[f'STOCHk_{k_period}_{d_period}_3'].fillna(_nan).tolist(),
        'd': stoch[f'STOCHd_{k_period}_{d_period}_3'].fillna(_nan).tolist()
    }


def _calculate_obv(df: pd.DataFrame) -> List[float]:
    """计算能量潮指标"""
    obv = ta.obv(df['close'], df['volume'])
    return obv.fillna(0).tolist()


def _calculate_ichimoku(df: pd.DataFrame) -> Dict[str, List[float]]:
    """计算一目均衡表"""
    _nan = float('nan')
    n = len(df)
    empty = {'tenkan': [_nan] * n, 'kijun': [_nan] * n,
             'senkou_a': [_nan] * n, 'senkou_b': [_nan] * n, 'chikou': [_nan] * n}
    if n < 52:
        return empty
    try:
        ichi = ta.ichimoku(df['high'], df['low'], df['close'])
        if ichi is None or ichi.empty:
            return empty
        return {
            'tenkan': ichi.get('ITS_9', pd.Series([_nan] * n)).fillna(_nan).tolist(),
            'kijun': ichi.get('IKS_26', pd.Series([_nan] * n)).fillna(_nan).tolist(),
            'senkou_a': ichi.get('ISA_9', pd.Series([_nan] * n)).fillna(_nan).tolist(),
            'senkou_b': ichi.get('ISB_26', pd.Series([_nan] * n)).fillna(_nan).tolist(),
            'chikou': ichi.get('ICS_26', pd.Series([_nan] * n)).fillna(_nan).tolist(),
        }
    except Exception as e:
        logger.error(f"Error calculating Ichimoku: {e}")
        return empty


def _calculate_adx(df: pd.DataFrame, period: int = 14) -> List[float]:
    """计算平均趋向指数"""
    _nan = float('nan')
    n = len(df)
    empty = [_nan] * n
    if n < period * 2:
        return empty
    try:
        adx = ta.adx(df['high'], df['low'], df['close'], length=period)
        if adx is None or adx.empty:
            return empty
        col = f'ADX_{period}'
        if col in adx.columns:
            return adx[col].fillna(_nan).tolist()
        return adx.iloc[:, 0].fillna(_nan).tolist()
    except Exception as e:
        logger.error(f"Error calculating ADX: {e}")
        return empty


def _calculate_keltner(df: pd.DataFrame, period: int = 20, multiplier: float = 2.0) -> Dict[str, List[float]]:
    """计算肯特纳通道"""
    _nan = float('nan')
    n = len(df)
    empty = {'upper': [_nan] * n, 'middle': [_nan] * n, 'lower': [_nan] * n}
    if n < period:
        return empty
    try:
        kc = ta.kc(df['high'], df['low'], df['close'], length=period, scalar=multiplier)
        if kc is None or kc.empty:
            return empty
        upper = kc.iloc[:, 0].fillna(_nan).tolist() if len(kc.columns) >= 1 else [_nan] * n
        middle = kc.iloc[:, 1].fillna(_nan).tolist() if len(kc.columns) >= 2 else [_nan] * n
        lower = kc.iloc[:, 2].fillna(_nan).tolist() if len(kc.columns) >= 3 else [_nan] * n
        return {'upper': upper, 'middle': middle, 'lower': lower}
    except Exception as e:
        logger.error(f"Error calculating Keltner: {e}")
        return empty


def _calculate_williams_r(df: pd.DataFrame, period: int = 14) -> List[float]:
    """计算威廉指标"""
    _nan = float('nan')
    n = len(df)
    empty = [_nan] * n  # [2026-07-10] 数据不足返回 NaN 而非 -50（中性占位）
    if n < period:
        return empty
    try:
        wr = ta.willr(df['high'], df['low'], df['close'], length=period)
        if wr is None:
            return empty
        return wr.fillna(_nan).tolist()
    except Exception as e:
        logger.error(f"Error calculating Williams %R: {e}")
        return empty


def get_available_indicators() -> List[Dict[str, str]]:
    """获取支持的技术指标列表"""
    return [
        {'name': 'MA5', 'description': '5期简单移动平均线'},
        {'name': 'MA10', 'description': '10期简单移动平均线'},
        {'name': 'MA20', 'description': '20期简单移动平均线'},
        {'name': 'EMA20', 'description': '20期指数移动平均线'},
        {'name': 'EMA50', 'description': '50期指数移动平均线'},
        {'name': 'EMA100', 'description': '100期指数移动平均线'},
        {'name': 'MACD', 'description': '移动平均收敛发散指标'},
        {'name': 'RSI14', 'description': '14期相对强弱指数'},
        {'name': 'RSI7', 'description': '7期相对强弱指数'},
        {'name': 'BOLL', 'description': '布林带'},
        {'name': 'ATR14', 'description': '14期平均真实波幅'},
        {'name': 'VWAP', 'description': '成交量加权平均价'},
        {'name': 'STOCH', 'description': '随机震荡指标'},
        {'name': 'OBV', 'description': '能量潮指标'},
        {'name': 'ICHIMOKU', 'description': '一目均衡表 (Ichimoku Cloud)'},
        {'name': 'ADX', 'description': '平均趋向指数 (ADX)'},
        {'name': 'KELTNER', 'description': '肯特纳通道 (Keltner Channel)'},
        {'name': 'WILLIAMS_R', 'description': '威廉指标 (Williams %R)'},
    ]
