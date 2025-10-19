import os
import time
import numpy as np
import pandas as pd
from binance.client import Client
from dotenv import load_dotenv

# -------------------------------
# Cargar variables de entorno
# -------------------------------
load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
USE_TESTNET = os.getenv("USE_TESTNET") == "True"
SYMBOL = os.getenv("SYMBOL")
TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT"))

# Conectar a Binance
client = Client(API_KEY, API_SECRET)
if USE_TESTNET:
    client.API_URL = 'https://testnet.binance.vision/api'

# -------------------------------
# Funciones de indicadores
# -------------------------------
def get_klines(symbol, interval="1m", limit=100):
    data = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(data, columns=[
        "OpenTime","Open","High","Low","Close","Volume",
        "CloseTime","QuoteAssetVolume","NumTrades",
        "TakerBuyBase","TakerBuyQuote","Ignore"
    ])
    for col in ["Open","High","Low","Close","Volume"]:
        df[col] = df[col].astype(float)
    return df

def EMA(series, length):
    return series.ewm(span=length, adjust=False).mean()

def RSI(series, length):
    delta = series.diff()
    gain = np.where(delta>0, delta, 0)
    loss = np.where(delta<0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(length).mean()
    avg_loss = pd.Series(loss).rolling(length).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def ATR(df, period):
    df['H-L'] = df['High'] - df['Low']
    df['H-C'] = abs(df['High'] - df['Close'].shift())
    df['L-C'] = abs(df['Low'] - df['Close'].shift())
    tr = df[['H-L','H-C','L-C']].max(axis=1)
    return tr.rolling(period).mean()

# -------------------------------
# Ejecutar orden
# -------------------------------
def place_order(side):
    try:
        if side == "BUY":
            order = client.order_market_buy(symbol=SYMBOL, quoteOrderQty=TRADE_AMOUNT)
        else:
            order = client.order_market_sell(symbol=SYMBOL, quoteOrderQty=TRADE_AMOUNT)
        print(f"✅ Orden ejecutada: {side} {SYMBOL}")
    except Exception as e:
        print("❌ Error al ejecutar orden:", e)

# -------------------------------
# Lógica principal (Scalp Hunt)
# -------------------------------
SHORT_EMA_LEN = 21
LONG_EMA_LEN = 65
RSI_LEN_FAST = 25
RSI_LEN_SLOW = 100
ATR_LEN = 14
ATR_MULT = 1

while True:
    df = get_klines(SYMBOL, limit=100)
    close = df['Close']
    
    # Indicadores
    short_ema = EMA(close, SHORT_EMA_LEN)
    long_ema = EMA(close, LONG_EMA_LEN)
    rsi_fast = RSI(close, RSI_LEN_FAST)
    rsi_slow = RSI(close, RSI_LEN_SLOW)
    atr = ATR(df, ATR_LEN)
    
    upper_band = close.iloc[-1] + atr.iloc[-1]*ATR_MULT
    lower_band = close.iloc[-1] - atr.iloc[-1]*ATR_MULT
    
    # Condiciones RSI
    rsi_long = rsi_fast.iloc[-1] > rsi_slow.iloc[-1]
    rsi_short = rsi_fast.iloc[-1] < rsi_slow.iloc[-1]
    
    # Condiciones de entrada
    long_cond = close.iloc[-1] < lower_band and rsi_long
    short_cond = close.iloc[-1] > upper_band and rsi_short
    
    # Confirmación EMA (Golden Cross)
    golden_long = short_ema.iloc[-1] > long_ema.iloc[-1] and short_ema.iloc[-2] < long_ema.iloc[-2]
    golden_short = short_ema.iloc[-1] < long_ema.iloc[-1] and short_ema.iloc[-2] > long_ema.iloc[-2]
    
    # Decisión final
    if long_cond and golden_long:
        place_order("BUY")
    elif short_cond and golden_short:
        place_order("SELL")
    else:
        print("⏸ No se cumplen condiciones. Precio actual:", close.iloc[-1])
    
    time.sleep(60)  # revisar cada minuto
