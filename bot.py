import os
import pandas as pd
import time
from binance.client import Client
from binance.exceptions import BinanceAPIException

# =====================
# Variables de entorno
# =====================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT", 4))  # monto de prueba
SYMBOL = os.getenv("SYMBOL", "BTCUSDT")            # par WAL/USDT
USE_TESTNET = os.getenv("USE_TESTNET", "True") == "True"

# =====================
# Conexión a Binance
# =====================
client = Client(API_KEY, API_SECRET, testnet=USE_TESTNET)

# =====================
# Obtener datos históricos
# =====================
def get_klines(symbol, interval='1m', limit=100):
    try:
        klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base', 'taker_buy_quote', 'ignore'
        ])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        return df
    except BinanceAPIException as e:
        print(f"Error Binance API: {e}")
        return None
    except Exception as e:
        print(f"Error obteniendo datos: {e}")
        return None

# =====================
# Indicadores
# =====================
def calculate_indicators(df):
    atr_len = 14
    atr_mult = 1.0
    shortEMA_len = 21
    longEMA_len = 65
    rsi_len1 = 25
    rsi_len2 = 100

    # ATR
    df['hl'] = df['high'] - df['low']
    df['hc'] = abs(df['high'] - df['close'].shift())
    df['lc'] = abs(df['low'] - df['close'].shift())
    df['tr'] = df[['hl', 'hc', 'lc']].max(axis=1)
    df['atr'] = df['tr'].rolling(atr_len).mean()

    # ATR Bands
    df['upper_band'] = df['close'] + df['atr'] * atr_mult
    df['lower_band'] = df['close'] - df['atr'] * atr_mult

    # EMAs
    df['shortEMA'] = df['close'].ewm(span=shortEMA_len, adjust=False).mean()
    df['longEMA'] = df['close'].ewm(span=longEMA_len, adjust=False).mean()

    # RSI
    delta = df['close'].diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    roll_up1 = up.rolling(rsi_len1).mean()
    roll_down1 = down.rolling(rsi_len1).mean()
    df['rsi1'] = 100 - (100 / (1 + roll_up1 / roll_down1))
    roll_up2 = up.rolling(rsi_len2).mean()
    roll_down2 = down.rolling(rsi_len2).mean()
    df['rsi2'] = 100 - (100 / (1 + roll_up2 / roll_down2))

    # RSI Cross
    df['RSILong'] = df['rsi1'] > df['rsi2']
    df['RSIShort'] = df['rsi1'] < df['rsi2']

    # EMA Cross
    df['GoldenLong'] = df['shortEMA'] > df['longEMA']
    df['GoldenShort'] = df['shortEMA'] < df['longEMA']

    return df

# =====================
# Señales de trading
# =====================
def check_signals(df):
    latest = df.iloc[-1]
    side = None
    stopLoss = None
    takeProfit = None

    # Condiciones Long
    if latest['open'] < latest['lower_band'] and latest['RSILong']:
        side = 'BUY'
        stopLoss = latest['low'] - latest['atr'] * 2
        takeProfit = latest['high'] + latest['atr'] * 5

    # Condiciones Short
    elif latest['open'] > latest['upper_band'] and latest['RSIShort']:
        side = 'SELL'
        stopLoss = latest['high'] + latest['atr'] * 2
        takeProfit = latest['low'] - latest['atr'] * 5

    return side, stopLoss, takeProfit

# =====================
# Orden de prueba
# =====================
def test_order(side):
    try:
        order = client.create_test_order(
            symbol=SYMBOL,
            side=side,
            type='MARKET',
            quantity=TRADE_AMOUNT
        )
        print(f"Orden de prueba {side} ejecutada correctamente")
    except BinanceAPIException as e:
        print(f"Error Binance API: {e}")
    except Exception as e:
        print(f"Error ejecutando orden de prueba: {e}")

# =====================
# Loop principal
# =====================
if __name__ == "__main__":
    print("Bot iniciado y corriendo continuamente...")
    while True:
        df = get_klines(SYMBOL, interval='1m', limit=100)
        if df is not None:
            df = calculate_indicators(df)
            side, stopLoss, takeProfit = check_signals(df)
            if side:
                print(f"Señal detectada: {side}, SL: {stopLoss:.4f}, TP: {takeProfit:.4f}")
                test_order(side)
            else:
                print("No hay señales de trading en este minuto")
        else:
            print("Error obteniendo datos, reintentando en 60s")
        time.sleep(60)

