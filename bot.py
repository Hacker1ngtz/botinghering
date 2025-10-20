import os
import time
import math
import pandas as pd
from binance.client import Client
from binance.enums import *

# ==============================
# CONFIGURACIÓN DESDE VARIABLES DE ENTORNO
# ==============================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "BNBUSDT").upper()
LEVERAGE = int(os.getenv("LEVERAGE", 10))
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", 60))

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
    min_qty = float(next(f['minQty'] for f in s_info['filters'] if f['filterType'] == 'LOT_SIZE'))
    return step_size, tick_size, min_qty

def calculate_qty(symbol, leverage, risk_pct=0.05):
    balances = client.futures_account_balance()
    usdt_balance = next((float(b['balance']) for b in balances if b['asset'] == 'USDT'), 0.0)
    if usdt_balance <= 0:
        print("⚠️ Sin balance USDT.")
        return 0.0
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    step_size, tick_size, min_qty = get_symbol_rules(symbol)
    risk_balance = usdt_balance * risk_pct
    raw_qty = (risk_balance * leverage) / price
    qty = max(round_step(raw_qty, step_size), min_qty)
    return qty

# ==============================
# INDICADORES
# ==============================
def get_futures_klines(symbol, interval="1m", limit=200):
    klines = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=[
        'open_time','open','high','low','close','volume','close_time',
        'quote_asset_volume','num_trades','taker_buy_base','taker_buy_quote','ignore'
    ])
    for c in ['open','high','low','close','volume']:
        df[c] = df[c].astype(float)
    return df

def calculate_indicators(df):
    df['ema_fast'] = df['close'].ewm(span=5, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=13, adjust=False).mean()
    delta = df['close'].diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.rolling(14).mean()
    roll_down = down.rolling(14).mean()
    df['rsi'] = 100 - (100 / (1 + roll_up / roll_down))
    return df

def get_signal(df):
    if len(df) < 20:
        return None
    last = df.iloc[-1]
    prev = df.iloc[-2]
    # BUY
    if prev['ema_fast'] < prev['ema_slow'] and last['ema_fast'] > last['ema_slow'] and last['rsi'] > 55:
        return "BUY"
    # SELL
    if prev['ema_fast'] > prev['ema_slow'] and last['ema_fast'] < last['ema_slow'] and last['rsi'] < 45:
        return "SELL"
    return None

# ==============================
# POSICIONES
# ==============================
def get_position(symbol):
    positions = client.futures_position_information(symbol=symbol)
    pos = next((float(p['positionAmt']) for p in positions if p['symbol'] == symbol), 0.0)
    return pos

def close_all(symbol):
    pos = get_position(symbol)
    if pos == 0:
        return
    side = SIDE_SELL if pos > 0 else SIDE_BUY
    qty = abs(pos)
    client.futures_create_order(symbol=symbol, side=side, type=FUTURE_ORDER_TYPE_MARKET, quantity=qty, reduceOnly=True)

# ==============================
# ENTRADA CON TP/SL
# ==============================
def open_position(symbol, side):
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    step_size, tick_size, _ = get_symbol_rules(symbol)
    qty = calculate_qty(symbol, LEVERAGE)
    if qty <= 0:
        print("⚠️ Cantidad insuficiente, no se abre posición.")
        return

    client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
    close_all(symbol)

    entry_side = SIDE_BUY if side == "BUY" else SIDE_SELL
    exit_side = SIDE_SELL if side == "BUY" else SIDE_BUY
    order = client.futures_create_order(
        symbol=symbol,
        side=entry_side,
        type=FUTURE_ORDER_TYPE_MARKET,
        quantity=qty
    )

    # TP / SL
    if side == "BUY":
        tp = price * 1.015
        sl = price * 0.993
    else:
        tp = price * 0.985
        sl = price * 1.007

    tp = round_price(tp, tick_size)
    sl = round_price(sl, tick_size)

    try:
        client.futures_create_order(
            symbol=symbol, side=exit_side,
            type=FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
            stopPrice=tp, reduceOnly=True, quantity=qty
        )
        client.futures_create_order(
            symbol=symbol, side=exit_side,
            type=FUTURE_ORDER_TYPE_STOP_MARKET,
            stopPrice=sl, reduceOnly=True, quantity=qty
        )
        print(f"✅ {side} ejecutada | TP: {tp} | SL: {sl}")
    except Exception as e:
        print(f"⚠️ Error al colocar TP/SL: {e}")

# ==============================
# LOOP PRINCIPAL
# ==============================
if __name__ == "__main__":
    print(f"🚀 Bot iniciado para {SYMBOL} con apalancamiento x{LEVERAGE}")
    while True:
        try:
            df = get_futures_klines(SYMBOL)
            df = calculate_indicators(df)
            signal = get_signal(df)
            if signal:
                print(f"📊 Señal detectada: {signal}")
                open_position(SYMBOL, signal)
            else:
                print("⏳ Esperando señal...")
            time.sleep(SLEEP_SECONDS)
        except Exception as e:
            print(f"⚠️ Error en el loop principal: {e}")
            time.sleep(10)
