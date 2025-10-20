# bot.py
import os
import time
import math
import pandas as pd
from binance.client import Client
from binance.enums import *

# ==============================
# VARIABLES DE ENTORNO
# ==============================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "BNBUSDT").upper()  # Cambia aquí tu moneda
LEVERAGE = int(os.getenv("LEVERAGE", 10))
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", 60))

# Indicadores configurables
ATR_LEN = int(os.getenv("ATR_LEN", 14))
ATR_MULT = float(os.getenv("ATR_MULT", 1.0))
SHORT_EMA = int(os.getenv("SHORT_EMA", 21))
LONG_EMA = int(os.getenv("LONG_EMA", 65))
RSI_FAST = int(os.getenv("RSI_FAST", 25))
RSI_SLOW = int(os.getenv("RSI_SLOW", 100))

# ==============================
# CLIENTE BINANCE FUTURES
# ==============================
client = Client(API_KEY, API_SECRET)

# ==============================
# FUNCIONES AUXILIARES
# ==============================
def round_step(quantity, step):
    precision = max(0, int(round(-math.log10(step))))
    qty = math.floor(quantity / step) * step
    return round(qty, precision)

def round_price(price, tick):
    precision = max(0, int(round(-math.log10(tick))))
    return round(price, precision)

def get_symbol_rules(symbol):
    info = client.futures_exchange_info()
    s_info = next((s for s in info['symbols'] if s['symbol'] == symbol), None)
    step_size = float(next(f['stepSize'] for f in s_info['filters'] if f['filterType'] == 'LOT_SIZE'))
    tick_size = float(next(f['tickSize'] for f in s_info['filters'] if f['filterType'] == 'PRICE_FILTER'))
    min_notional = float(next(f.get('minNotional', 5.0) for f in s_info['filters'] if f['filterType'] == 'MIN_NOTIONAL'))
    min_qty = float(next(f['minQty'] for f in s_info['filters'] if f['filterType'] == 'LOT_SIZE'))
    return step_size, tick_size, min_notional, min_qty

def calculate_qty_full_balance(symbol, leverage):
    balances = client.futures_account_balance()
    usdt_balance = next((float(b['balance']) for b in balances if b['asset'] == 'USDT'), 0.0)
    if usdt_balance <= 0:
        print("⚠️ No hay USDT disponible.")
        return 0.0
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    # Dejamos un 5% de colchón para evitar margen insuficiente
    raw_qty = (usdt_balance * 0.95 * leverage) / price
    step_size, tick_size, min_notional, min_qty = get_symbol_rules(symbol)
    qty = max(round_step(raw_qty, step_size), min_qty)
    if qty * price < min_notional:
        print(f"⚠️ Qty ajustada no cumple notional mínimo ({min_notional} USDT)")
        return 0.0
    return qty

def get_futures_klines(symbol, interval='1m', limit=200):
    klines = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=[
        'open_time','open','high','low','close','volume','close_time',
        'quote_asset_volume','num_trades','taker_buy_base','taker_buy_quote','ignore'
    ])
    for c in ['open','high','low','close','volume']:
        df[c] = df[c].astype(float)
    return df

# ==============================
# CALCULAR INDICADORES Y TENDENCIA
# ==============================
def calculate_indicators(df):
    df = df.copy()
    # ATR
    df['hl'] = df['high'] - df['low']
    df['hc'] = (df['high'] - df['close'].shift(1)).abs()
    df['lc'] = (df['low'] - df['close'].shift(1)).abs()
    df['tr'] = df[['hl','hc','lc']].max(axis=1)
    df['atr'] = df['tr'].rolling(ATR_LEN).mean()
    df['upper_band'] = df['close'] + df['atr'] * ATR_MULT
    df['lower_band'] = df['close'] - df['atr'] * ATR_MULT
    # EMA
    df['ema_short'] = df['close'].ewm(span=SHORT_EMA, adjust=False).mean()
    df['ema_long'] = df['close'].ewm(span=LONG_EMA, adjust=False).mean()
    # RSI
    delta = df['close'].diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up_fast = up.rolling(RSI_FAST).mean()
    roll_down_fast = down.rolling(RSI_FAST).mean()
    df['rsi_fast'] = 100 - (100 / (1 + roll_up_fast / roll_down_fast))
    roll_up_slow = up.rolling(RSI_SLOW).mean()
    roll_down_slow = down.rolling(RSI_SLOW).mean()
    df['rsi_slow'] = 100 - (100 / (1 + roll_up_slow / roll_down_slow))
    # Tendencia
    df['trend'] = 'NEUTRAL'
    df.loc[(df['ema_short'] > df['ema_long']) & (df['rsi_fast'] > df['rsi_slow']), 'trend'] = 'LONG'
    df.loc[(df['ema_short'] < df['ema_long']) & (df['rsi_fast'] < df['rsi_slow']), 'trend'] = 'SHORT'
    return df

