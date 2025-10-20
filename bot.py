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
SYMBOL = os.getenv("SYMBOL", "BNBUSDT").upper()
LEVERAGE = int(os.getenv("LEVERAGE", 10))
SLEEP_SECONDS = float(os.getenv("SLEEP_SECONDS", 1.5))  # loop rápido para scalping

# Indicadores configurables
EMA_FAST = int(os.getenv("EMA_FAST", 8))
EMA_SLOW = int(os.getenv("EMA_SLOW", 21))
RSI_LEN = int(os.getenv("RSI_LEN", 5))
ATR_LEN = int(os.getenv("ATR_LEN", 7))
ATR_MULT_SL = float(os.getenv("ATR_MULT_SL", 0.5))
ATR_MULT_TP = float(os.getenv("ATR_MULT_TP", 1.2))

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

def calculate_qty_full_balance_safe(symbol, leverage):
    balances = client.futures_account_balance()
    usdt_balance = next((float(b['balance']) for b in balances if b['asset'] == 'USDT'), 0.0)
    if usdt_balance <= 0:
        print("⚠️ No hay USDT disponible.")
        return 0.0

    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    step_size, tick_size, min_notional, min_qty = get_symbol_rules(symbol)

    # Usar todo el saldo disponible ajustado por un 1% de margen de seguridad
    raw_qty = (usdt_balance * leverage * 0.99) / price

    # Validar mínimo notional
    if raw_qty * price < min_notional:
        print(f"⚠️ Qty calculada menor al mínimo notional ({min_notional})")
        return 0.0

    qty = max(round_step(raw_qty, step_size), min_qty)
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
# CALCULO INDICADORES
# ==============================
def calculate_indicators(df):
    df = df.copy()
    df['ema_fast'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()
    delta = df['close'].diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.rolling(RSI_LEN).mean()
    roll_down = down.rolling(RSI_LEN).mean()
    df['rsi'] = 100 - (100 / (1 + roll_up / roll_down))
    df['hl'] = df['high'] - df['low']
    df['hc'] = (df['high'] - df['close'].shift(1)).abs()
    df['lc'] = (df['low'] - df['close'].shift(1)).abs()
    df['tr'] = df[['hl','hc','lc']].max(axis=1)
    df['atr'] = df['tr'].rolling(ATR_LEN).mean()
    return df

# ==============================
# LOGICA DE SEÑALES
# ==============================
def check_signal(df):
    row = df.iloc[-1]
    signal = None
    if row['ema_fast'] > row['ema_slow'] and row['rsi'] > 50:
        signal = 'LONG'
    elif row['ema_fast'] < row['ema_slow'] and row['rsi'] < 50:
        signal = 'SHORT'
    return signal

# ==============================
# POSICIONES
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
    side_for_close = SIDE_SELL if amt > 0 else SIDE_BUY
    qty = abs(amt)
    client.futures_create_order(symbol=symbol, side=side_for_close, type='MARKET', quantity=qty, reduceOnly=True)
    time.sleep(0.5)
    return True

def open_position(symbol, side, qty, atr):
    ok = close_opposite_if_needed(symbol, side)
    if not ok or qty <= 0:
        return None
    order = client.futures_create_order(
        symbol=symbol,
        side=SIDE_BUY if side=='LONG' else SIDE_SELL,
        type=FUTURE_ORDER_TYPE_MARKET,
        quantity=qty
    )
    step_size, tick_size, _, _ = get_symbol_rules(symbol)
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    sl_price = price - ATR_MULT_SL * atr if side=='LONG' else price + ATR_MULT_SL * atr
    tp_price = price + ATR_MULT_TP * atr if side=='LONG' else price - ATR_MULT_TP * atr
    sl_price = round_price(sl_price, tick_size)
    tp_price = round_price(tp_price, tick_size)
    try:
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL if side=='LONG' else SIDE_BUY,
            type='STOP_MARKET',
            stopPrice=sl_price,
            closePosition=True
        )
    except Exception as e:
        print("⚠️ Error SL:", e)
    try:
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL if side=='LONG' else SIDE_BUY,
            type='TAKE_PROFIT_MARKET',
            stopPrice=tp_price,
            closePosition=True
        )
    except Exception as e:
        print("⚠️ Error TP:", e)
    return order

# ==============================
# LOOP PRINCIPAL
# ==============================
if __name__ == "__main__":
    client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
    print(f"🚀 Bot scalping iniciado para {SYMBOL} con x{LEVERAGE}")
    while True:
        try:
            df = get_futures_klines(SYMBOL, interval='1m', limit=50)
            df = calculate_indicators(df)
            signal = check_signal(df)
            if signal:
                atr = df['atr'].iloc[-1]
                qty = calculate_qty_full_balance_safe(SYMBOL, LEVERAGE)
                print(f"Señal {signal} detectada. Abrir posición qty={qty}")
                open_position(SYMBOL, signal, qty, atr)
            else:
                print("⏳ Sin señal clara")
        except Exception as e:
            print("⚠️ Error:", e)
        time.sleep(SLEEP_SECONDS)


