# bot.py
import os
import time
import math
import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException

# ------------------------------
# Config desde Environment
# ------------------------------
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "ETHUSDT").upper()
LEVERAGE = int(os.getenv("LEVERAGE", 10))
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", 5))  # loop rápido para reaccionar a ticks

# Indicadores
ATR_LEN = int(os.getenv("ATR_LEN", 14))
ATR_MULT = float(os.getenv("ATR_MULT", 1.0))
SHORT_EMA = int(os.getenv("SHORT_EMA", 21))
LONG_EMA = int(os.getenv("LONG_EMA", 65))
RSI_FAST = int(os.getenv("RSI_FAST", 25))
RSI_SLOW = int(os.getenv("RSI_SLOW", 100))

# ------------------------------
# Cliente Binance Futures (real)
# ------------------------------
client = Client(API_KEY, API_SECRET)

# ------------------------------
# Helpers
# ------------------------------
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
    except Exception as e:
        print("Error al obtener klines:", e)
        return None

def get_symbol_rules(symbol):
    try:
        info = client.futures_exchange_info()
        for s in info.get('symbols', []):
            if s['symbol'] == symbol:
                step_size = tick_size = 0.0
                min_notional = min_qty = None
                for f in s.get('filters', []):
                    if f.get('filterType') == 'LOT_SIZE':
                        step_size = float(f.get('stepSize', 0.0))
                        min_qty = float(f.get('minQty', 0.0))
                    if f.get('filterType') == 'PRICE_FILTER':
                        tick_size = float(f.get('tickSize', 0.0))
                    if f.get('filterType') == 'MIN_NOTIONAL':
                        min_notional = float(f.get('notional', f.get('minNotional', 0.0)))
                return step_size or 0.00000001, tick_size or 0.00000001, min_notional or 5.0, min_qty or 0.0
    except Exception as e:
        print("Error al obtener symbol rules:", e)
    return 0.00000001, 0.00000001, 5.0, 0.0

def round_step(quantity, step):
    precision = max(0, int(round(-math.log10(step))))
    qty = math.floor(quantity / step) * step
    return round(qty, precision)

def round_price(price, tick):
    precision = max(0, int(round(-math.log10(tick))))
    return round(price, precision)

# ------------------------------
# Indicadores
# ------------------------------
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

    df['rsi_long'] = df['rsi_fast'] > df['rsi_slow']
    df['rsi_short'] = df['rsi_fast'] < df['rsi_slow']

    df['golden_long'] = (df['ema_short'] > df['ema_long']) & (df['ema_short'].shift(1) <= df['ema_long'].shift(1))
    df['golden_short'] = (df['ema_short'] < df['ema_long']) & (df['ema_short'].shift(1) >= df['ema_long'].shift(1))

    return df

# ------------------------------
# Señales
# ------------------------------
def check_signals(df):
    row = df.iloc[-1]
    side = None
    sl = None
    tp = None
    if row['open'] < row['lower_band'] and row['rsi_long']:
        side = 'LONG'
        sl = row['low'] - row['atr'] * 2
        tp = row['high'] + row['atr'] * 5
    elif row['open'] > row['upper_band'] and row['rsi_short']:
        side = 'SHORT'
        sl = row['high'] + row['atr'] * 2
        tp = row['low'] - row['atr'] * 5
    return side, sl, tp

# ------------------------------
# Posición actual
# ------------------------------
def get_current_position(symbol):
    try:
        pos = client.futures_position_information(symbol=symbol)
        for p in pos:
            if p['symbol'] == symbol:
                amt = float(p['positionAmt'])
                return amt
    except Exception as e:
        print("Error get_current_position:", e)
    return 0.0

