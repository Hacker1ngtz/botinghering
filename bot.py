import os
import time
import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException

# -----------------------
# CONFIG desde Environment Variables
# -----------------------
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL")                  # ejemplo: ETHUSDT
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", 60))  # espera entre ciclos

# Indicadores
ATR_LEN = int(os.getenv("ATR_LEN", 14))
ATR_MULT = float(os.getenv("ATR_MULT", 1.0))
SHORT_EMA = int(os.getenv("SHORT_EMA", 21))
LONG_EMA = int(os.getenv("LONG_EMA", 65))
RSI_FAST = int(os.getenv("RSI_FAST", 25))
RSI_SLOW = int(os.getenv("RSI_SLOW", 100))

# -----------------------
# Cliente Binance Futures
# -----------------------
client = Client(API_KEY, API_SECRET)
# === CONFIGURACIÓN DE APALANCAMIENTO ===
from binance.exceptions import BinanceAPIException

SYMBOL = os.getenv("SYMBOL", "BNBUSDT")  # o tu símbolo desde variables de entorno
LEVERAGE = int(os.getenv("LEVERAGE", 10))  # puedes definirlo en Railway como LEVERAGE=10

try:
    # Cambiar apalancamiento del par
    response = client.futures_change_leverage(
        symbol=SYMBOL,
        leverage=LEVERAGE
    )
    print(f"✅ Apalancamiento establecido en {LEVERAGE}x para {SYMBOL}")
except BinanceAPIException as e:
    print(f"⚠️ Error al establecer apalancamiento: {e}")

# -----------------------
# Helpers: obtener velas (futuros) y formatear dataframe
# -----------------------
def get_futures_klines(symbol, interval='1m', limit=200):
    try:
        klines = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=[
            'open_time','open','high','low','close','volume','close_time',
            'quote_asset_volume','num_trades','taker_buy_base','taker_buy_quote','ignore'
        ])
        for c in ['open','high','low','close','volume']:
            df[c] = df[c].astype(float)
        return df
    except BinanceAPIException as e:
        print(f"Error Binance API al obtener klines: {e}")
        return None
    except Exception as e:
        print(f"Error (get_futures_klines): {e}")
        return None

# -----------------------
# Indicadores: ATR, EMA, RSI doble
# -----------------------
def calculate_indicators(df):
    # ATR
    df['hl'] = df['high'] - df['low']
    df['hc'] = (df['high'] - df['close'].shift(1)).abs()
    df['lc'] = (df['low'] - df['close'].shift(1)).abs()
    df['tr'] = df[['hl','hc','lc']].max(axis=1)
    df['atr'] = df['tr'].rolling(ATR_LEN).mean()

    # ATR bands
    df['upper_band'] = df['close'] + df['atr'] * ATR_MULT
    df['lower_band'] = df['close'] - df['atr'] * ATR_MULT

    # EMAs
    df['ema_short'] = df['close'].ewm(span=SHORT_EMA, adjust=False).mean()
    df['ema_long'] = df['close'].ewm(span=LONG_EMA, adjust=False).mean()

    # RSI fast & slow
    delta = df['close'].diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)

    roll_up_fast = up.rolling(RSI_FAST).mean()
    roll_down_fast = down.rolling(RSI_FAST).mean()
    df['rsi_fast'] = 100 - (100 / (1 + roll_up_fast / roll_down_fast))

    roll_up_slow = up.rolling(RSI_SLOW).mean()
    roll_down_slow = down.rolling(RSI_SLOW).mean()
    df['rsi_slow'] = 100 - (100 / (1 + roll_up_slow / roll_down_slow))

    # Cross conditions
    df['rsi_long'] = df['rsi_fast'] > df['rsi_slow']
    df['rsi_short'] = df['rsi_fast'] < df['rsi_slow']

    df['golden_long'] = (df['ema_short'] > df['ema_long']) & (df['ema_short'].shift(1) <= df['ema_long'].shift(1))
    df['golden_short'] = (df['ema_short'] < df['ema_long']) & (df['ema_short'].shift(1) >= df['ema_long'].shift(1))

    return df

# -----------------------
# Señales (igual que tu Pine script)
# -----------------------
def check_signals(df):
    row = df.iloc[-1]
    side = None
    sl = None
    tp = None

    if row['open'] < row['lower_band'] and row['rsi_long']:
        side = 'BUY'
        sl = row['low'] - row['atr'] * 2
        tp = row['high'] + row['atr'] * 5
    elif row['open'] > row['upper_band'] and row['rsi_short']:
        side = 'SELL'
        sl = row['high'] + row['atr'] * 2
        tp = row['low'] - row['atr'] * 5

    return side, sl, tp

# -----------------------
# Ejecutar orden en Futuros — 1 contrato fijo
# -----------------------
def execute_futures_market(side):
    quantity = 1  # siempre 1 contrato
    try:
        if side == 'BUY':
            res = client.futures_create_order(symbol=SYMBOL, side='BUY', type='MARKET', quantity=quantity)
        else:
            res = client.futures_create_order(symbol=SYMBOL, side='SELL', type='MARKET', quantity=quantity)
        print(f"Orden ejecutada: {side} qty={quantity}  —  id: {res.get('orderId', 'no-id')}")
        return res
    except BinanceAPIException as e:
        print(f"Error Binance API al ejecutar orden: {e}")
        return None
    except Exception as e:
        print(f"Error al ejecutar orden: {e}")
        return None

# -----------------------
# MAIN LOOP
# -----------------------
if __name__ == "__main__":
    print("Bot iniciado — mercado REAL (Futures). Símbolo:", SYMBOL)
    while True:
        df = get_futures_klines(SYMBOL, interval='1m', limit=200)
        if df is None:
            print("No se obtuvieron velas. Reintentando en", SLEEP_SECONDS, "s")
            time.sleep(SLEEP_SECONDS)
            continue

        df = calculate_indicators(df)
        side, sl, tp = check_signals(df)

        if side:
            print(f"Señal detectada: {side}  SL={sl:.6f}  TP={tp:.6f}")
            execute_futures_market(side)
        else:
            print("No hay señales en este ciclo")

        time.sleep(SLEEP_SECONDS)


