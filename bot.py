"""
Bot de trading (Binance Futures) con websocket de kline para reaccionar en tiempo real.
- Reacciona ante la vela en formación (kline "is closed" = False) para entrar más rápido.
- Cachea reglas del símbolo.
- Usa un cooldown/debounce para evitar entradas repetidas en la misma señal.
- Recomiendo probar primero en TESTNET (set TESTNET=1 en env) antes de ir LIVE.
"""

import os
import time
import math
import threading
import pandas as pd
from datetime import datetime, timedelta, timezone
from binance.client import Client
from binance.enums import *
from binance import ThreadedWebsocketManager

# ==============================
# CONFIGURACIÓN DESDE VARIABLES DE ENTORNO
# ==============================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "BNBUSDT").upper()
LEVERAGE = int(os.getenv("LEVERAGE", 10))
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", 1))   # loop de respaldo (no usado para decidir señal)
TESTNET = os.getenv("TESTNET", "0") == "1"           # si es "1", usar TESTNET
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", 20))    # evitar reentrar inmediatamente
MIN_INTERVAL_BETWEEN_SIGNALS = COOLDOWN_SEC

# Indicadores
ATR_LEN = int(os.getenv("ATR_LEN", 14))
ATR_MULT = float(os.getenv("ATR_MULT", 1.0))
SHORT_EMA = int(os.getenv("SHORT_EMA", 21))
LONG_EMA = int(os.getenv("LONG_EMA", 65))
RSI_FAST = int(os.getenv("RSI_FAST", 25))
RSI_SLOW = int(os.getenv("RSI_SLOW", 100))

# ==============================
# VALIDACIONES INICIALES
# ==============================
if not API_KEY or not API_SECRET:
    raise SystemExit("Faltan BINANCE_API_KEY / BINANCE_API_SECRET en variables de entorno.")

# ==============================
# CLIENTE BINANCE (FUTURES) - TESTNET OPCIONAL
# ==============================
if TESTNET:
    client = Client(API_KEY, API_SECRET, testnet=True)
else:
    client = Client(API_KEY, API_SECRET)

# ==============================
# GLOBALS / CACHE
# ==============================
klines_df = None                 # DataFrame con velas recientes
klines_lock = threading.Lock()   # bloqueo para acceso seguro al df
last_signal_time = None          # timestamp de última ejecución de señal
last_signal_side = None          # lado de última señal para evitar duplicados
step_size = None
tick_size = None
min_notional = None
min_qty = None

# ==============================
# UTILIDADES NUMÉRICAS
# ==============================
def round_step(quantity, step):
    """Round down to step with correct precision."""
    precision = max(0, int(round(-math.log10(step))))
    qty = math.floor(quantity / step) * step
    return round(qty, precision)

def round_price(price, tick):
    precision = max(0, int(round(-math.log10(tick))))
    return round(price, precision)

# ==============================
# REGLAS DEL SIMBOLO (CACHE)
# ==============================
def load_symbol_rules(symbol):
    global step_size, tick_size, min_notional, min_qty
    info = client.futures_exchange_info()
    s_info = next((s for s in info['symbols'] if s['symbol'] == symbol), None)
    if not s_info:
        raise SystemExit(f"Símbolo {symbol} no encontrado en exchange info.")
    step_size = float(next(f['stepSize'] for f in s_info['filters'] if f['filterType'] == 'LOT_SIZE'))
    tick_size = float(next(f['tickSize'] for f in s_info['filters'] if f['filterType'] == 'PRICE_FILTER'))
    min_notional = float(next((f.get('minNotional', 5.0) for f in s_info['filters'] if f['filterType'] == 'MIN_NOTIONAL'), 5.0))
    min_qty = float(next(f['minQty'] for f in s_info['filters'] if f['filterType'] == 'LOT_SIZE'))
    print(f"Cached rules: step_size={step_size}, tick_size={tick_size}, min_qty={min_qty}, min_notional={min_notional}")

# ==============================
# OBTENER CANTIDAD SEGURA
# ==============================
def get_usdt_balance():
    balances = client.futures_account_balance()
    usdt_balance = next((float(b['balance']) for b in balances if b['asset'] == 'USDT'), 0.0)
    return usdt_balance

def calculate_qty_from_balance(symbol, leverage, usdt_balance, step_size, min_qty):
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    raw_qty = (usdt_balance * leverage) / price
    qty = max(round_step(raw_qty, step_size), min_qty)
    return qty

