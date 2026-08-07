"""Alpha158 因子库 — 30个核心因子"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class A158_Open(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_open",name="Open",display_name="开盘价",category="technical",subcategory="kline",author="Alpha158")
    def calculate(self, data): return data['open']
class A158_High(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_high",name="High",display_name="最高价",category="technical",subcategory="kline",author="Alpha158")
    def calculate(self, data): return data['high']
class A158_Low(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_low",name="Low",display_name="最低价",category="technical",subcategory="kline",author="Alpha158")
    def calculate(self, data): return data['low']
class A158_Close(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_close",name="Close",display_name="收盘价",category="technical",subcategory="kline",author="Alpha158")
    def calculate(self, data): return data['close']
class A158_VWAP(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_vwap",name="VWAP",display_name="成交量加权均价",category="technical",subcategory="kline",author="Alpha158")
    def calculate(self, data): return (data['close']*data['volume']).cumsum()/(data['volume'].cumsum()+1e-9)
class A158_Ret1(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_ret1",name="Return1",display_name="1周期收益",category="technical",subcategory="price",author="Alpha158")
    def calculate(self, data): return data['close'].pct_change(1)
class A158_Ret5(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_ret5",name="Return5",display_name="5周期收益",category="technical",subcategory="price",author="Alpha158")
    def calculate(self, data): return data['close'].pct_change(5)
class A158_Ret20(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_ret20",name="Return20",display_name="20周期收益",category="technical",subcategory="price",author="Alpha158")
    def calculate(self, data): return data['close'].pct_change(20)
class A158_Std5(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_std5",name="Std5",display_name="5周期标准差",category="technical",subcategory="price",author="Alpha158")
    def calculate(self, data): return data['close'].rolling(5).std()
class A158_Std20(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_std20",name="Std20",display_name="20周期标准差",category="technical",subcategory="price",author="Alpha158")
    def calculate(self, data): return data['close'].rolling(20).std()
class A158_Skew20(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_skew20",name="Skew20",display_name="20周期偏度",category="technical",subcategory="price",author="Alpha158")
    def calculate(self, data): return data['close'].rolling(20).skew()
class A158_HL_Ratio(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_hl_ratio",name="HighLowRatio",display_name="高低价比",category="technical",subcategory="price",author="Alpha158")
    def calculate(self, data): return (data['high']-data['low'])/(data['close']+1e-9)
class A158_ClosePos(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_close_pos",name="ClosePosition",display_name="收盘位置",category="technical",subcategory="price",author="Alpha158")
    def calculate(self, data): return (data['close']-data['low'])/(data['high']-data['low']+1e-9)
class A158_Volume(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_volume",name="Volume",display_name="成交量",category="technical",subcategory="volume",author="Alpha158")
    def calculate(self, data): return data['volume']
class A158_VolMA5(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_vol_ma5",name="VolMA5",display_name="5均量",category="technical",subcategory="volume",author="Alpha158")
    def calculate(self, data): return data['volume'].rolling(5).mean()
class A158_VolMA20(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_vol_ma20",name="VolMA20",display_name="20均量",category="technical",subcategory="volume",author="Alpha158")
    def calculate(self, data): return data['volume'].rolling(20).mean()
class A158_VolRatio(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_vol_ratio",name="VolRatio",display_name="量比5/20",category="technical",subcategory="volume",author="Alpha158")
    def calculate(self, data): return data['volume'].rolling(5).mean()/(data['volume'].rolling(20).mean()+1e-9)
class A158_Amount(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_amount",name="Amount",display_name="成交额",category="technical",subcategory="volume",author="Alpha158")
    def calculate(self, data): return data['close']*data['volume']
class A158_ROC5(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_roc5",name="ROC5",display_name="5周期ROC",category="technical",subcategory="momentum",author="Alpha158")
    def calculate(self, data): return data['close'].pct_change(5)
class A158_ROC20(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_roc20",name="ROC20",display_name="20周期ROC",category="technical",subcategory="momentum",author="Alpha158")
    def calculate(self, data): return data['close'].pct_change(20)
class A158_BB_Upper(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_bb_up",name="BBUpper",display_name="布林上轨",category="technical",subcategory="volatility",author="Alpha158")
    def calculate(self, data): ma=data['close'].rolling(20).mean(); std=data['close'].rolling(20).std(); return ma+2*std
class A158_BB_Lower(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_bb_low",name="BBLower",display_name="布林下轨",category="technical",subcategory="volatility",author="Alpha158")
    def calculate(self, data): ma=data['close'].rolling(20).mean(); std=data['close'].rolling(20).std(); return ma-2*std
class A158_BB_Width(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_bb_width",name="BBWidth",display_name="布林带宽",category="technical",subcategory="volatility",author="Alpha158")
    def calculate(self, data): ma=data['close'].rolling(20).mean(); std=data['close'].rolling(20).std(); return 4*std/ma
class A158_Corr10(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_corr10",name="Corr10",display_name="10周期价量相关",category="technical",subcategory="correlation",author="Alpha158")
    def calculate(self, data): return data['close'].rolling(10).corr(data['volume'])
class A158_MA5_Dev(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_ma5_dev",name="MA5Dev",display_name="5均偏离",category="technical",subcategory="trend",author="Alpha158")
    def calculate(self, data): return (data['close']-data['close'].rolling(5).mean())/data['close']
class A158_MA20_Dev(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_ma20_dev",name="MA20Dev",display_name="20均偏离",category="technical",subcategory="trend",author="Alpha158")
    def calculate(self, data): return (data['close']-data['close'].rolling(20).mean())/data['close']
class A158_MACD_DIF(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_macd_dif",name="MACD_DIF",display_name="MACD离差",category="technical",subcategory="trend",author="Alpha158")
    def calculate(self, data): ema12=data['close'].ewm(span=12).mean(); ema26=data['close'].ewm(span=26).mean(); return ema12-ema26
class A158_MACD_DEA(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_macd_dea",name="MACD_DEA",display_name="MACD信号线",category="technical",subcategory="trend",author="Alpha158")
    def calculate(self, data): dif=data['close'].ewm(span=12).mean()-data['close'].ewm(span=26).mean(); return dif.ewm(span=9).mean()
class A158_MACD_Hist(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_macd_hist",name="MACD_Hist",display_name="MACD柱",category="technical",subcategory="trend",author="Alpha158")
    def calculate(self, data): dif=data['close'].ewm(span=12).mean()-data['close'].ewm(span=26).mean(); dea=dif.ewm(span=9).mean(); return (dif-dea)*2
class A158_RSI_6(BaseFactor):
    def get_metadata(self): return FactorMetadata(factor_id="a158_rsi6",name="RSI6",display_name="6周期RSI",category="technical",subcategory="momentum",author="Alpha158")
    def calculate(self, data): delta=data['close'].diff(); gain=delta.clip(lower=0); loss=(-delta).clip(lower=0); avg_gain=gain.rolling(6).mean(); avg_loss=loss.rolling(6).mean(); rs=avg_gain/(avg_loss+1e-9); return 100-100/(1+rs)
