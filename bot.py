import os
import time
import math
import pandas as pd
from binance.client import Client
from binance.enums import *

# ==============================
# CONFIGURACIÓN
# ==============================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "BNBUSDT").upper()
LEVERAGE = int(os.getenv("LEVERAGE", 10))
SLEEP_SECONDS = float(os.getenv("SLEEP_SECONDS", 1.5))

# Indicadores
EMA_FAST = int(os.getenv("EMA_FAST", 8))
EMA_SLOW = int(os.getenv("EMA_SLOW", 21))
RSI_LEN = int(os.getenv("RSI_LEN", 5))
MACD_FAST = int(os.getenv("MACD_FAST", 12))
MACD_SLOW = int(os.getenv("MACD_SLOW", 26))
MACD_SIGNAL = int(os.getenv("MACD_SIGNAL", 9))
ATR_LEN = int(os.getenv("ATR_LEN", 7))

# Gestión de riesgo
ATR_MULT_SL = float(os.getenv("ATR_MULT_SL", 1.0))
TRAILING_SL_STEP = float(os.getenv("TRAILING_SL_STEP", 0.5))  # % ATR
USDT_USAGE_FACTOR = float(os.getenv("USDT_USAGE_FACTOR", 0.5))

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
# INDICADORES
# ==============================
def calculate_indicators(df):
    df = df.copy()
    df['ema_fast'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()
    # RSI
    delta = df['close'].diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.rolling(RSI_LEN).mean()
    roll_down = down.rolling(RSI_LEN).mean()
    df['rsi'] = 100 - (100 / (1 + roll_up / roll_down))
    # MACD
    ema_fast_macd = df['close'].ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow_macd = df['close'].ewm(span=MACD_SLOW, adjust=False).mean()
    df['macd'] = ema_fast_macd - ema_slow_macd
    df['macd_signal'] = df['macd'].ewm(span=MACD_SIGNAL, adjust=False).mean()
    # ATR
    df['hl'] = df['high'] - df['low']
    df['hc'] = (df['high'] - df['close'].shift(1)).abs()
    df['lc'] = (df['low'] - df['close'].shift(1)).abs()
    df['tr'] = df[['hl','hc','lc']].max(axis=1)
    df['atr'] = df['tr'].rolling(ATR_LEN).mean()
    return df

# ==============================
# SEÑALES CON PONDERACIÓN
# ==============================
def get_best_signal(df):
    row = df.iloc[-1]
    long_score = 0
    short_score = 0
    # EMA
    if row['ema_fast'] > row['ema_slow']: long_score += 1
    else: short_score += 1
    # RSI
    if row['rsi'] > 50: long_score +=1
    else: short_score +=1
    # MACD
    if row['macd'] > row['macd_signal']: long_score +=1
    else: short_score +=1
    # Resultado
    if long_score > short_score:
        return 'LONG'
    elif short_score > long_score:
        return 'SHORT'
    return None

# ==============================
# POSICIONES
# ==============================
def cancel_all_open_orders(symbol):
    try:
        orders = client.futures_get_open_orders(symbol=symbol)
        for o in orders:
            client.futures_cancel_order(symbol=symbol, orderId=o['orderId'])
        if orders:
            print(f"🧹 Canceladas {len(orders)} órdenes abiertas previas")
    except Exception as e:
        print(f"⚠️ Error cancelando órdenes: {e}")

def get_current_position(symbol):
    pos = client.futures_position_information(symbol=symbol)
    for p in pos:
        if p['symbol'] == symbol:
            return float(p['positionAmt'])
    return 0.0

def is_position_open(symbol):
    return abs(get_current_position(symbol)) > 0

def calculate_qty(symbol, leverage):
    balances = client.futures_account_balance()
    usdt_balance = next((float(b['balance']) for b in balances if b['asset'] == 'USDT'), 0.0)
    if usdt_balance <= 0: return 0.0
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    step_size, tick_size, min_notional, min_qty = get_symbol_rules(symbol)
    raw_qty = (usdt_balance * leverage * USDT_USAGE_FACTOR) / price
    if raw_qty * price < min_notional:
        raw_qty = min_notional / price
    return max(round_step(raw_qty, step_size), min_qty)

def manage_trailing_stop(symbol, side, atr):
    pos_amt = get_current_position(symbol)
    if pos_amt == 0: return
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    step_size, tick_size, _, _ = get_symbol_rules(symbol)
    if side == 'LONG':
        new_sl = round_price(price - TRAILING_SL_STEP*atr, tick_size)
        try:
            client.futures_create_order(symbol=symbol, side=SIDE_SELL,
                                        type='STOP_MARKET', stopPrice=new_sl, closePosition=True)
        except: pass
    elif side == 'SHORT':
        new_sl = round_price(price + TRAILING_SL_STEP*atr, tick_size)
        try:
            client.futures_create_order(symbol=symbol, side=SIDE_BUY,
                                        type='STOP_MARKET', stopPrice=new_sl, closePosition=True)
        except: pass

def open_position(symbol, side, qty, atr):
    if is_position_open(symbol):
        print("⚠️ Ya hay posición abierta")
        return None
    cancel_all_open_orders(symbol)
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    step_size, tick_size, _, _ = get_symbol_rules(symbol)
    sl_price = price - ATR_MULT_SL*atr if side=='LONG' else price + ATR_MULT_SL*atr
    min_distance = tick_size*2
    if side=='LONG': sl_price = max(price - ATR_MULT_SL*atr, price - min_distance)
    else: sl_price = min(price + ATR_MULT_SL*atr, price + min_distance)
    sl_price = round_price(sl_price, tick_size)
    try:
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_BUY if side=='LONG' else SIDE_SELL,
            type=FUTURE_ORDER_TYPE_MARKET,
            quantity=qty
        )
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL if side=='LONG' else SIDE_BUY,
            type='STOP_MARKET',
            stopPrice=sl_price,
            closePosition=True
        )
        print(f"✅ Posición {side} abierta. SL={sl_price}, qty={qty}")
        return side
    except Exception as e:
        print("⚠️ Error abriendo posición:", e)
        return None

# ==============================
# LOOP PRINCIPAL
# ==============================
if __name__ == "__main__":
    client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
    print(f"🚀 Bot scalping avanzado listo para {SYMBOL} x{LEVERAGE}")
    active_side = None

    while True:
        try:
            df = get_futures_klines(SYMBOL, interval='1m', limit=50)
            df = calculate_indicators(df)
            atr = df['atr'].iloc[-1]

            if is_position_open(SYMBOL) and active_side:
                manage_trailing_stop(SYMBOL, active_side, atr)
            else:
                signal = get_best_signal(df)
                if signal:
                    qty = calculate_qty(SYMBOL, LEVERAGE)
                    active_side = open_position(SYMBOL, signal, qty, atr)

        except Exception as e:
            print(f"⚠️ Error en loop: {e}")
        time.sleep(SLEEP_SECONDS)