# ==============================
# KLINES (Websocket callback)
# ==============================
def kline_to_row(k):
    open_time = int(k['t'])
    open_p = float(k['o'])
    high_p = float(k['h'])
    low_p = float(k['l'])
    close_p = float(k['c'])
    volume = float(k['v'])
    close_time = int(k['T'])
    is_closed = bool(k['x'])
    return {
        'open_time': open_time,
        'open': open_p,
        'high': high_p,
        'low': low_p,
        'close': close_p,
        'volume': volume,
        'close_time': close_time,
        'is_closed': is_closed
    }

def kline_callback(msg):
    global klines_df, last_signal_time, last_signal_side
    try:
        k = msg['k']
        row = kline_to_row(k)
        with klines_lock:
            if klines_df is None:
                df_hist = get_futures_klines(SYMBOL, interval='1m', limit=200)
                klines_df = df_hist
            last_open_time = klines_df.iloc[-1]['open_time']
            if row['open_time'] == last_open_time:
                klines_df.at[klines_df.index[-1], 'open'] = row['open']
                klines_df.at[klines_df.index[-1], 'high'] = max(klines_df.iloc[-1]['high'], row['high'])
                klines_df.at[klines_df.index[-1], 'low'] = min(klines_df.iloc[-1]['low'], row['low'])
                klines_df.at[klines_df.index[-1], 'close'] = row['close']
                klines_df.at[klines_df.index[-1], 'volume'] = row['volume']
            else:
                new_row = {
                    'open_time': row['open_time'],
                    'open': row['open'],
                    'high': row['high'],
                    'low': row['low'],
                    'close': row['close'],
                    'volume': row['volume'],
                    'close_time': row['close_time'],
                    'quote_asset_volume': 0.0,
                    'num_trades': 0,
                    'taker_buy_base': 0.0,
                    'taker_buy_quote': 0.0,
                    'ignore': 0
                }
                klines_df = pd.concat([klines_df.iloc[1:].reset_index(drop=True), pd.DataFrame([new_row])], ignore_index=True)
            df_copy = klines_df.copy()
        df_ind = calculate_indicators(df_copy)
        signal, sl, tp = check_signals(df_ind)

        now = datetime.now(timezone.utc)  # <-- actualizado
        if signal:
            if last_signal_time and (now - last_signal_time).total_seconds() < MIN_INTERVAL_BETWEEN_SIGNALS:
                if last_signal_side == signal:
                    print(f"[{now.isoformat()}] Señal {signal} detectada pero en cooldown ({MIN_INTERVAL_BETWEEN_SIGNALS}s). Ignorando.")
                else:
                    print(f"[{now.isoformat()}] Señal contraria detectada dentro de cooldown: {signal}. Permitida.")
                    execute_signal_if_safe(signal, sl, tp)
                    last_signal_time = now
                    last_signal_side = signal
            else:
                print(f"[{now.isoformat()}] Señal {signal} detectada. Ejecutando...")
                execute_signal_if_safe(signal, sl, tp)
                last_signal_time = now
                last_signal_side = signal

    except Exception as e:
        print(f"⚠️ Error en kline_callback: {e}")

# ==============================
# FUNCIONES DE INDICADORES Y SEÑALES
# ==============================
def get_futures_klines(symbol, interval='1m', limit=200):
    klines = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=[
        'open_time','open','high','low','close','volume','close_time',
        'quote_asset_volume','num_trades','taker_buy_base','taker_buy_quote','ignore'
    ])
    for c in ['open','high','low','close','volume']:
        df[c] = df[c].astype(float)
    return df

def calculate_indicators(df):
    df = df.copy()
    df['hl'] = df['high'] - df['low']
    df['hc'] = (df['high'] - df['close'].shift(1)).abs()
    df['lc'] = (df['low'] - df['close'].shift(1)).abs()
    df['tr'] = df[['hl','hc','lc']].max(axis=1)
    df['atr'] = df['tr'].rolling(ATR_LEN).mean()
    df['upper_band'] = df['close'] + df['atr'] * ATR_MULT
    df['lower_band'] = df['close'] - df['atr'] * ATR_MULT
    df['ema_short'] = df['close'].ewm(span=SHORT_EMA, adjust=False).mean()
    df['ema_long'] = df['close'].ewm(span=LONG_EMA, adjust=False).mean()
    delta = df['close'].diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up_fast = up.rolling(RSI_FAST).mean()
    roll_down_fast = down.rolling(RSI_FAST).mean()
    df['rsi_fast'] = 100 - (100 / (1 + roll_up_fast / roll_down_fast))
    roll_up_slow = up.rolling(RSI_SLOW).mean()
    roll_down_slow = down.rolling(RSI_SLOW).mean()
    df['rsi_slow'] = 100 - (100 / (1 + roll_up_slow / roll_down_slow))
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
# EJECUCIÓN DE ORDENES (SAFE)
# ==============================
def get_current_position_amount(symbol):
    pos = client.futures_position_information(symbol=symbol)
    for p in pos:
        if p['symbol'] == symbol:
            return float(p['positionAmt'])
    return 0.0