# ------------------------------
# Cerrar opuesta
# ------------------------------
def close_opposite_if_needed(symbol, target_side):
    amt = get_current_position(symbol)
    if amt == 0:
        return True
    existing_side = 'LONG' if amt > 0 else 'SHORT'
    if existing_side == target_side:
        return True
    qty = abs(amt)
    try:
        side_for_close = 'SELL' if amt > 0 else 'BUY'
        client.futures_create_order(symbol=symbol, side=side_for_close, type='MARKET', quantity=qty, reduceOnly=True)
        print(f"Cerrada posición opuesta ({existing_side}) qty={qty}")
        time.sleep(1)
        return True
    except Exception as e:
        print("Error cerrando opuesta:", e)
        return False

# ------------------------------
# Calcular cantidad
# ------------------------------
def calculate_qty_full_balance(symbol, leverage):
    try:
        balances = client.futures_account_balance()
        usdt_balance = 0.0
        for b in balances:
            if b['asset'] == 'USDT':
                usdt_balance = float(b.get('balance', 0.0))
                break
        if usdt_balance <= 0:
            print("Balance USDT vacío.")
            return 0.0

        price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
        raw_notional = usdt_balance * leverage
        raw_qty = raw_notional / price
        step_size, tick_size, min_notional, min_qty = get_symbol_rules(symbol)
        qty = round_step(raw_qty, step_size)
        return qty
    except Exception as e:
        print("Error calculate_qty_full_balance:", e)
        return 0.0

# ------------------------------
# Abrir posición
# ------------------------------
def open_position_with_tp_sl(symbol, side, sl_price, tp_price):
    ok = close_opposite_if_needed(symbol, side)
    if not ok:
        print("No se cerró posición opuesta, abortando apertura.")
        return None

    qty = calculate_qty_full_balance(symbol, LEVERAGE)
    if qty <= 0:
        print("Qty inválida, abortando.")
        return None

    try:
        order = client.futures_create_order(symbol=symbol, side='BUY' if side=='LONG' else 'SELL', type='MARKET', quantity=qty)
        print(f"Abrida posición {side} qty={qty} orderId={order.get('orderId')}")

        step_size, tick_size, _, _ = get_symbol_rules(symbol)
        slp = round_price(sl_price, tick_size)
        tpp = round_price(tp_price, tick_size)

        try:
            client.futures_create_order(symbol=symbol, side='SELL' if side=='LONG' else 'BUY',
                                        type='STOP_MARKET', stopPrice=slp, reduceOnly=True, quantity=qty)
            print(f"SL colocado en {slp}")
        except Exception as e:
            print("No se pudo colocar SL:", e)

        try:
            client.futures_create_order(symbol=symbol, side='SELL' if side=='LONG' else 'BUY',
                                        type='TAKE_PROFIT_MARKET', stopPrice=tpp, reduceOnly=True, quantity=qty)
            print(f"TP colocado en {tpp}")
        except Exception as e:
            print("No se pudo colocar TP:", e)

        return order
    except Exception as e:
        print("Error abriendo posición market:", e)
        return None

# ------------------------------
# Main
# ------------------------------
if __name__ == "__main__":
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        print(f"Apalancamiento establecido: x{LEVERAGE} para {SYMBOL}")
    except Exception as e:
        print("Warning: no se pudo establecer apalancamiento:", e)

    print("Bot iniciado — MERCADO REAL (Futures). Símbolo:", SYMBOL)

    while True:
        df = get_futures_klines(SYMBOL, interval='1m', limit=200)
        if df is None:
            print("No se obtuvieron velas. Reintentando en", SLEEP_SECONDS, "s")
            time.sleep(SLEEP_SECONDS)
            continue

        df = calculate_indicators(df)
        signal, sl, tp = check_signals(df)

        if signal == 'LONG':
            print("Señal LONG detectada. Intentando abrir posición con TP/SL...")
            open_position_with_tp_sl(SYMBOL, 'LONG', sl, tp)
        elif signal == 'SHORT':
            print("Señal SHORT detectada. Intentando abrir posición con TP/SL...")
            open_position_with_tp_sl(SYMBOL, 'SHORT', sl, tp)
        else:
            print("No hay señales en este ciclo")

        time.sleep(SLEEP_SECONDS)