def check_signals(df):
    row = df.iloc[-1]
    side = None
    sl = None
    tp = None
    if row['trend'] == 'LONG':
        side = 'LONG'
        sl = row['low'] - row['atr'] * 2
        tp = row['high'] + row['atr'] * 5
    elif row['trend'] == 'SHORT':
        side = 'SHORT'
        sl = row['high'] + row['atr'] * 2
        tp = row['low'] - row['atr'] * 5
    return side, sl, tp

# ==============================
# POSICIÓN Y APERTURA
# ==============================
def get_current_position(symbol):
    pos = client.futures_position_information(symbol=symbol)
    for p in pos:
        if p['symbol'] == symbol:
            return float(p['positionAmt'])
    return 0.0

def close_opposite_if_needed(symbol, target_side):
    amt = get_current_position(symbol)
    if amt == 0:
        return True
    existing_side = 'LONG' if amt > 0 else 'SHORT'
    if existing_side == target_side:
        return True
    qty = abs(amt)
    side_for_close = 'SELL' if amt > 0 else 'BUY'
    client.futures_create_order(symbol=symbol, side=side_for_close, type='MARKET', quantity=qty, reduceOnly=True)
    time.sleep(1)
    return True

def open_position_with_tp_sl(symbol, side, sl_price, tp_price):
    ok = close_opposite_if_needed(symbol, side)
    if not ok:
        print("No se cerró posición opuesta, abortando.")
        return None
    qty = calculate_qty_full_balance(symbol, LEVERAGE)
    if qty <= 0:
        print("⚠️ Qty inválida, abortando.")
        return None
    order = client.futures_create_order(
        symbol=symbol,
        side=SIDE_BUY if side=='LONG' else SIDE_SELL,
        type=FUTURE_ORDER_TYPE_MARKET,
        quantity=qty
    )
    step_size, tick_size, _, _ = get_symbol_rules(symbol)
    slp = round_price(sl_price, tick_size)
    tpp = round_price(tp_price, tick_size)
    try:
        client.futures_create_order(symbol=symbol, side=SIDE_SELL if side=='LONG' else SIDE_BUY,
                                    type='STOP_MARKET', stopPrice=slp, reduceOnly=True, quantity=qty)
    except:
        print("No se pudo colocar SL")
    try:
        client.futures_create_order(symbol=symbol, side=SIDE_SELL if side=='LONG' else SIDE_BUY,
                                    type='TAKE_PROFIT_MARKET', stopPrice=tpp, reduceOnly=True, quantity=qty)
    except:
        print("No se pudo colocar TP")
    return order

# ==============================
# LOOP PRINCIPAL
# ==============================
if __name__ == "__main__":
    client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
    print(f"🚀 Bot iniciado para {SYMBOL} con apalancamiento x{LEVERAGE}")
    while True:
        try:
            df = get_futures_klines(SYMBOL)
            df = calculate_indicators(df)
            signal, sl, tp = check_signals(df)
            if signal:
                print(f"Señal {signal} detectada. Abrir posición con TP/SL...")
                open_position_with_tp_sl(SYMBOL, signal, sl, tp)
            else:
                print("⏳ No hay señal clara en este ciclo")
        except Exception as e:
            print(f"⚠️ Error inesperado: {e}")
        time.sleep(SLEEP_SECONDS)