def close_opposite_if_needed_sync(symbol, target_side):
    amt = get_current_position_amount(symbol)
    if amt == 0:
        return True
    existing_side = 'LONG' if amt > 0 else 'SHORT'
    if existing_side == target_side:
        return True
    qty = abs(amt)
    side_for_close = SIDE_SELL if amt > 0 else SIDE_BUY
    try:
        client.futures_create_order(symbol=symbol, side=side_for_close, type='MARKET', quantity=qty, reduceOnly=True)
        time.sleep(0.3)
        return True
    except Exception as e:
        print(f"Error cerrando posición opuesta: {e}")
        return False

def execute_signal_if_safe(side, sl_price, tp_price):
    try:
        ok = close_opposite_if_needed_sync(SYMBOL, side)
        if not ok:
            print("No se pudo cerrar posición opuesta. Abortando ejecución de señal.")
            return None

        usdt_balance = get_usdt_balance()
        if usdt_balance <= 0:
            print("⚠️ No hay saldo USDT suficiente.")
            return None

        qty = calculate_qty_from_balance(SYMBOL, LEVERAGE, usdt_balance, step_size, min_qty)
        if qty <= 0:
            print("⚠️ Qty calculada inválida (<=0).")
            return None

        side_enum = SIDE_BUY if side == 'LONG' else SIDE_SELL
        order = client.futures_create_order(symbol=SYMBOL, side=side_enum, type=FUTURE_ORDER_TYPE_MARKET, quantity=qty)
        print(f"Order placed: side={side}, qty={qty}, orderId={order.get('orderId')}")

        slp = round_price(sl_price, tick_size)
        tpp = round_price(tp_price, tick_size)
        try:
            client.futures_create_order(symbol=SYMBOL,
                                        side=SIDE_SELL if side=='LONG' else SIDE_BUY,
                                        type='STOP_MARKET',
                                        stopPrice=slp,
                                        closePosition=False,
                                        reduceOnly=True,
                                        quantity=qty)
        except Exception as e:
            print(f"No se pudo colocar SL: {e}")
        try:
            client.futures_create_order(symbol=SYMBOL,
                                        side=SIDE_SELL if side=='LONG' else SIDE_BUY,
                                        type='TAKE_PROFIT_MARKET',
                                        stopPrice=tpp,
                                        closePosition=False,
                                        reduceOnly=True,
                                        quantity=qty)
        except Exception as e:
            print(f"No se pudo colocar TP: {e}")

        return order
    except Exception as e:
        print(f"⚠️ Error ejecutando señal: {e}")
        return None

# ==============================
# WEBSOCKET Y MAIN
# ==============================
def start_kline_ws(symbol, interval='1m'):
    twm = ThreadedWebsocketManager(api_key=API_KEY, api_secret=API_SECRET)
    twm.start()
    print("Websocket manager started.")
    twm.start_kline_socket(callback=kline_callback, symbol=symbol, interval=interval)
    return twm

def main():
    load_symbol_rules(SYMBOL)

    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        print(f"Apalancamiento seteado a x{LEVERAGE} para {SYMBOL}")
    except Exception as e:
        print(f"Warning: no se pudo setear apalancamiento: {e}")

    global klines_df
    klines_df = get_futures_klines(SYMBOL, interval='1m', limit=200)

    twm = start_kline_ws(SYMBOL, interval='1m')

    print(f"🚀 Bot ON — {SYMBOL} — Testnet={TESTNET} — Cooldown={MIN_INTERVAL_BETWEEN_SIGNALS}s")
    try:
        while True:
            time.sleep(SLEEP_SECONDS)
            now = datetime.now(timezone.utc)  # <-- actualizado
            if last_signal_time:
                dt = (now - last_signal_time).total_seconds()
            else:
                dt = None
            print(f"[{now.isoformat()}] Heartbeat. LastSignal={last_signal_side} dt={dt}")
    except KeyboardInterrupt:
        print("Deteniendo bot y websocket...")
        twm.stop()
        print("Stopped.")

if __name__ == "__main__":
    main()

